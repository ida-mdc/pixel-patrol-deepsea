"""Score cached detections against DeepSea-MOT, as a precision/recall curve.

The question asked of this dataset is "how many animals can be found without
saying anything that is not there", so the number that matters is not average
precision but *recall at a precision we would be willing to publish*. Every
config is therefore reported at three precision floors as well as in aggregate.
"""
import argparse, json, os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

SCRATCH = Path(os.environ.get("S", "."))
CACHE = SCRATCH / "dets"
GT = Path(os.environ.get("PIXEL_PATROL_DSMOT", "../data/dsmot"))
MATCH_IOU = 0.3
FLOORS = (0.99, 0.95, 0.90)


def read_mot(path: Path) -> Dict[int, List[Tuple[float, float, float, float]]]:
    boxes: Dict[int, List] = defaultdict(list)
    for line in path.read_text().splitlines():
        parts = line.split(",")
        if len(parts) < 6:
            continue
        frame = int(float(parts[0]))
        x, y, w, h = (float(parts[i]) for i in range(2, 6))
        boxes[frame].append((x, y, x + w, y + h))
    return dict(boxes)


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    wide = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    high = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    both = wide * high
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - both
    return both / union if union > 0 else 0.0


def judge_frame(boxes: List[Tuple], truth: List[Tuple], match_iou=MATCH_IOU):
    """(confidence, hit) per detection, plus how many truth boxes there were."""
    taken, out = set(), []
    for box in sorted(boxes, key=lambda b: -b[0]):
        best, at = match_iou, None
        for index, want in enumerate(truth):
            if index not in taken:
                overlap = iou(box[2:], want)
                if overlap >= best:
                    best, at = overlap, index
        if at is not None:
            taken.add(at)
        out.append((box[0], at is not None))
    return out, len(truth)


def curve(judged: List[Tuple[float, bool]], truth_total: int):
    """Precision and recall at every confidence, most confident first."""
    hits = 0
    points = []
    for rank, (conf, hit) in enumerate(sorted(judged, key=lambda j: -j[0]), 1):
        hits += hit
        points.append((conf, hits / rank, hits / truth_total if truth_total else 0.0))
    return points


def recall_at(points, floor: float) -> Tuple[float, float]:
    """The most recall reachable while precision stays at or above `floor`."""
    best = (0.0, 1.0)
    for conf, precision, recall in points:
        if precision >= floor and recall > best[0]:
            best = (recall, conf)
    return best


def summarise(label: str, data: dict, sequences=None, classes=None, every: int = 1):
    per_sequence, overall = {}, []
    truth_all = 0
    for sequence, frames in data["dets"].items():
        if sequences and sequence not in sequences:
            continue
        truth = read_mot(GT / f"{sequence}_gt.txt")
        judged, total = [], 0
        for name, boxes in frames.items():
            index = int(name.split(".")[0])
            if index % every:
                continue        # a common frame set, so configs run at different
                                # sampling rates are compared on the same footage
            frame = index * 10 + 1
            if frame not in truth:
                continue
            if classes is not None:
                boxes = [b for b in boxes if b[1] in classes]
            rows, count = judge_frame(boxes, truth[frame])
            judged += rows
            total += count
        if not total:
            continue
        points = curve(judged, total)
        per_sequence[sequence] = dict(
            truth=total, found=len(judged),
            best_recall=points[-1][2] if points else 0.0,
            **{f"r@p{int(f * 100)}": recall_at(points, f) for f in FLOORS})
        overall += judged
        truth_all += total
    whole = curve(overall, truth_all)
    return dict(label=label, seconds_per_frame=data.get("seconds_per_frame"),
                sequences=per_sequence, truth=truth_all,
                overall={f"r@p{int(f * 100)}": recall_at(whole, f) for f in FLOORS},
                best_recall=whole[-1][2] if whole else 0.0)


def show(rows: List[dict]) -> None:
    print(f"\n{'config':22} {'s/frm':>6}  " + "  ".join(f"{'r@p' + str(int(f * 100)):>10}" for f in FLOORS) + f" {'max rec':>8}")
    print("-" * 78)
    for row in sorted(rows, key=lambda r: -r["overall"][f"r@p{int(FLOORS[1] * 100)}"][0]):
        cells = "  ".join(f"{row['overall'][f'r@p{int(f * 100)}'][0]:>10.3f}" for f in FLOORS)
        print(f"{row['label'][:22]:22} {row['seconds_per_frame'] or 0:>6.1f}  {cells} {row['best_recall']:>8.3f}")


def per_sequence_table(rows: List[dict]) -> None:
    print(f"\nrecall at precision 0.95, per sequence")
    names = sorted({s for row in rows for s in row["sequences"]})
    print(f"{'config':22} " + "  ".join(f"{n:>8}" for n in names))
    print("-" * (23 + 10 * len(names)))
    for row in sorted(rows, key=lambda r: -r["overall"]["r@p95"][0]):
        cells = []
        for name in names:
            got = row["sequences"].get(name)
            cells.append(f"{got['r@p95'][0]:>8.3f}" if got else f"{'-':>8}")
        print(f"{row['label'][:22]:22} " + "  ".join(cells))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", nargs="*")
    parser.add_argument("--sequences")
    parser.add_argument("--per-sequence", action="store_true")
    parser.add_argument("--every", type=int, default=1,
                        help="score only every Nth extracted frame, so runs over "
                             "different samplings are judged on the same frames")
    args = parser.parse_args(argv)
    labels = args.labels or sorted(p.stem for p in CACHE.glob("*.json"))
    wanted = args.sequences.split(",") if args.sequences else None
    rows = [summarise(label, json.loads((CACHE / f"{label}.json").read_text()), wanted,
                      every=args.every)
            for label in labels]
    show(rows)
    if args.per_sequence:
        per_sequence_table(rows)
    (SCRATCH / "scores.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
