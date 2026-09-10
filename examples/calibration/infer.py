"""Run one detection configuration over the ground-truth frames and cache the boxes.

Inference is the expensive part and the confidence floor is not: every run keeps
everything down to 0.001 so a threshold sweep afterwards costs nothing.
"""
import argparse, json, os, sys, time
from pathlib import Path
from typing import List, Tuple

import numpy as np

SCRATCH = Path(os.environ.get("S", "."))
FRAMES = SCRATCH / (os.environ.get("FRAMES_DIR") or "frames")
CACHE = SCRATCH / "dets"
SEQS = ("BD", "BS", "MWD", "MWS", "MD_FLN")
FLOOR = 0.001
MAX_DET = 5000


# ── configurations ────────────────────────────────────────────────────────────

def full(size):
    return dict(kind="full", size=size)

def tiles(tile_w, tile_h, size, overlap=0.25):
    return dict(kind="tiles", tile=(tile_w, tile_h), size=size, overlap=overlap)

CONFIGS = {
    "full640":        full(640),
    "full960":        full(960),
    "full1280":       full(1280),
    "full1600":       full(1600),
    "full1920":       full(1920),
    "t1280x720@1280": tiles(1280, 720, 1280),
    "t960x540@960":   tiles(960, 540, 960),
    "t960x540@640":   tiles(960, 540, 640),
    "t640x360@640":   tiles(640, 360, 640),
    "t640x360@448":   tiles(640, 360, 448),
    "t480x270@640":   tiles(480, 270, 640),
    "t320x180@640":   tiles(320, 180, 640),
}


def starts(extent: int, tile: int, overlap: float) -> List[int]:
    """Tile origins covering `extent`, the last one flush against the far edge."""
    if tile >= extent:
        return [0]
    step = max(1, int(round(tile * (1.0 - overlap))))
    out = list(range(0, extent - tile + 1, step))
    if out[-1] != extent - tile:
        out.append(extent - tile)
    return out


# ── model ─────────────────────────────────────────────────────────────────────

_MODEL = None

def model_and_names(weights: str):
    global _MODEL
    if _MODEL is None:
        import torch
        torch.set_num_threads(int(os.environ.get("TORCH_THREADS", "1")))
        os.environ["PIXEL_PATROL_DETECTOR"] = weights
        from pixel_patrol_deepsea import detector
        _MODEL = detector.load_detector()
    return _MODEL


def raw_boxes(image: np.ndarray, size: int, agnostic: bool, augment: bool, weights: str):
    """Every box the model proposes in one image, in that image's own pixels."""
    import torch
    from pixel_patrol_deepsea import detector
    model, _ = model_and_names(weights)
    from utils.general import non_max_suppression

    canvas, ratio, (pad_x, pad_y) = detector.letterbox(detector.as_rgb(image), size)
    batch = torch.from_numpy(canvas).permute(2, 0, 1).float().div(255).unsqueeze(0)
    with torch.no_grad():
        out = model(batch, augment=augment) if augment else model(batch)
    out = out[0] if isinstance(out, (list, tuple)) else out
    picked = non_max_suppression(out, conf_thres=FLOOR, iou_thres=0.45,
                                 agnostic=agnostic, max_det=MAX_DET)[0]
    if not len(picked):
        return []
    found = []
    for row in picked.tolist():
        x1, y1, x2, y2 = ((v - p) / ratio for v, p in zip(row[:4], (pad_x, pad_y, pad_x, pad_y)))
        found.append((round(row[4], 5), int(row[5]), round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)))
    return found


def detect(image: np.ndarray, config: dict, agnostic: bool, augment: bool, weights: str):
    if config["kind"] == "full":
        return raw_boxes(image, config["size"], agnostic, augment, weights)
    height, width = image.shape[:2]
    tile_w, tile_h = config["tile"]
    found = []
    for top in starts(height, tile_h, config["overlap"]):
        for left in starts(width, tile_w, config["overlap"]):
            patch = image[top:top + tile_h, left:left + tile_w]
            for conf, cls, x1, y1, x2, y2 in raw_boxes(patch, config["size"], agnostic, augment, weights):
                found.append((conf, cls, round(x1 + left, 1), round(y1 + top, 1),
                              round(x2 + left, 1), round(y2 + top, 1)))
    return merge(found)


# ── merging what the tiles found ──────────────────────────────────────────────

MERGE_IOU = 0.45
MERGE_CONTAINMENT = 0.7   # a fragment cut off at a tile edge barely overlaps its whole

def merge(boxes: List[Tuple]) -> List[Tuple]:
    """One box per animal, from boxes proposed by overlapping tiles.

    Plain IoU is not enough at a seam. A tile edge slices an animal in two, and the
    half a neighbouring tile saw whole has an IoU with that half of well under the
    threshold - so both survive and one animal is reported twice. Containment
    catches it: a box almost entirely inside a more confident one is the same animal.
    """
    kept: List[Tuple] = []
    for box in sorted(boxes, key=lambda b: -b[0]):
        if not any(_same(box, seen) for seen in kept):
            kept.append(box)
    return kept


def _same(one, other) -> bool:
    ax1, ay1, ax2, ay2 = one[2:]
    bx1, by1, bx2, by2 = other[2:]
    wide = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    high = max(0.0, min(ay2, by2) - max(ay1, by1))
    both = wide * high
    if both <= 0:
        return False
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - both
    return (both / union >= MERGE_IOU) or (both / min(area_a, area_b) >= MERGE_CONTAINMENT)


# ── running it ────────────────────────────────────────────────────────────────

def _one_frame(task):
    sequence, name, config, agnostic, augment, weights, magnify = task
    import cv2
    where = FRAMES / sequence / name
    if not where.is_file():
        # Sequences are not all the same length - two of DeepSea-MOT's five decode
        # 599 frames where the others give 600 - so a missing frame is the end of
        # a recording rather than a problem.
        return sequence, name, None, 0.0
    image = cv2.cvtColor(cv2.imread(str(where)), cv2.COLOR_BGR2RGB)
    started = time.time()
    found = detect(image, config, agnostic, augment, weights)
    if magnify != 1.0:
        # The frames were shrunk to imitate a published proxy; the ground truth is
        # still in the original's pixels, so the boxes go back to those.
        found = [(c, k, *(round(v * magnify, 1) for v in box)) for c, k, *box in found]
    return sequence, name, found, time.time() - started


def run(label: str, config: dict, every: int, workers: int, agnostic: bool,
        augment: bool, weights: str, sequences=SEQS, magnify: float = 1.0,
        frames: int = 60) -> Path:
    from multiprocessing import Pool
    tasks = [(s, f"{i:03d}.png", config, agnostic, augment, weights, magnify)
             for s in sequences for i in range(0, frames, every)]
    out: dict = {s: {} for s in sequences}
    spent = 0.0
    with Pool(workers) as pool:
        for done, (sequence, name, boxes, took) in enumerate(
                pool.imap_unordered(_one_frame, tasks, chunksize=1), 1):
            if boxes is None:
                continue
            out[sequence][name] = boxes
            spent += took
            if done % 20 == 0:
                print(f"  {label}: {done}/{len(tasks)} frames", flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    where = CACHE / f"{label}.json"
    where.write_text(json.dumps({"config": config, "agnostic": agnostic, "augment": augment,
                                 "weights": weights, "every": every,
                                 "seconds_per_frame": round(spent / max(1, len(tasks)), 2),
                                 "dets": out}))
    print(f"{label}: {spent / max(1, len(tasks)):.1f} s/frame -> {where}", flush=True)
    return where


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("configs", nargs="+")
    parser.add_argument("--every", type=int, default=3, help="use every Nth extracted frame")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--weights", default="general")
    parser.add_argument("--agnostic", action="store_true")
    parser.add_argument("--augment", action="store_true")
    parser.add_argument("--sequences", default=",".join(SEQS))
    parser.add_argument("--frames", type=int, default=60,
                        help="how many frames per sequence the frame directory holds")
    parser.add_argument("--magnify", type=float, default=1.0,
                        help="scale detections back up by this, for frames shrunk to "
                             "imitate a lower-resolution archive")
    parser.add_argument("--suffix", default="", help="appended to every label, to keep runs over different frame sets apart")
    args = parser.parse_args(argv)
    for name in args.configs:
        suffix = ("" if not args.agnostic else "+agn") + ("" if not args.augment else "+tta")
        weights = "" if args.weights == "general" else f"+{args.weights}"
        run(f"{name}{suffix}{weights}{args.suffix}", CONFIGS[name], args.every, args.workers,
            args.agnostic, args.augment, args.weights, tuple(args.sequences.split(",")),
            magnify=args.magnify, frames=args.frames)
    return 0


if __name__ == "__main__":
    sys.exit(main())
