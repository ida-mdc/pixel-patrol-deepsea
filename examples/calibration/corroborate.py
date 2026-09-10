"""Ask the next frames whether they saw it too.

A detector run on one frame has no way to tell a faint animal from a speck of
noise that happened to look like one. The footage does: an animal is still there a
thirtieth of a second later, and a spurious box is not. So the cheapest precision
available is not a higher threshold - it is a second look at the following frames,
which is also what the two-pass design already pays for.

This scores that. It takes the boxes found on a frame and the boxes found on the
frames after it, and either filters or reweights the first set by how many of the
others agree.
"""
import argparse, json, os
from pathlib import Path
from typing import List, Sequence, Tuple

SCRATCH = Path(os.environ.get("S", "."))
CACHE = SCRATCH / "dets"


def follows(box: Sequence[float], other: Sequence[float], iou_floor: float) -> bool:
    """Whether two boxes a frame or two apart are the same animal.

    Plain overlap is too strict here in a way it is not across scales: between
    frames the animal has *moved*, and a small fast one can leave its own box
    entirely. So a box that has drifted less than its own width also counts.
    """
    ax1, ay1, ax2, ay2 = box[2:6]
    bx1, by1, bx2, by2 = other[2:6]
    wide = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    high = max(0.0, min(ay2, by2) - max(ay1, by1))
    both = wide * high
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - both
    if union > 0 and both / union >= iou_floor:
        return True
    reach = max(ax2 - ax1, ay2 - ay1)
    moved_x = abs((ax1 + ax2) - (bx1 + bx2)) / 2
    moved_y = abs((ay1 + ay2) - (by1 + by2)) / 2
    return max(moved_x, moved_y) <= reach


def agreement(boxes: List[Tuple], neighbours: List[List[Tuple]], iou_floor: float):
    """Per box, the confidence each following frame gave the same animal."""
    out = []
    for box in boxes:
        seconded = []
        for later in neighbours:
            best = max((other[0] for other in later if follows(box, other, iou_floor)),
                       default=0.0)
            seconded.append(best)
        out.append((box, seconded))
    return out


POLICIES = {
    "require1": lambda conf, later: (conf if any(v > 0 for v in later) else None),
    "require2": lambda conf, later: (conf if sum(v > 0 for v in later) >= 2 else None),
    # Frames that did not see it vote zero, as in the scale fusion: persistence
    # lifts a box and a one-frame flicker sinks, without discarding anything.
    "mean":     lambda conf, later: (conf + sum(later)) / (1 + len(later)),
    # Filter and reweight together.
    "r1mean":   lambda conf, later: ((conf + sum(later)) / (1 + len(later))
                                     if any(v > 0 for v in later) else None),
}


def run(centre: str, later: List[str], policy: str, iou_floor: float, out_label: str) -> Path:
    first = json.loads((CACHE / f"{centre}.json").read_text())
    rest = [json.loads((CACHE / f"{label}.json").read_text()) for label in later]
    kept: dict = {}
    for sequence, frames in first["dets"].items():
        kept[sequence] = {}
        for name, boxes in frames.items():
            neighbours = [d["dets"].get(sequence, {}).get(name, []) for d in rest]
            out = []
            for box, seconded in agreement(boxes, neighbours, iou_floor):
                score = POLICIES[policy](box[0], seconded)
                if score is not None:
                    out.append((round(score, 5), box[1], *box[2:6]))
            kept[sequence][name] = out
    where = CACHE / f"{out_label}.json"
    where.write_text(json.dumps({
        "config": {"kind": "corroborated", "centre": centre, "later": later, "policy": policy},
        "seconds_per_frame": (first.get("seconds_per_frame") or 0)
                             + sum(d.get("seconds_per_frame") or 0 for d in rest),
        "dets": kept}))
    print(f"{out_label}: {centre} confirmed against {len(rest)} later frames ({policy}) -> {where}")
    return where


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("centre")
    parser.add_argument("later", nargs="+")
    parser.add_argument("--policy", default="mean", choices=sorted(POLICIES))
    parser.add_argument("--iou", type=float, default=0.2)
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)
    run(args.centre, args.later, args.policy, args.iou, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
