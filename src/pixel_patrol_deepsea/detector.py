"""Locating and running a FathomNet YOLOv5 detector, if the user has set one up.

This is deliberately not a dependency. The published weights are CC-BY-4.0, but the
code needed to unpickle them - YOLOv5 v6.2 - is GPL-3.0, and pixel-patrol is MIT.
So nothing is vendored and nothing is declared: the processor looks for the pieces
at run time and stays out of the way when they are absent.

Point it at both pieces with environment variables, or put them in the cache
directory below:

    PIXEL_PATROL_YOLOV5     checkout of https://github.com/ultralytics/yolov5 at v6.2
    PIXEL_PATROL_DETECTOR   a .pt checkpoint, e.g. FathomNet/MBARI-midwater-supercategory-detector

Both are fetched by `python -m pixel_patrol_deepsea.fetch_detector`.
"""

import functools
import logging
import os
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "pixel-patrol" / "detector"

# Fitted on MBARI midwater footage: 1280 px gave 0.27 recall at 0.85 precision, where
# 640 px gave 0.10 at 1.00 and 1600 px gave 0.31 at 0.78. Recall tracks resolution
# because these animals are small; 1280 is the knee.
INPUT_SIZE = int(os.environ.get("PIXEL_PATROL_DETECTOR_SIZE", "1280"))

# More than one size, because the size decides which animals can be seen at all and
# there is no one right answer. Measured against all five annotated DeepSea-MOT
# sequences, recall at 95% precision:
#
#   one size    640      0.549
#               960      0.584
#               1280     0.596
#               1920     0.438     <- native resolution is the worst of them
#   fused       640+960+1280       0.634
#
# The model has a scale it wants animals to arrive at, and it is about two thirds of
# HD - feeding it the frame at full size is worse than halving it. Which is also why
# cutting the frame into overlapping tiles and detecting at native scale, the obvious
# thing to try for small animals, made it worse: 0.478, and 0.041 at 99% precision,
# for three times the work. It helped on the two midwater sequences and wrecked the
# benthic ones, where a tile seam cuts animals in half and a dense seabed fills a
# tile edge to edge.
#
# Fusing sizes is the version of the same idea that works, because it changes the
# scale the animal arrives at without cutting anything up.
FUSED_SIZES = tuple(int(s) for s in
                    os.environ.get("PIXEL_PATROL_DETECTOR_SIZES", "640,960,1280").split(",")
                    if s.strip())
# Low on purpose. Scored against MBARI's annotated benthic sequences, a 0.25 floor
# recalled 0.57 of the animals at 0.99 precision - it was throwing away real animals
# to buy a precision nobody asked for. Every box below the floor is one the pipeline
# can never recover, while a box above it can always be hidden later, and the viewer
# does exactly that with a floor of its own that a reader can move.
#
#   floor   BD prec/recall   BS prec/recall
#    0.25     0.99 / 0.57      0.99 / 0.40
#    0.10     0.96 / 0.68      0.91 / 0.44
#    0.05     0.92 / 0.75      0.82 / 0.49
#    0.02     0.85 / 0.82      0.69 / 0.54
#
# Below 0.05 precision falls faster than recall rises. What is left after that is not
# a threshold: a third of the BS annotations are never proposed at any confidence.
CONFIDENCE = float(os.environ.get("PIXEL_PATROL_DETECTOR_CONFIDENCE", "0.05"))


def yolov5_path() -> Optional[Path]:
    override = os.environ.get("PIXEL_PATROL_YOLOV5")
    candidate = Path(override) if override else CACHE / "yolov5"
    return candidate if (candidate / "models" / "experimental.py").is_file() else None


def weights_path() -> Optional[Path]:
    """Which checkpoint to run, by name or by path.

    More than one model is worth having - fish and gelatinous zooplankton are
    different problems and no single checkpoint here covers both - so
    PIXEL_PATROL_DETECTOR takes either the name of one fetched into the cache
    ("fish", "midwater", "general") or a path to any other.
    """
    override = os.environ.get("PIXEL_PATROL_DETECTOR")
    if override:
        if Path(override).is_file():
            return Path(override)
        named = CACHE / f"{override}.pt"
        return named if named.is_file() else None
    found = sorted(CACHE.glob("*.pt")) if CACHE.is_dir() else []
    return found[0] if found else None


def is_available() -> bool:
    """True when both halves are present and torch can be imported."""
    if yolov5_path() is None or weights_path() is None:
        return False
    try:
        import cv2  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


@functools.lru_cache(maxsize=1)
def load_detector():
    """Load the checkpoint once per process. Returns (model, class_names)."""
    import torch

    root = yolov5_path()
    if root is None or weights_path() is None:
        raise RuntimeError("no detector configured; see pixel_patrol_deepsea.detector")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # YOLOv5 v6.2 predates torch 2.6's weights_only default, and its checkpoints are
    # pickled model objects rather than plain tensors.
    original = torch.load
    torch.load = functools.partial(original, weights_only=False)
    try:
        from models.experimental import attempt_load
        model = attempt_load(str(weights_path()), device="cpu", inplace=True, fuse=True).eval()
    finally:
        torch.load = original
    names = model.names
    return model, (list(names.values()) if isinstance(names, dict) else list(names))


def letterbox(frame: np.ndarray, size: int, stride: int = 32):
    """Resize keeping aspect, pad to a stride multiple; returns the array and the mapping back."""
    import cv2

    height, width = frame.shape[:2]
    ratio = min(size / height, size / width)
    new_h, new_w = int(round(height * ratio)), int(round(width * ratio))
    pad_h, pad_w = (-new_h) % stride, (-new_w) % stride
    canvas = np.full((new_h + pad_h, new_w + pad_w, 3), 114, np.uint8)
    canvas[pad_h // 2:pad_h // 2 + new_h, pad_w // 2:pad_w // 2 + new_w] = cv2.resize(frame, (new_w, new_h))
    return canvas, ratio, (pad_w // 2, pad_h // 2)


def as_rgb(frame: np.ndarray) -> np.ndarray:
    """Detectors expect 8-bit RGB; slices arrive as whatever the loader produced."""
    array = frame
    if array.ndim == 2:
        array = np.repeat(array[:, :, None], 3, axis=2)
    elif array.shape[-1] == 1:
        array = np.repeat(array, 3, axis=2)
    elif array.shape[-1] > 3:
        array = array[..., :3]
    if array.dtype != np.uint8:
        top = float(np.nanmax(array)) or 1.0
        array = np.clip(array / top * 255.0, 0, 255).astype(np.uint8)
    return array


def detect(frame: np.ndarray) -> List[Tuple[float, int]]:
    """Return (confidence, class index) for every detection in one RGB frame."""
    return [(d.confidence, d.class_index) for d in detect_boxes(frame)]


class Detection(NamedTuple):
    """One detection, in the coordinates of the frame that was passed in."""
    confidence: float
    class_index: int
    box: Tuple[int, int, int, int]          # x1, y1, x2, y2


# How many boxes one pass may return. YOLOv5's own default is 300, which is not a
# number anybody here chose: an annotated seabed holds around fifty animals per
# frame and this runs at a confidence floor of 0.05, so the default was quietly
# truncating the list before anything downstream could see it.
MOST_BOXES = 3000


def detect_boxes(frame: np.ndarray, size: Optional[int] = None) -> List["Detection"]:
    """Detections with their boxes mapped back onto the original frame.

    The model sees a padded square, so every coordinate it returns has to be
    un-padded and un-scaled before it means anything on the frame handed in.

    Suppression is deliberately **class-agnostic**. This checkpoint knows 499
    classes and the honest truth is that it cannot tell many of them apart, so
    left to itself it returns the same animal five times over under five different
    names and every one of them counts as a separate detection. Measured on all
    five annotated DeepSea-MOT sequences, ignoring the label while suppressing
    took recall at 95% precision from 0.531 to 0.596 and improved every sequence.
    The label is still reported - it is just not allowed to decide what is one
    animal and what is two.
    """
    import torch

    model, _ = load_detector()          # puts the yolov5 checkout on sys.path
    from utils.general import non_max_suppression

    rgb = as_rgb(frame)
    canvas, ratio, (pad_x, pad_y) = letterbox(rgb, size or INPUT_SIZE)
    batch = torch.from_numpy(canvas).permute(2, 0, 1).float().div(255).unsqueeze(0)
    with torch.no_grad():
        raw = model(batch)
    raw = raw[0] if isinstance(raw, (list, tuple)) else raw
    picked = non_max_suppression(raw, conf_thres=CONFIDENCE, iou_thres=0.45,
                                 agnostic=True, max_det=MOST_BOXES)[0]

    height, width = rgb.shape[:2]
    found = []
    for row in picked:
        pads = (pad_x, pad_y, pad_x, pad_y)
        x1, y1, x2, y2 = ((float(v) - pad) / ratio for v, pad in zip(row[:4], pads))
        found.append(Detection(
            confidence=float(row[4]),
            class_index=int(row[5]),
            box=(max(0, int(x1)), max(0, int(y1)), min(width, int(x2)), min(height, int(y2))),
        ))
    return found


def detect_fused(frame: np.ndarray, sizes: Sequence[int] = ()) -> List["Detection"]:
    """One frame read at several sizes, with agreement folded into the confidence.

    Two things come out of this and only one of them is more animals. The union of
    the passes finds creatures no single size found - that is the recall. And a box
    that every size proposed is far likelier to be real than one that only the
    smallest saw, so a pass that did not see it is counted as a vote of zero and
    the confidence becomes the mean over all of them. That reordering is what buys
    precision, without a threshold and without discarding anything: measured on the
    annotated sequences it beat taking the loudest opinion (0.634 against 0.635 at
    95% precision but 0.700 against 0.683 at 90%), and it beat every single size.
    """
    at = sizes or FUSED_SIZES
    groups = _group_across_passes([detect_boxes(frame, size) for size in at])
    fused = [Detection(confidence=round(total / max(1, len(at)), 5),
                       class_index=best.class_index, box=best.box)
             for best, total in groups]
    fused.sort(key=lambda d: -d.confidence)
    return fused


def _group_across_passes(passes: Sequence[List["Detection"]]):
    """Gather the passes' boxes into one entry per animal, one opinion per pass.

    The most confident box in a group represents it: a pass that saw the animal
    clearly also localised it better than one that barely saw it, and it is the
    only one of them with a label worth reporting.

    **One vote per pass**, which is the whole point and was worth a bug. A pass can
    put two boxes on one animal - suppression within a pass drops boxes that
    overlap, and grouping here also joins a box almost entirely inside another,
    which suppression does not. Summing both made the total exceed the number of
    passes, so a "mean over the sizes" came out above 1.0 and the agreement it is
    supposed to measure was double-counted for exactly the animals two boxes
    disagreed about.
    """
    flat = sorted(((d, index) for index, found in enumerate(passes) for d in found),
                  key=lambda pair: -pair[0].confidence)
    groups: List[dict] = []
    for detection, source in flat:
        for group in groups:
            if same_animal(detection.box, group["best"].box):
                group["votes"].setdefault(source, detection.confidence)
                break
        else:
            groups.append({"best": detection, "votes": {source: detection.confidence}})
    return [(group["best"], sum(group["votes"].values())) for group in groups]


SAME_IOU = 0.45
# A box almost entirely inside a more confident one is the same animal seen less
# well, which plain overlap misses whenever the two passes disagree about how much
# of the creature to include - a jelly's bell against its bell plus tentacles.
SAME_CONTAINMENT = 0.7


def same_animal(one: Sequence[float], other: Sequence[float]) -> bool:
    """Whether two boxes of the same frame are one animal or two."""
    ax1, ay1, ax2, ay2 = one
    bx1, by1, bx2, by2 = other
    wide = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    high = max(0.0, min(ay2, by2) - max(ay1, by1))
    both = wide * high
    if both <= 0:
        return False
    area_a = max(1e-9, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1e-9, (bx2 - bx1) * (by2 - by1))
    return (both / (area_a + area_b - both) >= SAME_IOU
            or both / min(area_a, area_b) >= SAME_CONTAINMENT)


def crop_of(frame: np.ndarray, box: Tuple[int, int, int, int], margin: float = 0.6) -> np.ndarray:
    """The detection, with enough room around it to see what it is.

    A box drawn tight around a two-centimetre animal in open water is a picture of
    almost nothing; the margin is what makes the crop recognisable.
    """
    rgb = as_rgb(frame)
    height, width = rgb.shape[:2]
    x1, y1, x2, y2 = box
    pad_x = int((x2 - x1) * margin) + 8
    pad_y = int((y2 - y1) * margin) + 8
    return rgb[max(0, y1 - pad_y):min(height, y2 + pad_y),
               max(0, x1 - pad_x):min(width, x2 + pad_x)]
