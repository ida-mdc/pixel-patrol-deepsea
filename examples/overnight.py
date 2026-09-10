"""Collect a night's worth of deep-sea expeditions into one browsable collection.

    python overnight.py collection/                 # the whole plan
    python overnight.py collection/ --only DSMOT    # one expedition
    python overnight.py collection/ --plan          # print the plan, run nothing
    python overnight.py collection/ --site-only     # rebuild the page over what is there

Each expedition is `collect run`, which lists the archive, chooses what is worth
analysing, analyses those in parallel and merges the result. Everything is
resumable: a recording whose parquet exists is skipped, so a run that was
interrupted is continued by starting it again.

The plan below is the argument of this script - which footage, and how hard to
look at it. Three things decide it.

**One expedition can be checked.** MBARI's DeepSea-MOT ships a box around every
animal in every frame, so it is the only footage here whose numbers are measured
rather than claimed. It is analysed first, at every slice rather than every fifth
second, and scored afterwards. Everything else inherits the settings that
expedition justified.

**Depth and distance are the point of the rest.** Four Okeanos Explorer cruises
that are nowhere near each other - the Central Pacific, the Gulf of Alaska, the
Mid-Atlantic Ridge, the US West Coast - plus the Blake Plateau and the Marianas.
Deepest dives first, and only the footage taken while the vehicle was on the
bottom: a 4859 m dive spends hours descending through open water and publishes
every minute of it.

**One camera has been filming the same place for a decade.** Five recordings from
the ASHES vent field on Axial Seamount, two years apart each time. No cruise can
answer what changed; this can, and it is the reason the reports carry a timestamp
and a position at all.

The cruise pair is the same question asked of a ship: EX1903L2 and EX2107 worked
the same Blake Plateau coral mounds two years apart, which is as close to a repeat
visit as a research vessel gets.
"""

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


# One second of footage per slice, and one look per second. Measured against
# DeepSea-MOT, counting distinct animals rather than boxes:
#
#   0.30 s apart   0.83 of the animals
#   0.60 s         0.83
#   0.90 s         0.79
#   1.50 s         0.76
#   3.75 s         0.63
#
# The old setting was five seconds, off the bottom of that table. One second costs
# five times the inference and is worth it; below half a second the curve is flat
# and it is not. Precision is 0.96-0.97 at every interval, so this buys recall and
# risks nothing.
#
# A slice is one second because `--fps 10` thins the footage to ten frames a second.
# Slice length and how often the detector looks are separate settings now - see
# `detector_processor._moments_to_read` - so five-second slices with a one-second
# interval would find exactly as much for a fifth of the cached stills and clips.
# One-second slices are what is set here because a one-second timeline is worth
# having in its own right, and the price is report size rather than accuracy.
SLICE_FRAMES = 10
DETECT_EVERY = 1


@dataclass
class Leg:
    """One expedition, and how hard to look at it.

    `jobs` is bounded by memory rather than by cores. A worker running the fused
    detector on HD footage is resident at about 4.8 GB and on a 640x360 proxy at
    about 3.5 GB, so what fits at once is roughly RAM over five gigabytes - which
    on a 22-core, 93 GB machine is ten to twelve, not twenty-two.
    """
    expedition: str
    why: str
    jobs: int = 10
    dives: int = 0
    per_dive: int = 0
    most: int = 0
    fps: float = 10.0
    slice_frames: int = SLICE_FRAMES
    detect_every: float = DETECT_EVERY
    detector_sizes: str = "640,960,1280"
    extra: List[str] = field(default_factory=list)

    def command(self, root: Path) -> List[str]:
        out = ["-m", "pixel_patrol_deepsea.collect", "run", self.expedition, str(root),
               "--jobs", str(self.jobs), "--fps", str(self.fps),
               "--slice-frames", str(self.slice_frames),
               "--detect-every", str(self.detect_every),
               "--detector-sizes", self.detector_sizes]
        for name, value in (("--dives", self.dives), ("--per-dive", self.per_dive),
                            ("--most", self.most)):
            if value:
                out += [name, str(value)]
        return out + self.extra


# Six recordings across four dives is what a night was planned around, and a night
# turned out to be shorter than the plan: measured, one 640x360 proxy recording is
# about 25 minutes of wall time with ten running at once, so 24 of them is over an
# hour per cruise and six cruises is most of a day.
#
# So the plan trades footage per cruise for cruises. Three dives and three
# recordings each is 45 minutes of footage from a cruise rather than two hours, and
# what that buys is that the Central Pacific, the Gulf of Alaska, the Mid-Atlantic
# Ridge, the West Coast, the Blake Plateau - twice, nine years apart - and the
# Marianas are all in the collection rather than the first two of them. Diversity
# was the ask; depth of sampling within one cruise was not.
#
# Raising these is the first thing to do with a spare afternoon - the run is
# resumable, so it costs only the recordings that are new.
DIVES, PER_DIVE = 3, 3

# The proxies NOAA publishes are 640x360. Measured on the annotated sequences
# shrunk to that size, a third pass at 1280 px bought two thousandths of recall
# for twice the cost - there is no detail in a proxy for a larger input to find.
# So those cruises read each frame at two sizes and the full-resolution footage
# reads it at three.
PROXY_SIZES = "640,960"

PLAN: List[Leg] = [
    Leg("DSMOT", jobs=5, fps=0, slice_frames=10, detect_every=1,
        why="the only footage with a box around every animal; looked at every slice, "
            "and scored afterwards against its own ground truth"),
    Leg("OOI_AXIAL_CAMHD", jobs=5,
        why="the same hydrothermal vent, 1542 m down, on the same day of the year in "
            "2016, 2018, 2022, 2024 and 2026"),
    Leg("EX2503", jobs=10, dives=DIVES, per_dive=PER_DIVE, detector_sizes=PROXY_SIZES,
        why="4859 m in Papahanaumokuakea, the deepest of the modern cruises"),
    Leg("EX2306", jobs=10, dives=DIVES, per_dive=PER_DIVE, detector_sizes=PROXY_SIZES,
        why="Gulf of Alaska seamounts to 4262 m; cold, high-latitude deep sea"),
    Leg("EX2205", jobs=10, dives=DIVES, per_dive=PER_DIVE, detector_sizes=PROXY_SIZES,
        why="the Mid-Atlantic Ridge rift valley, the only Atlantic ridge here"),
    Leg("EX2301", jobs=10, dives=DIVES, per_dive=PER_DIVE, detector_sizes=PROXY_SIZES,
        why="California and Oregon submarine canyons to 3956 m"),
    Leg("EX2107", jobs=10, dives=DIVES, per_dive=PER_DIVE, detector_sizes=PROXY_SIZES,
        why="Blake Plateau coral mounds; a 1 Hz vehicle track, so every slice has "
            "its own position and depth"),
    Leg("EX1903L2", jobs=10, dives=DIVES, per_dive=PER_DIVE, detector_sizes=PROXY_SIZES,
        why="the same coral mounds two years earlier - the one pair of cruises here "
            "that visited the same place twice"),
    Leg("EX1605L1", jobs=10, most=9, detector_sizes=PROXY_SIZES,
        why="the Marianas and the deepest footage here, 4996 m; the one cruise that "
            "published its clips by subject - ROVHD_FSH is the fish, which is a "
            "label the detector never saw"),
]


def python() -> List[str]:
    """This interpreter, so a subprocess lands in the environment that has the package."""
    return [sys.executable]


def run_leg(leg: Leg, root: Path) -> int:
    """One expedition, with its own log beside the collection."""
    log = root / "logs" / f"{leg.expedition}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    print(f"\n== {leg.expedition}: {leg.why}")
    print(f"   {' '.join(leg.command(root)[2:])}")
    with open(log, "a") as stream:
        stream.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {leg.expedition}\n")
        stream.flush()
        code = subprocess.call(python() + leg.command(root), stdout=stream,
                               stderr=subprocess.STDOUT)
    print(f"   {'done' if not code else f'FAILED ({code})'} in "
          f"{(time.time() - started) / 60:.0f} min -> {log}")
    return code


def score_what_can_be(root: Path) -> None:
    """Check every expedition that ships ground truth, and leave the numbers on disk."""
    for leg in PLAN:
        parquet = root / "parquet" / f"{leg.expedition}.parquet"
        if not parquet.is_file():
            continue
        code = subprocess.call(
            python() + ["-m", "pixel_patrol_deepsea.collect", "score",
                        leg.expedition, str(root)],
            stdout=sys.stdout, stderr=subprocess.STDOUT)
        if code:
            continue        # says for itself that it had nothing to score against


def build_site(root: Path) -> None:
    subprocess.call(python() + ["-m", "pixel_patrol_deepsea.collect", "site", str(root)])


def space_left_gb(root: Path) -> float:
    return shutil.disk_usage(root).free / (1 << 30)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--only", action="append", default=[],
                        help="just these expeditions, by id")
    parser.add_argument("--plan", action="store_true", help="print the plan and stop")
    parser.add_argument("--site-only", action="store_true",
                        help="score and rebuild the page over what is already collected")
    parser.add_argument("--least-space-gb", type=float, default=4.0,
                        help="stop before an expedition if the disk is this low")
    args = parser.parse_args(argv)

    legs = [leg for leg in PLAN if not args.only or leg.expedition in args.only]
    if args.plan:
        for leg in legs:
            print(f"{leg.expedition:18} {leg.why}")
            print(f"{'':18} {' '.join(leg.command(args.root)[4:])}\n")
        return 0

    args.root.mkdir(parents=True, exist_ok=True)
    if not args.site_only:
        for leg in legs:
            free = space_left_gb(args.root)
            if free < args.least_space_gb:
                print(f"stopping before {leg.expedition}: {free:.1f} GB left",
                      file=sys.stderr)
                break
            run_leg(leg, args.root)

    print("\n== against the ground truth")
    score_what_can_be(args.root)
    print("\n== the collection page")
    build_site(args.root)
    print(f"\nserve it:  cd {args.root} && python3 -m http.server 8090")
    return 0


if __name__ == "__main__":
    sys.exit(main())
