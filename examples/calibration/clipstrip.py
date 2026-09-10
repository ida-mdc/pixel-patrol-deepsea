"""Lay one animal's clip out as a strip, to see whether the animation is watchable.

The test is not whether the crops are of an animal - the detector decided that -
but whether the animal stays the same size and stays in the middle of the tile
across the run. That is what a fixed box could not do and is the whole reason the
box is tracked.
"""
import base64, json, os, sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import polars as pl

report = Path(sys.argv[1])
out = Path(sys.argv[2])
wanted = int(sys.argv[3]) if len(sys.argv) > 3 else 3

from pixel_patrol_deepsea.reports import slice_rows

slices = slice_rows(pl.read_parquet(report))
clips = defaultdict(list)
for row in slices.iter_rows(named=True):
    if not row.get("detections"):
        continue
    for animal in json.loads(row["detections"]):
        if animal.get("clip") and animal.get("crop"):
            key = (row.get("child_id") or row.get("name"), row.get("dim_t"), animal.get("of"))
            clips[key].append(animal)

# The longest clips first: a run of one frame proves nothing either way.
best = sorted(clips.items(), key=lambda kv: -len(kv[1]))[:wanted]
print(f"{len(clips)} clips in {report.name}; showing {len(best)}")
rows = []
for (name, at, group), frames in best:
    frames.sort(key=lambda f: f["frame"])
    tiles = []
    for frame in frames:
        tile = cv2.imdecode(np.frombuffer(base64.b64decode(frame["crop"]), np.uint8),
                            cv2.IMREAD_COLOR)
        tiles.append(cv2.resize(tile, (150, 150), interpolation=cv2.INTER_NEAREST))
    label = f"{frames[0]['class'][:22]}  t={at}  {len(frames)} frames"
    # `cut` is where the crop was actually taken - it moves with the animal.
    # `box` is the clip's identity, the box the model fired on, and is the same on
    # every frame on purpose: it is what a reader matches an animal against.
    where = [f.get("cut", f["box"])[0] for f in frames]
    print(f"  {label}  crop x0 per frame: {where}")
    strip = np.hstack(tiles)
    banner = np.zeros((20, strip.shape[1], 3), np.uint8)
    cv2.putText(banner, label, (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
    rows.append(np.vstack([banner, strip]))
width = max(r.shape[1] for r in rows)
padded = [np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0))) for r in rows]
cv2.imwrite(str(out), np.vstack(padded))
print(f"-> {out}")
