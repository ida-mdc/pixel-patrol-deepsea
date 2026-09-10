"""How many animals were there, rather than how many detections there were.

A detection is a per-frame observation. The number a person reads off the
collection page is a count of *creatures*, and turning one into the other is
`refine.track_sightings`: link a detection to the one before it when the boxes
overlap, or when the box has moved less than about its own size, and close a track
when nothing has continued it for a few sampling intervals.

Every one of those tolerances is a guess unless it is measured, and DeepSea-MOT
makes it measurable: the second column of its ground truth is the identity of the
animal, so "how many animals" has a right answer. Two ways of being wrong, and
they pull against each other:

    splitting   one animal counted as several. BD's 94 animals coming out as 184
                tracks is a page that says twice as much was found as was found.
    merging     several animals counted as one, which hides finds and is the
                error that a gallery of crops makes look correct.

So both are reported, always, and a setting that improves one by wrecking the
other is not an improvement.

Unlike its neighbours here this script does not run the model: it reads the
parquet the pipeline actually wrote, which is the same thing `collect score`
judges. Grouping is cheap, so the sweep is over the report rather than over
inference.

    python -m pixel_patrol_deepsea.collect run DSMOT collection/ --jobs 5 --fps 0
    python tracking.py collection/
    python tracking.py collection/ --sequences BD BS      # just the benthic ones
"""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Sequence

from pixel_patrol_deepsea import refine
from pixel_patrol_deepsea.catalogue import (
    catalogue_path, discover, find_expedition, load_catalogue)
from pixel_patrol_deepsea.collect import _fps_of, _read_truth
from pixel_patrol_deepsea.groundtruth import MATCH_IOU, first_annotated_frame, read_mot
from pixel_patrol_deepsea.refine import Sighting, is_an_animal, track_sightings

EXPEDITION = "DSMOT"


def sightings_of(report: Path) -> List[Sighting]:
    """The detections in one recording's report, as the tracker wants them.

    Clip frames are skipped: they are crops of frames the model never looked at,
    kept for the animation, and counting them as sightings would put an animal in
    the report several times over for having been filmed.
    """
    import polars as pl

    table = pl.read_parquet(report)
    if "detections" not in table.columns:
        return []
    rows = table.filter(pl.col("detections").is_not_null() & pl.col("dim_t").is_not_null())
    key = "child_id" if "child_id" in rows.columns else "name"
    found = []
    for row in rows.iter_rows(named=True):
        where = Path(str(row.get(key) or row.get("name") or "")).stem
        try:
            animals = json.loads(row["detections"])
        except Exception:
            continue
        for animal in animals:
            if animal.get("clip"):
                continue
            found.append(Sighting(
                recording=where, second=float(animal.get("second") or 0.0),
                frame=int(animal.get("frame") or 0), slice_t=int(row["dim_t"]),
                taxon=str(animal.get("class") or "?"),
                confidence=float(animal.get("conf") or 0.0),
                box=tuple(animal.get("box") or (0, 0, 0, 0)), crop=None))
    return [s for s in found if is_an_animal(s.taxon)]


def _iou(one, other) -> float:
    left, top = max(one[0], other[0]), max(one[1], other[1])
    right, bottom = min(one[2], other[2]), min(one[3], other[3])
    inter = max(0, right - left) * max(0, bottom - top)
    union = ((one[2] - one[0]) * (one[3] - one[1])
             + (other[2] - other[0]) * (other[3] - other[1]) - inter)
    return inter / union if union > 0 else 0.0


def annotated_identity(sightings: Sequence[Sighting], truth: Dict, fps: float,
                       first_frame: int) -> Dict[int, int]:
    """Which annotated animal each detection landed on, by position in the list.

    Matched in time exactly as `groundtruth.score_recording` does it, because a
    detection judged against the wrong frame is worse than one not judged at all.
    """
    half = 0.5 / fps if fps else 0.0
    landed: Dict[int, int] = {}
    for index, sighting in enumerate(sightings):
        if not fps:
            break
        frame = round(sighting.second * fps) + first_frame
        nearest = min((f for f in (frame - 1, frame, frame + 1) if f in truth),
                      key=lambda f: abs((f - first_frame) / fps - sighting.second),
                      default=None)
        if nearest is None:
            continue
        if abs((nearest - first_frame) / fps - sighting.second) > half * 3:
            continue
        best = max(((_iou(sighting.box, box[:4]), box[4]) for box in truth[nearest]
                    if len(box) > 4), default=(0.0, None))
        if best[0] >= MATCH_IOU and best[1] is not None:
            landed[index] = best[1]
    return landed


def judge(sightings: Sequence[Sighting], landed: Dict[int, int]) -> dict:
    """Track the sightings as configured, and count both ways of being wrong."""
    where = {id(s): i for i, s in enumerate(sightings)}
    tracks = track_sightings(sightings)
    per_animal: Dict[int, set] = {}
    spanning = 0
    for number, track in enumerate(tracks):
        animals = {landed.get(where.get(id(s))) for s in track.sightings} - {None}
        if len(animals) > 1:
            spanning += 1
        for animal in animals:
            per_animal.setdefault(animal, set()).add(number)
    splits = [len(t) for t in per_animal.values()]
    return {
        "tracks": len(tracks),
        "reached": len(per_animal),
        "split": sum(splits) / len(splits) if splits else 0.0,
        "worst": max(splits, default=0),
        "spanning": spanning,
    }


def relabel_off():
    """Association that will not let a track change its name, for comparison."""
    import contextlib

    @contextlib.contextmanager
    def held(value):
        kept = refine.RELABEL_IOU
        refine.RELABEL_IOU = value
        try:
            yield
        finally:
            refine.RELABEL_IOU = kept
    return held


def settings(labels: Sequence[str]) -> List[dict]:
    """The grid. One knob moves at a time around the shipped setting."""
    grid = []
    for label in labels:
        for gap in (1.5, 2.5, 4.0, 8.0):
            for drift in (1.5, 3.0):
                grid.append({"label": label, "gap_samples": gap, "drift": drift,
                             "iou": refine.TRACK_IOU})
        grid.append({"label": label, "gap_samples": refine.GAP_SAMPLES, "drift": refine.DRIFT_SIZES,
                     "iou": 0.05})
    return grid


def measure(root: Path, wanted: Sequence[str]) -> int:
    expedition = find_expedition(load_catalogue(catalogue_path()), EXPEDITION)
    urls = {Path(url.split("?")[0]).stem: url for url in discover(expedition).videos}
    prepared = []
    for name in sorted(wanted or urls):
        report = root / "parts" / EXPEDITION / f"{name}.parquet"
        if not report.is_file():
            print(f"  {name}: not analysed yet ({report})", file=sys.stderr)
            continue
        sightings = sightings_of(report)
        truth = _read_truth(expedition, urls[name], read_mot)
        if not truth or not sightings:
            print(f"  {name}: nothing to compare", file=sys.stderr)
            continue
        fps = _fps_of(urls[name])
        landed = annotated_identity(sightings, truth, fps, first_annotated_frame(truth))
        real = {box[4] for boxes in truth.values() for box in boxes if len(box) > 4}
        prepared.append((name, sightings, landed, real))
        print(f"{name}: {len(sightings)} detections, {len(landed)} on an annotated animal, "
              f"{len(real)} animals annotated")
    if not prepared:
        return 1

    print(f"\n{'label':>8} {'gap':>5} {'drift':>6} {'iou':>5} | "
          f"{'tracks':>7} {'reached':>8} {'per animal':>11} {'worst':>6} {'spanning':>9}")
    kept = (refine.GAP_SAMPLES, refine.DRIFT_SIZES, refine.TRACK_IOU)
    held = relabel_off()
    try:
        for setting in settings(("match", "relabel", "ignore")):
            refine.GAP_SAMPLES, refine.DRIFT_SIZES = setting["gap_samples"], setting["drift"]
            refine.TRACK_IOU = setting["iou"]
            totals = {"tracks": 0, "reached": 0, "spanning": 0}
            splits, worst = [], 0
            for name, sightings, landed, real in prepared:
                # Three rules, all expressed through the same tracker. "ignore"
                # drops the taxon, which is exactly what associating without regard
                # to the label does and keeps the order, so `landed` - keyed by
                # position - still says what each detection landed on. "match" puts
                # the relabelling gate out of reach. "relabel" is what ships.
                mine = ([replace(s, taxon="?") for s in sightings]
                        if setting["label"] == "ignore" else sightings)
                with held(2.0 if setting["label"] == "match" else refine.RELABEL_IOU):
                    out = judge(mine, landed)
                for field in totals:
                    totals[field] += out[field]
                splits.append(out["split"])
                worst = max(worst, out["worst"])
            print(f"{setting['label']:>8} {setting['gap_samples']:>5} {setting['drift']:>6} "
                  f"{setting['iou']:>5} | {totals['tracks']:>7} {totals['reached']:>8} "
                  f"{sum(splits)/len(splits):>11.2f} {worst:>6} {totals['spanning']:>9}")
    finally:
        refine.GAP_SAMPLES, refine.DRIFT_SIZES, refine.TRACK_IOU = kept
    print("\nper animal: tracks the average annotated animal was split across, 1.00 is right\n"
          "spanning:   tracks that covered more than one annotated animal")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="a collection root holding parts/DSMOT/*.parquet")
    parser.add_argument("--sequences", nargs="*", default=[], help="only these")
    args = parser.parse_args(argv)
    return measure(args.root, args.sequences)


if __name__ == "__main__":
    sys.exit(main())
