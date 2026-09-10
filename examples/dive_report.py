"""Analyse a full NOAA Ocean Exploration dive, end to end.

One Okeanos Explorer dive is eight to nine hours of ROV video that nobody has
watched frame by frame, and NCEI publishes about ten thousand hours of it. This
takes one dive and asks two questions of every five seconds of it:

    what moved?        raster-motion, which needs no model and so can flag an
                       animal that no detector has a class for
    what was it?       raster-detections with FathomNet's megafishdetector, which
                       knows one thing - fish - and knows it well on this footage

The two disagree usefully. A stretch where something moved and nothing named it is
either a species outside the model's vocabulary or debris, and it is the only thing
here that can point at an animal nobody has described.

    python dive_report.py --process --refine --index

Footage is fetched by scratch/fetch_dive.sh into data/noaa; see the README for why a
transcoded local proxy beats streaming for a recording analysed end to end.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
FOOTAGE = DATA / "noaa"
REPORT = DATA / "dive_EX2107_D01.parquet"
CROPS = DATA / "dive_crops"
SIGHTINGS = DATA / "dive_sightings.parquet"
SIGHTINGS_CSV = DATA / "dive_sightings.csv"

# 5 s per slice: fine enough that an animal passing through is its own row, coarse
# enough that eight hours is six thousand of them rather than thirty thousand.
SLICE_FRAMES = 50
SOURCE = ("https://www.ncei.noaa.gov/data/oceans/oer/video/EX2107/Video/"
          "EX2107_DIVE01_20211027/")


def _only(*processors):
    """`--processors-include` once per processor, which is how the CLI takes it."""
    return [part for name in processors for part in ("--processors-include", name)]


def process() -> None:
    """One pass over the dive: movement, motion objects, stills, and fish."""
    from pixel_patrol_deepsea import detector

    print(f"detector: {'ready' if detector.is_available() else 'not set up'}")
    environment = {
        **os.environ,
        # The footage is 640 wide; inferring at 1280 upsamples it for no gain.
        "PIXEL_PATROL_DETECTOR": "fish",
        "PIXEL_PATROL_DETECTOR_SIZE": "640",
        "PIXEL_PATROL_DETECTOR_CONFIDENCE": "0.25",
        "PIXEL_PATROL_DETECTOR_EVERY": "5",
    }
    subprocess.run(
        ["pixel-patrol", "process", str(FOOTAGE), "-o", str(REPORT),
         "--loader", "video", "--slice-size", f"T={SLICE_FRAMES}",
         # Keep the colour axis whole. Without this the pipeline hands each
         # channel to its own leaf and the detector sees three greyscale copies
         # of one moment - triple the inference, out of its training domain, and
         # crops that have to be replaced afterwards.
         "--slice-size", "C=-1",
         "--name", "NOAA EX2107 dive 01",
         "--description", "One full ROV dive from NOAA Ocean Exploration's Okeanos "
                          "Explorer, 27 October 2021, read end to end: what moved, and "
                          "what a fish detector could name. " + SOURCE,
         # Named rather than excluded: --processors-exclude is repeatable, not
         # comma-separated, so a list in one flag silently matches nothing and you
         # get spectral_slope over eight hours of footage. Saying what to run also
         # means a processor added later cannot join this pass by surprise.
         *_only("raster-temporal", "raster-motion", "slice-thumbnail",
                "slice-colour", "raster-detections", "raster-basic"),
         "--max-workers", "2", "--mb-per-task", "4096"],
        check=True, env=environment)


# Eight hours with a fish in view a fifth of the time is more stretches than a
# second pass can walk in a night. The longest are the real encounters.
MOST_WINDOWS = 250


def refine(most_windows: int = MOST_WINDOWS) -> None:
    """Revisit the detected stretches closely and write a crop of every fish."""
    import polars as pl

    from pixel_patrol_deepsea.reports import slice_rows
    from pixel_patrol_deepsea.refine import (
        refine_windows, windows_with_detections,
        write_sightings_csv, write_sightings_parquet,
    )

    table = pl.read_parquet(REPORT)
    slices = slice_rows(table)
    if "detection_count" not in slices.columns:
        print("no detector ran, nothing to refine")
        return

    everything = []
    for name in sorted(slices["name"].unique().to_list()):
        rows = slices.filter(pl.col("name") == name)
        fps = float(rows["fps"][0])
        windows = windows_with_detections(rows.to_dicts(), fps)
        if not windows:
            continue
        if len(windows) > most_windows:
            longest = sorted(windows, key=lambda w: w.start - w.end)[:most_windows]
            print(f"  {name}: {len(windows)} windows, revisiting the {most_windows} longest")
            windows = sorted(longest, key=lambda w: w.start)
        covered = sum(w.end - w.start + 4 for w in windows)
        print(f"  {name}: {len(windows)} windows, {covered/60:.0f} min to revisit")
        found = refine_windows(FOOTAGE / name, windows, CROPS, slice_frames=SLICE_FRAMES)
        print(f"    {len(found)} detections")
        everything.extend(found)

    if everything:
        write_sightings_csv(everything, SIGHTINGS_CSV)
        write_sightings_parquet(everything, SIGHTINGS)
        from pixel_patrol_deepsea.refine import track_sightings
        print(f"\n{len(everything)} detections -> {len(track_sightings(everything))} animals")


def summarise() -> None:
    """What the dive turned out to contain, in the terms the two passes report."""
    import polars as pl

    from pixel_patrol_deepsea.reports import slice_rows

    table = pl.read_parquet(REPORT)
    slices = slice_rows(table)
    fps = float(slices["fps"][0]) if len(slices) else 30.0
    hours = len(slices) * SLICE_FRAMES / fps / 3600
    print(f"\n{len(slices):,} slices, {hours:.1f} h of footage")
    for column, label in (("camera_speed", "camera speed px/s"),
                          ("moving_object_count", "slices with a mover"),
                          ("detection_count", "slices with a fish")):
        if column not in slices.columns:
            continue
        values = slices[column].drop_nulls()
        if column == "camera_speed":
            print(f"  {label:22} median {values.median():.0f}  "
                  f"holding station (<5) {float((values < 5).mean()):.1%}")
        else:
            print(f"  {label:22} {int((values > 0).sum()):,} ({float((values > 0).mean()):.1%})")
    if {"moving_object_count", "detection_count"} <= set(slices.columns):
        unnamed = slices.filter((pl.col("moving_object_count") > 0)
                                & ((pl.col("detection_count").is_null())
                                   | (pl.col("detection_count") == 0)))
        print(f"  {'moved, unnamed':22} {len(unnamed):,} slices - the candidates for "
              "something with no class")


def write_index() -> None:
    from pixel_patrol_deepsea.catalogue_page import write_catalogue_page

    write_catalogue_page(DATA)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--process", action="store_true", help="build the report")
    parser.add_argument("--refine", action="store_true", help="crop every detected fish")
    parser.add_argument("--index", action="store_true", help="rewrite data/index.html")
    parser.add_argument("--max-windows", type=int, default=MOST_WINDOWS,
                        help="how many detected stretches the second pass may revisit")
    args = parser.parse_args(argv)

    if args.process:
        process()
    if REPORT.exists():
        summarise()
    if args.refine:
        refine(args.max_windows)
    if args.index:
        write_index()
    return 0


if __name__ == "__main__":
    sys.exit(main())
