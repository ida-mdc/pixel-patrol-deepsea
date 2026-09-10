"""What the confident mistakes actually are.

"Without false positives" is a target measured against an annotation file, and an
annotation file can be wrong. Before chasing the last of the precision it is worth
knowing whether the boxes being counted as mistakes are mistakes: a detector that
finds an animal nobody labelled is scored exactly like one that finds a rock.
"""
import json, os, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(os.environ.get("S", "."))))
from score import CACHE, GT, judge_frame, read_mot

label = sys.argv[1]
top = int(sys.argv[2]) if len(sys.argv) > 2 else 60
data = json.loads((CACHE / f"{label}.json").read_text())
names = json.loads((Path(os.environ["S"]) / "class_names.json").read_text())

hits, misses = Counter(), Counter()
worst = []
for sequence, frames in data["dets"].items():
    truth = read_mot(GT / f"{sequence}_gt.txt")
    for name, boxes in frames.items():
        frame = int(name.split(".")[0]) * 10 + 1
        if frame not in truth:
            continue
        judged, _ = judge_frame(boxes, truth[frame])
        for box, (conf, hit) in zip(sorted(boxes, key=lambda b: -b[0]), judged):
            tag = names[box[1]] if box[1] < len(names) else str(box[1])
            (hits if hit else misses)[tag] += 1
            if not hit:
                worst.append((conf, sequence, name, tag, box[2:6]))

print(f"{label}: {sum(hits.values())} on a truth box, {sum(misses.values())} not\n")
print(f"{'class':34} {'on truth':>9} {'not':>6}  {'share right':>11}")
for tag, count in (hits + misses).most_common(18):
    right = hits[tag] / max(1, hits[tag] + misses[tag])
    print(f"{tag[:34]:34} {hits[tag]:9d} {misses[tag]:6d}  {right:11.2f}")

worst.sort(key=lambda row: -row[0])
print(f"\nthe {top} most confident boxes with no truth box under them:")
for conf, sequence, name, tag, box in worst[:12]:
    print(f"  {conf:.3f}  {sequence:7} {name}  {tag[:30]:32} {[int(v) for v in box]}")
json.dump([{"conf": c, "sequence": s, "frame": n, "class": t,
            "box": [int(v) for v in b]} for c, s, n, t, b in worst[:top]],
          open(Path(os.environ["S"]) / f"errors_{label}.json", "w"), indent=1)
