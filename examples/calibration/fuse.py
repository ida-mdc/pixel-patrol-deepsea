"""Combine what several detection passes found, and let agreement carry weight.

One pass has to choose a scale, and the scale it chooses decides which animals it
can see: the same frame read at 640 px and at 1280 px does not find the same
creatures. Rather than pick, run both and fuse.

Fusing gives two separate things. The union of the passes finds more animals than
any one of them - that is recall. And a box that several independent passes all
propose is far more likely to be real than one that only a single pass saw - that
is precision, bought with no threshold and no new inference. `--policy` chooses
which of the two is being asked for.
"""
import argparse, json, os
from pathlib import Path
from typing import Dict, List, Tuple

SCRATCH = Path(os.environ.get("S", "."))
CACHE = SCRATCH / "dets"
SAME_IOU = 0.45
SAME_CONTAINMENT = 0.7


def same(one, other) -> bool:
    ax1, ay1, ax2, ay2 = one[2:6]
    bx1, by1, bx2, by2 = other[2:6]
    wide = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    high = max(0.0, min(ay2, by2) - max(ay1, by1))
    both = wide * high
    if both <= 0:
        return False
    area_a, area_b = (ax2 - ax1) * (ay2 - ay1), (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - both
    return (both / union >= SAME_IOU) or (both / min(area_a, area_b) >= SAME_CONTAINMENT)


def cluster(per_source: List[List[Tuple]]) -> List[dict]:
    """Group boxes from several passes into one entry per animal.

    The most confident box in a cluster is what represents it - a pass that saw the
    animal clearly localised it better than one that barely saw it.
    """
    flat = [(box, index) for index, boxes in enumerate(per_source) for box in boxes]
    flat.sort(key=lambda pair: -pair[0][0])
    clusters: List[dict] = []
    for box, source in flat:
        for group in clusters:
            if same(box, group["box"]):
                group["confidence"].setdefault(source, box[0])
                break
        else:
            clusters.append({"box": box, "confidence": {source: box[0]}})
    return clusters


POLICIES = {
    # The loudest opinion wins. Most recall, least discrimination.
    "max":   lambda confidences, sources: max(confidences.values()),
    # Passes that did not see it vote zero, so agreement raises a box and a lone
    # sighting is pushed down - a reordering of the same set, not a filter.
    "mean":  lambda confidences, sources: sum(confidences.values()) / sources,
    # Agreement as a gate rather than a weight.
    "vote2": lambda confidences, sources: (max(confidences.values())
                                           if len(confidences) >= 2 else None),
    "vote3": lambda confidences, sources: (max(confidences.values())
                                           if len(confidences) >= 3 else None),
    "vote2mean": lambda confidences, sources: (sum(confidences.values()) / sources
                                               if len(confidences) >= 2 else None),
}


def fuse(labels: List[str], policy: str, out_label: str) -> Path:
    loaded = [json.loads((CACHE / f"{label}.json").read_text()) for label in labels]
    sequences = sorted(set.intersection(*[set(d["dets"]) for d in loaded]))
    combined: Dict[str, Dict[str, list]] = {}
    for sequence in sequences:
        names = sorted(set.intersection(*[set(d["dets"][sequence]) for d in loaded]))
        combined[sequence] = {}
        for name in names:
            groups = cluster([d["dets"][sequence][name] for d in loaded])
            boxes = []
            for group in groups:
                score = POLICIES[policy](group["confidence"], len(loaded))
                if score is None:
                    continue
                box = group["box"]
                boxes.append((round(score, 5), box[1], *box[2:6]))
            combined[sequence][name] = boxes
    where = CACHE / f"{out_label}.json"
    where.write_text(json.dumps({
        "config": {"kind": "fused", "of": labels, "policy": policy},
        "seconds_per_frame": sum(d.get("seconds_per_frame") or 0 for d in loaded),
        "dets": combined}))
    print(f"{out_label}: {len(labels)} passes, policy {policy} -> {where}")
    return where


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", nargs="+")
    parser.add_argument("--policy", default="mean", choices=sorted(POLICIES))
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)
    fuse(args.labels, args.policy, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
