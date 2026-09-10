"""Choosing which recordings of an expedition to actually analyse.

Listing a cruise is cheap and analysing one is not. EX2503 publishes 127 five-minute
recordings for a single dive - ten and a half hours of video for one of sixteen
dives - so "analyse the expedition" is not a thing a night has room for. Something
has to choose, and choosing badly is expensive in a way that is invisible
afterwards: the report comes out looking complete and full of nothing.

Two facts make the choice a good one rather than arbitrary, and both come out of
the dive's own report:

    **Most of a deep dive is transit.** The vehicle spends an hour or more
    descending through open water and as long coming back up, and all of it is
    recorded and published exactly like the rest. The report states when the
    vehicle reached the bottom and when it left, so those hours can simply be
    left out. On EX2503 dive 2 - 4859 m down - that is most of the footage.

    **Depth is published, so "as deep as possible" is answerable.** Each dive
    report states its maximum depth, and reading it costs one ranged read of a
    text file rather than a download of the dive.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from pixel_patrol_deepsea import locations
from pixel_patrol_deepsea.catalogue import Expedition

logger = logging.getLogger(__name__)


@dataclass
class Dive:
    """One dive of a cruise, and the recordings it published."""
    number: int
    videos: List[str] = field(default_factory=list)
    summary: Dict = field(default_factory=dict)

    @property
    def max_depth_m(self) -> float:
        return float(self.summary.get("max_depth_m") or 0.0)

    def __str__(self) -> str:
        return (f"dive {self.number:2d}  {self.max_depth_m:6.0f} m  "
                f"{len(self.videos):4d} recordings")


def by_dive(videos: Sequence[str]) -> Dict[int, Dive]:
    """The recordings of a cruise, grouped by the dive they belong to."""
    dives: Dict[int, Dive] = {}
    for url in videos:
        number = locations.dive_number(url)
        if number is None:
            continue
        dives.setdefault(number, Dive(number=number)).videos.append(url)
    return dives


def describe_dives(expedition: Expedition, videos: Sequence[str]) -> List[Dive]:
    """Every dive in the listing, with what its own report says about it."""
    data = (expedition.location or {}).get("data")
    dives = list(by_dive(videos).values())
    for dive in dives:
        if data:
            dive.summary = locations.noaa_dive_summary(data, dive.number) or {}
    return sorted(dives, key=lambda d: -d.max_depth_m)


def on_the_bottom(dive: Dive) -> List[str]:
    """The recordings made while the vehicle was working, not in transit.

    Without a dive report there is nothing to filter on and every recording is
    kept - a wrong answer would be worse than no answer here, because dropping
    footage that might hold the only animal in the dive is not recoverable.
    """
    window = locations.working_window(dive.summary)
    if window is None:
        return sorted(dive.videos)
    start, stop = window
    kept = []
    for url in sorted(dive.videos):
        when = locations.recorded_at(url)
        if when is None or start <= when <= stop:
            kept.append(url)
    if not kept:
        logger.warning("dive %d: nothing inside its on-bottom window; keeping all",
                       dive.number)
        return sorted(dive.videos)
    return kept


def spread(items: Sequence[str], most: int) -> List[str]:
    """`most` of these, evenly spaced, rather than the first `most`.

    The first N recordings of a dive are its first N times five minutes - one
    stretch of one place. Spread across the dive they are samples of everywhere it
    went, which is what a report about the dive should be built from.
    """
    if most <= 0 or len(items) <= most:
        return list(items)
    step = len(items) / most
    return [items[min(len(items) - 1, int(i * step))] for i in range(most)]


def choose(expedition: Expedition, videos: Sequence[str], dives: int = 0,
           per_dive: int = 0, most: int = 0) -> List[str]:
    """Which of an expedition's recordings to analyse, deepest dives first.

    An expedition that names its recordings outright - a benchmark, a camera that
    has filmed the same vent for a decade - has already made this choice, and
    only the overall cap applies.
    """
    if expedition.videos or not locations.dive_number(videos[0] if videos else ""):
        return spread(sorted(videos), most)
    ordered = describe_dives(expedition, videos)
    if dives:
        ordered = ordered[:dives]
    chosen: List[str] = []
    for dive in ordered:
        picked = spread(on_the_bottom(dive), per_dive)
        logger.info("%s -> %d chosen", dive, len(picked))
        chosen.extend(picked)
    return spread(chosen, most) if most else chosen


def report(expedition: Expedition, videos: Sequence[str], chosen: Sequence[str]) -> str:
    """What was chosen and why, in a line a log can carry."""
    dives = {locations.dive_number(url) for url in chosen} - {None}
    return (f"{expedition.title}: {len(chosen)} of {len(videos)} recordings"
            + (f" across {len(dives)} dives" if dives else ""))
