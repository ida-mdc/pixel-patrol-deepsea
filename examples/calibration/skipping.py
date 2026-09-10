"""What reading fewer frames costs, over the whole range.

Reading one frame in fifty is the largest saving in the pipeline and the easiest
thing to get wrong in either direction, so it is worth a curve rather than an
opinion. Two things make the curve honest.

**Count creatures, not boxes.** DeepSea-MOT is a tracking benchmark: it boxes the
same animal in every frame it is visible, so BD's 94 animals are 28,708 boxes.
Recall over boxes therefore mostly measures how often each animal was *re*-found,
and one sighting is all it takes to put a creature in a report. The identity in
the second column of the ground truth is what recall should be counted over.

**Judge against every annotated frame, not the ones that were read.** An animal on
a frame nobody looked at is an animal the report does not contain, and calling it
out of scope measures the detector instead of the pipeline.

The detector is run over *every* frame of every sequence once - that is what
`FRAMES_DIR=frames_all infer.py --every 1 --frames 600` is for - and then
thinned. Keeping every kth frame of that is exactly what a pass reading k times
less often would have found, so the whole curve comes from one run.

    python skipping.py                       # after the dense run is cached
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pixel_patrol_deepsea.groundtruth import _judge, read_mot   # noqa: E402
from score import CACHE, GT                                     # noqa: E402

# Frames per second of each sequence, read from its own header once. Only used to
# turn "every kth frame" into a number of seconds, which is the form the setting
# actually takes (`--detect-every`).
FPS = {"BD": 29.976, "BS": 59.94, "MWD": 59.94, "MWS": 60.009, "MD_FLN": 24.933}
STRIDES = (1, 2, 3, 4, 6, 8, 12, 20, 30, 50)
# The dense pass reads every Nth frame of the source; a stride here is a multiple
# of that, so the interval on the clock is stride * EXTRACTED_EVERY / fps.
EXTRACTED_EVERY = 3
# The floor the pipeline actually writes at. The cache keeps everything down to
# 0.001 so a threshold can be swept, and scoring it at that floor reports a
# precision of 0.47 that no report has ever shown - comparable numbers need the
# comparable floor.
FLOOR = 0.06


def judge(frames: dict, truth: dict, stride: int, floor: float):
    """Animals found, boxes offered and boxes that landed, at one sampling.

    Thinned by *position among the frames that were cached*, not by frame number.
    The dense pass reads every third frame, so filtering on `index % stride` asks
    for frames divisible by both and gives the same set for stride 1 and 3 - which
    is how this first produced a table with duplicate rows in it.
    """
    animals, matched, offered, read = set(), 0, 0, 0
    for name in sorted(frames)[::stride]:
        frame = int(name.split(".")[0]) + 1   # frames were extracted from frame one
        if frame not in truth:
            continue
        read += 1
        kept = [{"conf": b[0], "box": b[2:6]} for b in frames[name] if b[0] >= floor]
        rows = _judge(kept, truth[frame], 0.3)
        offered += len(rows)
        matched += sum(1 for _conf, landed in rows if landed is not None)
        animals.update(landed for _conf, landed in rows if landed is not None)
    return animals, matched, offered, read


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", default="fuse3_dense",
                        help="the cached detections to thin")
    parser.add_argument("--floor", type=float, default=FLOOR,
                        help="confidence floor, to match what a report writes")
    parser.add_argument("--extracted-every", type=int, default=EXTRACTED_EVERY,
                        help="the dense pass read every Nth frame of the source")
    args = parser.parse_args(argv)

    data = json.loads((CACHE / f"{args.label}.json").read_text())
    truths = {s: read_mot((GT / f"{s}_gt.txt").read_text()) for s in data["dets"]}
    everything = {s: len(t) for s, t in truths.items()}
    all_animals = {(s, box[4]) for s, t in truths.items()
                   for boxes in t.values() for box in boxes}

    print(f"{args.label}: {sum(everything.values())} annotated frames, "
          f"{len(all_animals)} distinct animals, floor {args.floor}\n")
    print(f"{'read':>14} {'apart':>8}  {'animals found':>13}  {'boxes':>7}  {'precision':>9}")
    rows = []
    for stride in STRIDES:
        found, matched, offered, read = set(), 0, 0, 0
        apart = []
        for sequence, frames in data["dets"].items():
            animals, hit, out, saw = judge(frames, truths[sequence], stride, args.floor)
            found |= {(sequence, a) for a in animals}
            matched, offered, read = matched + hit, offered + out, read + saw
            apart.append(stride * args.extracted_every / FPS.get(sequence, 30.0))
        share = read / sum(everything.values())
        rows.append({
            "stride": stride, "frames_read": read, "share_of_frames": share,
            "seconds_apart": sum(apart) / len(apart),
            "animals_found": len(found) / len(all_animals),
            "boxes": offered, "precision": matched / offered if offered else None,
        })
        print(f"{read:6d}/{sum(everything.values()):<6d} {share:5.1%} "
              f"{rows[-1]['seconds_apart']:7.2f}s  {rows[-1]['animals_found']:12.2f}  "
              f"{offered:7d}  {rows[-1]['precision'] or 0:9.2f}")

    where = Path(os.environ.get("S", ".")) / f"skipping_{args.label}.json"
    where.write_text(json.dumps(rows, indent=1))
    print(f"\n-> {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
