"""Look closely only where the coarse pass saw something.

Running a detector on every frame of a dive is the obvious approach and the wrong
one. Inference is by far the most expensive thing in the pipeline, and an animal
stays in frame for a long time - measured against MBARI's annotated sequences, a
median of 182 frames in midwater and 290 on the bottom. Sampling one frame a second
already sees 96-99% of the distinct animals a full read would.

So the first pass is deliberately sparse and its job is only to say *where* to look.
This is the second pass: it takes the slices that came back with a detection, walks
those stretches of footage at full rate, and writes out every detection it finds
along with a crop of it. The expensive work lands only on the footage that earned it.

    windows = windows_with_detections(table, recording)
    found = refine_windows(video_path, windows, crops_dir)
"""

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# How far either side of a coarse hit to look. The coarse pass samples every few
# seconds, so a hit means "something was here recently", not "exactly here".
PAD_SECONDS = 2.0
# Frames to actually run through the model per second of a refined window.
REFINED_FPS = 4.0


@dataclass(frozen=True)
class Window:
    """A stretch of footage the coarse pass flagged."""
    start: float
    end: float

    def padded(self, pad: float, limit: float) -> "Window":
        return Window(max(0.0, self.start - pad), min(limit, self.end + pad))


@dataclass(frozen=True)
class Sighting:
    """One detection in one frame - one animal, not one slice.

    This is deliberately its own record rather than more columns on a slice. The
    main table is an aggregation tree over image dimensions: a row is a slice, a
    channel, a whole image, and every metric on it rolls up along those axes. An
    animal is not one of those axes - a slice can hold twenty of them - so putting
    them in that table would either collapse them to a summary (which is what the
    slice columns already do) or break the invariant every widget queries on.

    So sightings live beside the report, keyed by recording and slice so the two
    can be joined whenever a per-animal view is wanted.
    """
    recording: str
    second: float
    frame: int
    slice_t: int
    taxon: str
    confidence: float
    box: Sequence[int]
    crop: Optional[bytes] = None
    crop_path: Optional[Path] = None


def windows_with_detections(rows: Iterable[Dict], fps: float, merge_gap: float = 4.0) -> List[Window]:
    """Merge the coarse hits into a few stretches worth a closer look.

    Adjacent hits are almost always the same animal, so they are joined rather than
    refined separately - otherwise the second pass repeats the same seconds.
    """
    seconds = sorted(float(r["dim_t"]) / fps for r in rows
                     if r.get("detection_count") and float(r["detection_count"]) > 0)
    windows: List[Window] = []
    for second in seconds:
        if windows and second - windows[-1].end <= merge_gap:
            windows[-1] = Window(windows[-1].start, second)
        else:
            windows.append(Window(second, second))
    return windows


def refine_windows(video: Path, windows: Sequence[Window], crops_dir: Optional[Path] = None,
                   refined_fps: float = REFINED_FPS, pad: float = PAD_SECONDS,
                   slice_frames: int = 30) -> List[Sighting]:
    """Walk each window at a higher rate and record every detection with a crop."""
    import av

    from pixel_patrol_deepsea import detector

    if not windows:
        return []
    _, names = detector.load_detector()
    if crops_dir:
        crops_dir.mkdir(parents=True, exist_ok=True)

    sightings: List[Sighting] = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        source_fps = float(stream.average_rate or 30.0)
        duration = float(stream.duration * stream.time_base) if stream.duration else 1e9
        step = max(1, round(source_fps / refined_fps))
        wanted = _frames_to_visit(windows, source_fps, duration, pad, step)
        if not wanted:
            return []
        for index, frame in enumerate(container.decode(video=0)):
            if index not in wanted:
                continue
            second = index / source_fps
            rgb = frame.to_ndarray(format="rgb24")
            # The same fused read as the coarse pass, so the two agree about what
            # is an animal and about how confident to be. A second pass that scored
            # differently from the first would give the gallery a threshold that
            # means one thing in the timeline and another in the crops.
            for detection in detector.detect_fused(rgb):
                taxon = (names[detection.class_index]
                         if detection.class_index < len(names) else str(detection.class_index))
                crop, crop_path = _make_crop(crops_dir, video, rgb, detection, taxon, second)
                sightings.append(Sighting(
                    recording=video.name, second=second, frame=index,
                    slice_t=index - index % slice_frames,
                    taxon=taxon, confidence=detection.confidence, box=detection.box,
                    crop=crop, crop_path=crop_path,
                ))
            if index > max(wanted):
                break
    return sightings


def _frames_to_visit(windows, fps: float, duration: float, pad: float, step: int) -> set:
    """Frame indices inside the padded windows, at the refined rate."""
    visit = set()
    for window in windows:
        padded = window.padded(pad, duration)
        first, last = int(padded.start * fps), int(padded.end * fps)
        visit.update(range(first - first % step, last + 1, step))
    return visit


def _make_crop(crops_dir, video: Path, rgb, detection, taxon: str, second: float):
    """Encode the crop once, keep the bytes, and write a file when asked."""
    from pixel_patrol_deepsea import detector
    from pixel_patrol_deepsea.slice_thumbnail_processor import encode_thumbnail

    patch = detector.crop_of(rgb, detection.box)
    if min(patch.shape[:2]) < 8:
        return None, None
    try:
        blob = encode_thumbnail(patch, width=256)
    except Exception as exc:
        logger.warning("refine: could not encode a crop: %s", exc)
        return None, None
    if crops_dir is None:
        return blob, None
    name = (f"{video.stem}_{int(second // 60):02d}m{second % 60:05.2f}s"
            f"_{taxon.replace(' ', '-')}_{detection.confidence:.2f}.jpg")
    path = crops_dir / name
    path.write_bytes(blob)
    return blob, path


def _timecode(second: float) -> str:
    return f"{int(second // 60):02d}:{second % 60:05.2f}"


def write_sightings_csv(sightings: Sequence[Sighting], path: Path) -> Path:
    """The sightings as a plain table, for anything that reads CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["recording", "second", "timecode", "frame", "dim_t", "taxon",
                         "confidence", "x1", "y1", "x2", "y2", "crop"])
        for s in sightings:
            writer.writerow([s.recording, f"{s.second:.2f}", _timecode(s.second), s.frame,
                             s.slice_t, s.taxon, f"{s.confidence:.3f}", *s.box,
                             s.crop_path.name if s.crop_path else ""])
    return path


def write_sightings_parquet(sightings: Sequence[Sighting], path: Path) -> Path:
    """One row per animal, keyed so it joins straight onto the slice table.

    `name` and `dim_t` are the report's own keys, so a per-animal view is a join
    away without the main table having to change shape.
    """
    import polars as pl

    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([{
        "name": s.recording,
        "dim_t": s.slice_t,
        "frame": s.frame,
        "second": s.second,
        "timecode": _timecode(s.second),
        "taxon": s.taxon,
        "confidence": s.confidence,
        "x1": s.box[0], "y1": s.box[1], "x2": s.box[2], "y2": s.box[3],
        "crop": s.crop,
    } for s in sightings]).write_parquet(path)
    return path


@dataclass
class Track:
    """One animal, followed across the frames it stayed in view for.

    A detection is a per-frame observation, and a gallery of those is misleading:
    106 crops of a jellyfish look like 106 jellyfish when they are one animal seen
    106 times. A track is the thing a person would count.
    """
    recording: str
    taxon: str
    first_second: float
    last_second: float
    sightings: List[Sighting]

    @property
    def frames(self) -> int:
        return len(self.sightings)

    @property
    def seconds(self) -> float:
        return self.last_second - self.first_second

    @property
    def best(self) -> Sighting:
        return max(self.sightings, key=lambda s: s.confidence)


# Classes in the 499-name vocabulary that are not animals. A detector that can say
# `equipment` about the ROV's arm, or `geologic` about a rock, is worth more than any
# geometric rule guessing at the same thing - so where it does say so, believe it.
NOT_AN_ANIMAL = frozenset({
    "equipment", "detritus sampler", "suction sampler", "anchor", "sinker",
    "ship container", "trash", "geologic", "sand", "marine snow", "detritus",
    "plantae detritus", "phyllospadix-zostera detritus", "pyrosoma detritus",
    "salp detritus", "bacterial mat", "amphipod tube mat", "bone", "carcass",
    "medusa carcass", "molt", "krill molt", "shell", "eggcase", "ink", "wood",
    "kelp", "mung", "carapace", "tube", "stalk", "chromista", "plantae",
})


def is_an_animal(taxon: str) -> bool:
    """Whether a label names a creature rather than a rock, a sampler or a shell."""
    return taxon.strip().lower() not in NOT_AN_ANIMAL


def looks_like_vehicle(track: "Track", width: int, height: int) -> bool:
    """True when this track is part of the ROV rather than something it passed.

    The manipulator arm is bolted to the vehicle, so it enters from a frame edge
    and stays against it for as long as it is deployed - on one dive a claw was
    detected as a fish for two minutes straight, 73 times, and it was the single
    largest "animal" on the page. An animal crossing the view touches an edge on
    the way in and on the way out, not for half a minute.

    It says nothing about a sponge sitting in the middle of the frame being called
    a fish. That is the detector's vocabulary, not geometry, and no rule here can
    reach it.
    """
    if track.seconds < VEHICLE_SECONDS:
        return False
    against = sum(_touches_edge(s.box, width, height) for s in track.sightings)
    return against / max(1, track.frames) >= VEHICLE_EDGE_SHARE


def _touches_edge(box: Sequence[int], width: int, height: int) -> bool:
    x1, y1, x2, y2 = box
    return (x1 <= EDGE_MARGIN or y1 <= EDGE_MARGIN
            or x2 >= width - EDGE_MARGIN or y2 >= height - EDGE_MARGIN)


TRACK_IOU = 0.15        # boxes overlapping less than this are not obviously one animal
RELABEL_IOU = 0.3       # ...but this much overlap outweighs the model changing its mind
TRACK_GAP = 1.5         # seconds out of view before a track is closed, at least
GAP_SAMPLES = 2.5       # ...or this many sampling intervals, whichever is longer
DRIFT_SIZES = 3.0       # a box may move this many of its own widths and still be it
BURST_WINDOW = 0.2      # frames closer together than this are one look, not two
GAP_CEILING = 30.0      # however sparse the sampling, half a minute unseen is a new animal
EDGE_MARGIN = 3         # px; a box this close to the frame boundary is touching it
VEHICLE_EDGE_SHARE = 0.8   # this much of a track's life spent against an edge...
VEHICLE_SECONDS = 10.0     # ...for this long, and it is bolted to the vehicle


def track_sightings(sightings: Sequence[Sighting], iou_gate: float = TRACK_IOU,
                    max_gap: Optional[float] = None) -> List[Track]:
    """Link per-frame detections into one entry per animal.

    Greedy association within a taxon, frame to frame. Not a real tracker - it
    cannot survive an occlusion or a crossing - but the question it answers is "how
    many animals were there", which a gallery of per-frame crops answers wrongly.

    Both of its tolerances have to follow the sampling, and that is the part that
    was wrong. A detector run once every five seconds sees the same fish five
    seconds later, several body-lengths along; judged by an absolute gap of 1.5 s
    and by box overlap alone, one fish followed across half a minute came out as
    eight animals. So the gap is measured in sampling intervals, and a box that has
    moved by about its own size counts as the same animal even when the two boxes
    do not touch.
    """
    tracks: List[Track] = []
    for recording in sorted({s.recording for s in sightings}):
        mine = [s for s in sightings if s.recording == recording]
        gap = max_gap if max_gap is not None else _gap_for(mine)
        open_tracks: List[Track] = []
        for second in sorted({s.second for s in mine}):
            in_frame = [s for s in mine if s.second == second]
            open_tracks = [t for t in open_tracks if second - t.last_second <= gap]
            for sighting in sorted(in_frame, key=lambda s: -s.confidence):
                if not _extend_best_match(open_tracks, sighting, iou_gate):
                    fresh = Track(recording, sighting.taxon, second, second, [sighting])
                    open_tracks.append(fresh)
                    tracks.append(fresh)
    # A track that changed its mind is named by its best look at the animal rather
    # than by whichever label happened to open it.
    for track in tracks:
        track.taxon = track.best.taxon
    return tracks


def _gap_for(sightings: Sequence[Sighting]) -> float:
    """How long an animal may go unseen, from how often the detector looked.

    Read off the data rather than configured, because the two are the same fact: if
    consecutive looks are five seconds apart, an animal missing for five seconds was
    not missing - nobody looked.

    It has to be the interval between *looks*, not between frames. The detector
    reads a burst of consecutive frames each time it looks, so the frame-to-frame
    spacing is a fiftieth of a second and the median of every step is that, not the
    five seconds that actually matter. Frames within a fifth of a second are one
    look.
    """
    looks = _look_times(sorted({s.second for s in sightings}))
    if len(looks) < 2:
        return TRACK_GAP
    steps = sorted(b - a for a, b in zip(looks, looks[1:]))
    typical = steps[len(steps) // 2]
    return min(GAP_CEILING, max(TRACK_GAP, typical * GAP_SAMPLES))


def _look_times(seconds: Sequence[float]) -> List[float]:
    """One time per look, collapsing each burst of frames into its first."""
    looks: List[float] = []
    for second in seconds:
        if not looks or second - looks[-1] > BURST_WINDOW:
            looks.append(second)
    return looks


def _extend_best_match(open_tracks: List[Track], sighting: Sighting, iou_gate: float) -> bool:
    """Put this detection on the open track it best continues, if any does.

    The label is evidence, not identity. This checkpoint knows 499 classes and
    cannot tell many of them apart - it is the reason suppression within a frame
    ignores the label entirely - so one jellyfish called `trachylinae` at one look
    and `scyphozoa` at the next is one jellyfish, and requiring the two to agree
    counted it twice. Measured against DeepSea-MOT's own identities, insisting on
    the label split the average animal across 2.8 tracks.

    Ignoring it outright is worse in the other direction: on a crowded seabed two
    animals of different kinds passing close by then merge. So the label may change
    when the geometry is not in doubt - the boxes genuinely overlap - and not when
    the match rests on proximity alone.
    """
    candidates = []
    for track in open_tracks:
        if track.last_second >= sighting.second:
            continue
        if track.taxon == sighting.taxon:
            score = _agreement(track.sightings[-1].box, sighting.box, iou_gate)
        else:
            overlap = _iou(track.sightings[-1].box, sighting.box)
            score = 1.0 + overlap if overlap >= RELABEL_IOU else 0.0
        if score > 0:
            candidates.append((score, track))
    best = max(candidates, key=lambda pair: pair[0], default=(0.0, None))
    if best[1] is None or best[0] <= 0:
        return False
    best[1].sightings.append(sighting)
    best[1].last_second = sighting.second
    return True


def _agreement(before: Sequence[int], now: Sequence[int], iou_gate: float) -> float:
    """How well one box continues another: overlap first, then plain proximity.

    Overlap alone assumes the animal barely moved between looks, which is true at
    ten frames a second and false at one look every five. Proximity relative to the
    animal's own size covers the second case: a fish that has moved less than about
    its own length is the same fish. The error this trades into - two animals of one
    species passing close together merged into one - is the rarer of the two and the
    less misleading on a page that counts individuals.
    """
    overlap = _iou(before, now)
    if overlap >= iou_gate:
        return 1.0 + overlap
    span = max(1.0, (before[2] - before[0] + now[2] - now[0]) / 2)
    moved = np.hypot(((before[0] + before[2]) - (now[0] + now[2])) / 2,
                     ((before[1] + before[3]) - (now[1] + now[3])) / 2)
    return max(0.0, 1.0 - moved / (span * DRIFT_SIZES))


def _iou(a: Sequence[int], b: Sequence[int]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0, right - left) * max(0, bottom - top)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / union if union > 0 else 0.0


