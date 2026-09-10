"""One curve over every scored sequence, which is what a single floor means.

The per-sequence numbers answer "how well did it do on this footage". A reader
moving one threshold in the viewer is asking the other question - "what does this
setting cost me" - and that is the pooled curve, because the threshold does not
know which recording a row came from.
"""
import json
from pathlib import Path

import polars as pl

from pixel_patrol_deepsea.catalogue import (
    catalogue_path, discover, find_expedition, load_catalogue, truth_url)
from pixel_patrol_deepsea.collect import _detections_by_recording, _fetch_text, _fps_of
from pixel_patrol_deepsea.groundtruth import (
    FLOORS, _curve, _judge, first_annotated_frame, read_mot)
from pixel_patrol_deepsea.reports import slice_rows

root = Path("data/collection")
expedition = find_expedition(load_catalogue(catalogue_path()), "DSMOT")
found = _detections_by_recording(slice_rows(pl.read_parquet(root / "parquet/DSMOT.parquet")))

judged, truth_total = [], 0
for video in discover(expedition).videos:
    name = Path(video.split("?")[0]).stem
    if name not in found:
        continue
    truth = read_mot(_fetch_text(truth_url(expedition, video)))
    fps, first = _fps_of(video), first_annotated_frame(truth)
    at_frame = {}
    for detection in found[name]:
        second = detection.get("second")
        if second is None:
            continue
        frame = round(float(second) * fps) + first
        if frame in truth:
            at_frame.setdefault(frame, []).append(detection)
    for frame, boxes in at_frame.items():
        judged += _judge(boxes, truth[frame], 0.3)
        truth_total += len(truth[frame])

points = _curve(judged, truth_total)
print(f"{len(judged)} detections over {truth_total} annotated animals\n")
print(f"{'floor':>8}  {'precision':>9}  {'recall':>7}")
for want in FLOORS:
    best = max((p for p in points if p[1] >= want), key=lambda p: p[2], default=None)
    if best:
        print(f"{best[0]:>8.3f}  {best[1]:>9.3f}  {best[2]:>7.3f}   <- best recall at {want:.0%} precision")
print()
for floor in (0, 0.02, 0.06, 0.1, 0.2, 0.32, 0.5):
    above = [p for p in points if p[0] >= floor] or [points[0]]
    print(f"{floor:>8.2f}  {above[-1][1]:>9.3f}  {above[-1][2]:>7.3f}")
