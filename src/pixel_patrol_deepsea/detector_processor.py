"""Runs a FathomNet detector over slices, when the user has set one up.

Unlike every other processor here this one carries a model, and it is optional:
`register_processor_plugins` only offers it once the weights and the YOLOv5 code
are in place. See `detector` for why none of that is a declared dependency.

Sampling is deliberately sparse. Against MBARI's annotated sequences an animal
stays in frame for a median of 182 frames in midwater and 290 on the bottom, so
reading one frame per slice sees 96-99% of the distinct animals a full read would,
for a thirtieth of the work. Refining that further finds mostly tracks that were
annotated for one or two frames.
"""

import base64
import json
import logging
import os
from typing import Any, Dict, List, Tuple

import numpy as np

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec
from pixel_patrol_base.plugins.processors.raster_processor import (
    RasterMetricSpec,
    _scalar_rows_agg,
    _weighted_mean_agg,
)
from pixel_patrol_deepsea import detector

logger = logging.getLogger(__name__)

DETECTION_COUNT      = "detection_count"
DETECTION_CONFIDENCE = "detection_confidence"
DETECTION_TOP_CLASS  = "detection_top_class"
DETECTION_CROP       = "detection_crop"
DETECTIONS           = "detections"

CROP_WIDTH = 192

# One frame per slice, and the whole budget spent on reading that frame properly.
# The detector is run at three sizes and their answers fused, which is three passes;
# doing that to three frames of the same slice would be nine, for three near-identical
# pictures of the same animal a tenth of a second apart. The animation does not need
# them either - a clip is made by cropping consecutive frames around the animal as
# it is tracked through them, and cropping costs nothing. See `detector.detect_fused`
# and `_follow`.
FRAMES_PER_SLICE = int(os.environ.get("PIXEL_PATROL_DETECTOR_FRAMES", "1"))
# What costs room in the table is the pictures, not the boxes: a crop is around five
# kilobytes of base64 and a box is sixty bytes. So every animal is written down, and
# only the most convincing ones are given a close-up. Capping the detections instead
# used to cap what the pipeline could be shown to find - a seabed with fifty animals
# on it reported eight, and scored against annotated footage as if it had missed the
# other forty-two.
#
# Per *look*, not per slice. A slice is now read at several moments rather than one,
# and a seabed with fifty animals in every frame would otherwise spend a cap meant
# for one frame across five - losing a fifth of the boxes, from the bottom of the
# confidence order, which is exactly where recall lives.
MOST_PER_LOOK = int(os.environ.get("PIXEL_PATROL_DETECTOR_MOST", "200"))
CROPS_PER_SLICE = int(os.environ.get("PIXEL_PATROL_DETECTOR_CROPS", "8"))
# Consecutive frames cropped around the animal, so the animation is smooth and
# there is a moment either side of the detection. Cropping is free - only inference
# costs - so this buys movement for nothing, and the box is tracked through the run
# rather than held still, which is what keeps a swimming animal in its own tile.
CLIP_FRAMES = int(os.environ.get("PIXEL_PATROL_DETECTOR_CLIP", "10"))
# Wide enough to see what the animal is. Every clip frame is a JPEG in the table, so
# this is the one setting here that costs real room: ten frames of six animals a
# slice at this width is about a quarter of a megabyte per slice looked at.
CLIP_WIDTH = int(os.environ.get("PIXEL_PATROL_DETECTOR_CLIP_WIDTH", "160"))
# How many separate animals in one slice get a clip of their own. A seabed carpeted
# with sea pens is a real thing, and one clip per slice meant every one of them was
# shown the same animation.
CLIPS_PER_SLICE = int(os.environ.get("PIXEL_PATROL_DETECTOR_CLIPS", "6"))
# Two boxes in one slice overlapping this much are one animal seen twice, not two.
SAME_ANIMAL_IOU = 0.3

# How many seconds of footage between looks. Measured against DeepSea-MOT, counting
# distinct animals rather than boxes, at the confidence floor a report writes:
#
#   0.08 s apart   0.87 of the animals
#   0.30 s         0.83
#   0.60 s         0.83
#   0.90 s         0.79
#   1.50 s         0.76
#   3.75 s         0.63
#
# The knee is around half a second and everything above it is flat - four times the
# frames buys four points. Precision is 0.96-0.97 at every one of these, so this
# trades recall against compute and nothing else: a frame nobody reads cannot
# produce a false positive.
DETECT_EVERY_SECONDS = float(os.environ.get("PIXEL_PATROL_DETECTOR_EVERY", "1"))


def _best_crop_agg(spec: RasterMetricSpec, rows: List[Dict]) -> Any:
    """Keep the crop belonging to the most confident detection in the group."""
    best, crop = -1.0, None
    for row in rows:
        confidence = float(row.get(DETECTION_CONFIDENCE, 0) or 0)
        if row.get(spec.name) is not None and confidence > best:
            best, crop = confidence, row[spec.name]
    return crop


def _detections_agg(spec: RasterMetricSpec, rows: List[Dict]) -> Any:
    """Carry the animal list up from whichever leaf was most confident.

    Every row in the table is an aggregation over leaf rows - the per-slice rows
    included, since a rollup groups leaves by the dims it fixes rather than
    chaining level to level. So returning None here does not mean "null above the
    slice", it means null everywhere, and an all-null column is dropped outright.
    Same rule as the crop beside it: the most convincing leaf wins.
    """
    best, animals = -1.0, None
    for row in rows:
        confidence = float(row.get(DETECTION_CONFIDENCE, 0) or 0)
        if row.get(spec.name) and confidence > best:
            best, animals = confidence, row[spec.name]
    return animals


def _top_class_agg(spec: RasterMetricSpec, rows: List[Dict]) -> Any:
    """Carry up the label from whichever row was most confident.

    A plain mean is meaningless for a taxon, and the most confident sighting in a
    stretch is the one a person would want named.
    """
    best, label = -1.0, None
    for row in rows:
        confidence = float(row.get(DETECTION_CONFIDENCE, 0) or 0)
        if spec.name in row and row[spec.name] and confidence > best:
            best, label = confidence, row[spec.name]
    return label


class _AtBox:
    """Just enough of a detection for crop_of, which only wants the box."""

    def __init__(self, box):
        self.box = tuple(int(v) for v in box)


# How far the animal is allowed to have moved between one frame and the next,
# as a fraction of its own size. Generous enough for a shrimp crossing the frame
# at ten frames a second, tight enough that the search cannot jump to a different
# animal two body-lengths away.
FOLLOW_REACH = 1.0


def _follow(greys, box, offsets: List[int], detected_at: int) -> Dict[int, tuple]:
    """Where one animal is in each frame of its clip, tracked out from the detection.

    Correlation of the previous frame's patch against a window of the next, which
    is the cheapest thing that works at these steps: between consecutive frames
    the animal has barely changed shape, so the picture of it is its own template.
    Tracking runs outwards in both directions from the frame the model actually
    fired on, because that is the frame where the box is known to be right.

    Failure is quiet and safe: when nothing correlates well the box simply stays
    where it was, which is exactly the old fixed-box behaviour.
    """
    found = {detected_at: tuple(int(v) for v in box)}
    for direction in (1, -1):
        current = found[detected_at]
        step = detected_at + direction
        while step in offsets:
            current = _nudge(greys[step - direction], greys[step], current)
            found[step] = current
            step += direction
    for offset in offsets:
        found.setdefault(offset, tuple(int(v) for v in box))
    return found


def _nudge(before, after, box: tuple) -> tuple:
    """The same box, moved to wherever its contents went in the next frame.

    Both frames arrive already greyscale, because the caller has a whole slice of
    them and converting per comparison did the same 1920x1080 frame over and over.
    """
    import cv2

    x1, y1, x2, y2 = box
    height, width = before.shape[:2]
    template = before[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
    if min(template.shape[:2]) < 6 or float(template.std()) < 1.0:
        # Correlation of a patch with no variation in it is undefined - the
        # coefficient divides by that variation - and comes back as NaN, which
        # then compares false against any threshold and is accepted as a perfect
        # match at the window's corner. A flat patch of open water is exactly
        # this, so it has to be caught here rather than trusted to the score.
        return box
    reach_x = int((x2 - x1) * FOLLOW_REACH) + 4
    reach_y = int((y2 - y1) * FOLLOW_REACH) + 4
    left, top = max(0, x1 - reach_x), max(0, y1 - reach_y)
    window = after[top:min(height, y2 + reach_y), left:min(width, x2 + reach_x)]
    if window.shape[0] < template.shape[0] or window.shape[1] < template.shape[1]:
        return box
    try:
        scores = cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED)
        _, best, _, at = cv2.minMaxLoc(scores)
    except Exception:
        return box
    if not np.isfinite(best) or best < 0.3:
        # Nothing in reach looks like what was there - the animal left the frame,
        # or the lighting changed under it. Staying put beats guessing.
        return box
    moved_x, moved_y = left + at[0] - max(0, x1), top + at[1] - max(0, y1)
    return (x1 + moved_x, y1 + moved_y, x2 + moved_x, y2 + moved_y)


def _grey(frame) -> np.ndarray:
    """One channel, 8-bit, which is what correlation wants.

    Through OpenCV for the ordinary case rather than a numpy mean over the channel
    axis: on a 1920x1080 frame the mean allocates a float64 copy sixteen megabytes
    wide and takes an order of magnitude longer, and this runs once per frame of
    every clip.
    """
    import cv2

    array = np.asarray(frame)
    if array.ndim == 3 and array.shape[-1] == 3 and array.dtype == np.uint8:
        return cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    if array.ndim == 3:
        array = array[..., 0] if array.shape[-1] == 1 else array.mean(axis=-1)
    if array.dtype != np.uint8:
        top = float(np.nanmax(array)) or 1.0
        array = np.clip(array / top * 255.0, 0, 255)
    return np.ascontiguousarray(array, dtype=np.uint8)


def _burst(available: int, wanted: int) -> List[int]:
    """`wanted` consecutive frame offsets, centred in a slice of `available`."""
    take = max(1, min(wanted, available))
    start = max(0, (available - take) // 2)
    return list(range(start, start + take))


def _moments_to_read(available: int, fps) -> List[int]:
    """Frame offsets to run the model on, spread across the slice.

    How often to look and how long a slice is were the same decision until this
    existed, because the processor read one frame per slice - so asking to look
    every second meant one-second slices, and one-second slices meant five times as
    many cached stills and five times as many clips. On fourteen minutes of HD that
    is the difference between a 190 MB report and a 950 MB one, for a finer timeline
    nobody asked for.

    They are separate now. A five-second slice with a one-second interval is read at
    five moments spread through it. The slice still yields one still and one set of
    clips, so the report is the size it was and only the inference goes up - which
    is the thing being bought.

    `FRAMES_PER_SLICE` still applies on top: it is a *burst* of consecutive frames
    at each moment, which is a different thing wanted for a different reason.
    """
    if available <= 0:
        return []
    rate = float(fps) if fps else 0.0
    if rate <= 0 or DETECT_EVERY_SECONDS <= 0:
        return _burst(available, FRAMES_PER_SLICE)
    wanted = max(1, round((available / rate) / DETECT_EVERY_SECONDS))
    if wanted <= 1:
        return _burst(available, FRAMES_PER_SLICE)
    # Spread rather than consecutive, and offset half a step in from each end so the
    # moments sit in the middle of what they represent rather than on its seams.
    step = available / wanted
    return sorted({min(available - 1, int((i + 0.5) * step)) for i in range(wanted)})


def _individuals(animals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The distinct animals among a slice's detections, most convincing first.

    The same animal is detected again in every frame the slice looked at, and those
    repeats sit in the list beside genuinely different neighbours. Overlap tells the
    two apart: a box that lands on an earlier one is that animal again.
    """
    best: List[Dict[str, Any]] = []
    for animal in sorted(animals, key=lambda a: -a["conf"]):
        if not any(_overlap(animal["box"], seen["box"]) >= SAME_ANIMAL_IOU for seen in best):
            best.append(animal)
    return best


def _overlap(one, other) -> float:
    """Intersection over union of two boxes, 0 when they do not touch."""
    ax0, ay0, ax1, ay1 = (float(v) for v in one)
    bx0, by0, bx1, by1 = (float(v) for v in other)
    wide = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    high = max(0.0, min(ay1, by1) - max(ay0, by0))
    both = wide * high
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - both
    return both / union if union > 0 else 0.0


class FathomNetDetectorProcessor:
    """Counts and names animals in each slice using a fine-tuned detector."""

    NAME        = "raster-detections"
    DESCRIPTION = "Counts and names animals in each slice with a FathomNet-trained detector. Optional: only offered when a checkpoint and the YOLOv5 code it needs are present."
    CHUNK_KIND  = ChunkKind.LEAF
    INPUT       = RecordSpec(axes={"X", "Y"}, kinds={"intensity"}, capabilities={"spatial-2d"})
    OUTPUT      = "features"

    METRICS: Tuple[RasterMetricSpec, ...] = (
        RasterMetricSpec(
            name=DETECTION_COUNT, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description="Mean number of animals detected per sampled frame. Scored against all five annotated MBARI DeepSea-MOT sequences, the detector finds 0.63 of the annotated animals at 95% precision and 0.70 at 90%, and 0.86 of them at some confidence. Read it as a floor on what is there, not a census - and read the precision as a floor too: the most confident boxes with no annotation under them turned out, on inspection, to be real sea pens and shrimp that the benchmark had not labelled.",
        ),
        RasterMetricSpec(
            name=DETECTION_CONFIDENCE, data_type=np.float32, aggregate_rows=_scalar_rows_agg(np.nanmax),
            description="Confidence of the single most confident detection in this row. This is a fused score: the frame is read at three sizes and a size that did not see the animal counts as a vote of zero, so agreement between them raises a box and a lone sighting sinks. Useful thresholds measured against annotated footage: 0.32 for 99% precision, 0.06 for 95%, 0.02 for 90%.",
        ),
        RasterMetricSpec(
            name=DETECTION_CROP, data_type=bytes, aggregate_rows=_best_crop_agg,
            description="JPEG close-up of the most confident detection in this row, with room around the box. A tight crop of a two-centimetre animal in open water shows almost nothing, so the crop is padded to make the subject recognisable.",
        ),
        RasterMetricSpec(
            name=DETECTIONS, data_type=str, aggregate_rows=_detections_agg,
            description="Every animal the detector found in this slice, as JSON: one entry per detection per frame it looked at, each with the frame offset, the label, the confidence, the box, and a JPEG crop of that animal. This is what a per-animal view is built from - the aggregate columns beside it describe the slice, and describing the slice loses the individuals.",
        ),
        RasterMetricSpec(
            name=DETECTION_TOP_CLASS, data_type=str, aggregate_rows=_top_class_agg,
            description="Label of that most confident detection, from the detector's own taxonomy. The detector only knows the categories it was trained on, so an unfamiliar animal is either missed or misnamed.",
        ),
    )
    OUTPUT_SCHEMA = {m.name: m.data_type for m in METRICS}
    OUTPUT_SCHEMA_DESCRIPTIONS = {m.name: m.description for m in METRICS}

    def run_chunk(self, record: Record) -> Dict:
        if self._skip(record):
            return {}
        chunk = record.data.compute() if hasattr(record.data, "compute") else np.asarray(record.data)
        stack = self._spatial_stack(chunk, record.dim_order)
        if stack is None:
            return {}
        picks = _moments_to_read(len(stack), record.meta.get("fps"))
        frames = [(int(i), stack[i]) for i in picks]
        try:
            found = [detector.detect_fused(frame) for _offset, frame in frames]
        except Exception as exc:
            logger.warning("raster-detections: %s", exc)
            return {}
        _, names = detector.load_detector()
        row: Dict[str, Any] = {DETECTION_COUNT: float(np.mean([len(f) for f in found]))}
        detected = self._each_animal(frames, found, names, record)
        if not detected:
            return row
        # The summary columns describe what was detected, so they are read off the
        # detections before the clip is added - a clip frame is a crop of a frame the
        # model never saw, and has no confidence to be the maximum of.
        best = max(detected, key=lambda a: a["conf"])
        row[DETECTION_CONFIDENCE] = best["conf"]
        row[DETECTION_TOP_CLASS] = best["class"]
        if best.get("crop"):
            row[DETECTION_CROP] = base64.b64decode(best["crop"])
        row[DETECTIONS] = json.dumps(detected + self._clips_of(stack, detected, record),
                                     separators=(",", ":"))
        return row

    def _clips_of(self, stack, animals, record) -> List[Dict[str, Any]]:
        """Consecutive frames around each animal in the slice, all cropped alike.

        Three things were wrong with animating the detections themselves. Each one
        is cropped to its own box, so a krill at 15x21 px and one at 68x27 px in the
        same slice produced crops of 49x61 and 164x75 - in a fixed tile the animal
        jumps in scale and position every frame. The model only fires on some of the
        frames it looks at, so there were often one or two frames to play. And
        nothing existed before or after the moment it fired.

        One box, applied to a run of consecutive frames, fixes all three. The model
        is not run again - these are crops of frames it never saw, which is why this
        is affordable at all.

        There is one clip per animal rather than one per slice. With a single clip
        the gallery had nothing to give the second animal in a slice but the first
        animal's film, so a bottom covered in twelve sea pens showed the same sea
        pen twelve times over. Each clip carries the box it was cut with, which is
        what a reader matches an animal against to find its own.
        """
        real = [a for a in animals if not a.get("clip")]
        if not real or CLIP_FRAMES < 2:
            return []
        # Greyscale once for the whole slice rather than once per comparison.
        # Tracking six animals over ten frames asks for the frame either side of
        # each step, which is 120 conversions of a 1920x1080 frame per slice - and
        # the frames do not change between animals.
        greys = np.stack([_grey(frame) for frame in stack])
        entries: List[Dict[str, Any]] = []
        for group, animal in enumerate(_individuals(real)[:CLIPS_PER_SLICE]):
            entries.extend(self._clip_of(stack, greys, animal, group, record))
        return entries

    def _clip_of(self, stack, greys, animal, group, record) -> List[Dict[str, Any]]:
        """One animal's run of frames, each cut where the animal has got to.

        A box fixed at the moment of detection keeps the animal's scale steady,
        which is most of what makes a tile watchable - but a swimming animal
        leaves it. So the box keeps its size and is allowed to move: each frame is
        searched for the patch the last frame held, which follows the animal
        without running the model again. Scale constant, subject centred, and
        still only inference on the one frame that was detected.
        """
        fps = float(record.meta.get("fps") or 0) or None
        start = record.meta.get("dim_t")
        first = max(0, min(len(stack) - CLIP_FRAMES, animal["frame"] - CLIP_FRAMES // 2))
        offsets = list(range(first, min(len(stack), first + CLIP_FRAMES)))
        boxes = _follow(greys, animal["box"], offsets, animal["frame"])
        entries = []
        for offset in offsets:
            box = boxes[offset]
            crop = self._encode_crop(stack[offset], _AtBox(box), width=CLIP_WIDTH)
            if not crop:
                continue
            # `box` stays the box the *animal was detected in*, not the box this
            # frame was cut with. It is the clip's identity: both the gallery and
            # the collection page find an animal's own film by overlapping it
            # against the detection, and the tracked box on the first frame has
            # deliberately drifted half a second away from that. Where the crop was
            # actually taken is recorded beside it as `cut`.
            entry = {"class": animal["class"], "box": [int(v) for v in animal["box"]],
                     "cut": [int(v) for v in box],
                     "frame": offset, "clip": True, "of": group,
                     "crop": base64.b64encode(crop).decode()}
            if fps and start is not None:
                entry["second"] = round((int(start) + offset) / fps, 3)
            entries.append(entry)
        return entries

    def _each_animal(self, frames, found, names, record) -> List[Dict[str, Any]]:
        """One entry per detection per frame, most convincing first.

        The detections already exist here - the model produced them a few lines up.
        Returning only the best one is what used to force a second pass that decoded
        the recording again and ran the model again to recover the rest.
        """
        fps = float(record.meta.get("fps") or 0) or None
        start = record.meta.get("dim_t")
        entries = []
        at_frame = {}
        for (offset, frame), detections in zip(frames, found):
            at_frame[offset] = frame
            for detection in detections:
                entry = {
                    "class": (names[detection.class_index]
                              if detection.class_index < len(names)
                              else str(detection.class_index)),
                    "conf": round(float(detection.confidence), 4),
                    "box": [int(v) for v in detection.box],
                    "frame": offset,
                    "_at": detection,
                }
                # The frame's own time, not the slice's. Stamping every crop in a
                # slice with the slice start makes a burst look simultaneous, and
                # then one animal over three frames reads as three animals.
                if fps and start is not None:
                    entry["second"] = round((int(start) + offset) / fps, 3)
                entries.append(entry)
        entries.sort(key=lambda e: -e["conf"])
        entries = entries[:MOST_PER_LOOK * max(1, len(frames))]
        self._add_crops(entries, at_frame)
        return entries

    def _add_crops(self, entries: List[Dict[str, Any]], at_frame: Dict[int, Any]) -> None:
        """Give a close-up to the animals worth showing, and to no others.

        Distinct animals get the places rather than the top of the list, or a slice
        where one sea pen was detected in eight frames would spend every close-up on
        that one sea pen and leave its neighbours as boxes with no picture.
        """
        wanted = {id(a) for a in _individuals(entries)[:CROPS_PER_SLICE]}
        for entry in entries:
            detection = entry.pop("_at")
            if id(entry) not in wanted:
                continue
            crop = self._encode_crop(at_frame[entry["frame"]], detection)
            if crop:
                entry["crop"] = base64.b64encode(crop).decode()

    @staticmethod
    def _most_confident(frames, found):
        pairs = [(frame, d) for (_o, frame), detections in zip(frames, found) for d in detections]
        return max(pairs, key=lambda pair: pair[1].confidence) if pairs else None

    @staticmethod
    def _encode_crop(frame, detection, width: int = CROP_WIDTH) -> Any:
        from pixel_patrol_deepsea.slice_thumbnail_processor import encode_thumbnail

        patch = detector.crop_of(frame, detection.box)
        if min(patch.shape[:2]) < 8:
            return None
        try:
            return encode_thumbnail(patch, width=width)
        except Exception as exc:
            logger.warning("raster-detections: crop failed: %s", exc)
            return None

    @staticmethod
    def _skip(record: Record) -> bool:
        """Leave a slice alone entirely.

        Only for the channel now. The pipeline usually hands each colour channel to
        its own leaf block, and running the model three times on three greyscale
        copies of one moment costs triple for an answer the aggregation then takes
        the maximum of, so only the first channel is looked at.

        How often to look is no longer a reason to skip a whole slice - see
        `_moments_to_read`, which reads the right number of moments *within* one.
        """
        channel = record.meta.get("dim_c")
        return channel is not None and int(channel) != 0

    @staticmethod
    def _spatial_stack(chunk: np.ndarray, dim_order: str):
        """The slice as (T, Y, X[, C]), which is what everything here works on."""
        y_ax, x_ax = dim_order.index("Y"), dim_order.index("X")
        channel = dim_order.index("C") if "C" in dim_order else None
        lead = [i for i in range(chunk.ndim) if i not in (y_ax, x_ax, channel)]
        order = lead + ([channel] if channel is not None else []) + [y_ax, x_ax]
        moved = chunk.transpose(order)
        stack = (moved.reshape(-1, *moved.shape[-3:]) if channel is not None
                 else moved.reshape(-1, *moved.shape[-2:]))
        if stack.shape[0] == 0 or stack.shape[-1] < 32 or stack.shape[-2] < 32:
            return None
        return np.stack([np.moveaxis(f, 0, -1) if channel is not None else f for f in stack])

    @staticmethod
    def _frames_to_read(chunk: np.ndarray, dim_order: str):
        """Frames of the slice to run the model on, as (offset, frame) pairs.

        One frame is a sample of the slice. More than one is a *burst* of
        consecutive frames from the middle of it, not a spread across it, and the
        difference matters twice over: consecutive crops of one animal a tenth of a
        second apart play as a little animation, and they are close enough in time
        for the tracker to recognise them as the same individual. Spread across five
        seconds they are three separate animals that happen to share a label.
        """
        y_ax, x_ax = dim_order.index("Y"), dim_order.index("X")
        channel = dim_order.index("C") if "C" in dim_order else None
        lead = [i for i in range(chunk.ndim) if i not in (y_ax, x_ax, channel)]
        order = lead + ([channel] if channel is not None else []) + [y_ax, x_ax]
        moved = chunk.transpose(order)
        spatial = moved.reshape(-1, *moved.shape[-3:]) if channel is not None else moved.reshape(-1, *moved.shape[-2:])
        if spatial.shape[0] == 0 or spatial.shape[-1] < 32 or spatial.shape[-2] < 32:
            return []
        picks = _burst(spatial.shape[0], FRAMES_PER_SLICE)
        frames = []
        for i in picks:
            frame = spatial[i]
            frames.append((int(i), np.moveaxis(frame, 0, -1) if channel is not None else frame))
        return frames

    def get_aggregation(self, name: str):
        spec = next((s for s in self.METRICS if s.name == name), None)
        if spec is None:
            return None
        return lambda rows, g_dims: spec.aggregate_rows(spec, rows)
