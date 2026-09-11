"""What each slice of footage was doing, decided once and written into the report.

Four verdicts, and between them they answer the question the collection exists to
answer - which minutes are worth a person's time:

    frozen   nothing changes between frames: duplicated tape, a dropped feed, a
             frozen frame. Time nobody should spend watching.
    subject  a detector named an animal here.
    unnamed  something moved independently of the camera and nothing named it,
             which is the only signal here that can point at a species no model
             has a class for.
    dwell    the camera holding still on something with detail in frame.
    empty    holding still on open water or a blank field. Moves like a dwell;
             only the detail in frame separates them.
    active   large frame-to-frame change: transit, cuts, subjects entering frame.

Two of the thresholds are percentiles of the recording's own movement, not fixed
numbers, because "holding still" and "moving hard" mean different things on a
transect and on a descent. That is the whole reason this lives here rather than in
a processor: a processor sees one slice and cannot know the distribution it
belongs to, while `collect one` holds the recording.

It used to live in the viewer, in JavaScript, computed on every page load from
every slice of every recording and then shipped back into SQL as an inlined table
so it could be plotted - which is what broke the triage widget on a collection of
287 recordings, and would have broken again at some larger number. Decided here,
it is four columns on a recording's own row, and a plot is a column name.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Below this much frame-to-frame change, nothing is moving at all.
FROZEN_BELOW = 0.01
# Percentiles of the recording's own movement: below the first the camera is
# holding still, above the second it is moving hard.
DWELL_PERCENTILE = 15
ACTIVE_PERCENTILE = 90
# Detail below this share of the recording's usual detail means nothing is in
# frame - what separates an empty dwell from one with a subject in it.
EMPTY_DETAIL_RATIO = 0.2
# A run shorter than this is a blip, and two runs of the same kind closer than
# this are one run with a gap in it.
MIN_WINDOW_ROWS = 2
MERGE_GAP_ROWS = 10

VERDICTS = ("frozen", "subject", "unnamed", "dwell", "empty", "active")


@dataclass
class Slice:
    """One slice of a timeline, as the verdict needs it."""
    t: int
    movement: float
    structure: Optional[float] = None
    detections: Optional[float] = None
    movers: Optional[float] = None
    top_class: Optional[str] = None


@dataclass
class Window:
    """A run of slices that shared a verdict."""
    kind: str
    label: Optional[str]
    start: int
    end: int                     # inclusive, in slice positions
    from_t: int = 0
    to_t: int = 0

    @property
    def slices(self) -> int:
        return self.end - self.start + 1


@dataclass
class Verdicts:
    """A recording's slices and what each of them was doing."""
    per_slice: List[Optional[str]] = field(default_factory=list)
    windows: List[Window] = field(default_factory=list)
    seconds: Dict[str, float] = field(default_factory=dict)
    total_seconds: float = 0.0


def percentile(values: Sequence[float], p: float) -> float:
    """The viewer's percentile, kept to the letter.

    Nearest-rank on the sorted values with a floor, which is not numpy's default
    and is not worth improving: this decides a threshold that is compared against
    the same values it came from, and changing it would move every verdict in
    every report ever written for no gain.
    """
    usable = sorted(v for v in values if v is not None and math.isfinite(v))
    if not usable:
        return 0.0
    index = min(len(usable) - 1, int((p / 100.0) * len(usable)))
    return usable[index]


def _is_empty(structure: Optional[float], detail: float) -> bool:
    return (detail > 0 and structure is not None and math.isfinite(structure)
            and structure < detail * EMPTY_DETAIL_RATIO)


def classify(movement: float, structure: Optional[float], detections: Optional[float],
             dwell_below: float, active_above: float, detail: float,
             movers: Optional[float]) -> Optional[str]:
    """What one slice was doing, or nothing if it was doing nothing in particular.

    The order is the argument. Frozen first, because dead footage is dead whatever
    else is true of it. Then anything a detector recognised, because movement only
    ever says the camera moved and a named animal is what someone came to find.
    Then something that moved on its own without being named, which is below a name
    because a name is more information and above everything else because nothing
    else here can point at a species that has none yet.
    """
    if movement is None or not math.isfinite(movement):
        return None
    if movement < FROZEN_BELOW:
        return "frozen"
    if detections is not None and detections > 0:
        return "subject"
    if movers is not None and movers > 0:
        return "unnamed"
    if movement <= dwell_below:
        return "empty" if _is_empty(structure, detail) else "dwell"
    if movement >= active_above:
        return "active"
    return None


def verdict_per_slice(timeline: Sequence[Slice]) -> List[Optional[str]]:
    """Every slice's verdict, in the order they were filmed."""
    movements = [s.movement for s in timeline
                 if s.movement is not None and math.isfinite(s.movement)]
    dwell_below = percentile([v for v in movements if v >= FROZEN_BELOW], DWELL_PERCENTILE)
    active_above = percentile(movements, ACTIVE_PERCENTILE)
    detail = percentile([s.structure for s in timeline
                         if s.structure is not None and math.isfinite(s.structure)], 50)
    return [classify(s.movement, s.structure, s.detections,
                     dwell_below, active_above, detail, s.movers) for s in timeline]


def find_windows(timeline: Sequence[Slice]) -> List[Window]:
    """Runs of slices sharing a verdict, merged across short gaps.

    A named animal is part of a run's identity, not just a label on it: two
    different species one after the other are two finds, not one long one.
    """
    if len(timeline) < MIN_WINDOW_ROWS:
        return []
    kinds = verdict_per_slice(timeline)
    runs: List[Window] = []
    for index, (slice_, kind) in enumerate(zip(timeline, kinds)):
        label = (slice_.top_class or "") if kind == "subject" else None
        open_run = runs[-1] if runs else None
        if (open_run and open_run.kind == kind and open_run.label == label
                and open_run.end == index - 1):
            open_run.end = index
        elif kind:
            runs.append(Window(kind=kind, label=label, start=index, end=index))
    merged = _merge_nearby(runs)
    kept = [run for run in merged if _long_enough(run)]
    for run in kept:
        run.from_t = timeline[run.start].t
        run.to_t = timeline[run.end].t
    return kept


def _long_enough(run: Window) -> bool:
    """Long enough to be an event rather than a threshold wobble.

    Every kind but one is a movement value crossing a line, and one slice either
    side of a line is noise. A detection is not: a model looked at the frame and
    named the animal in it. Three of the seven species in one midwater report are
    only ever on screen for a single slice, and holding them to the same minimum
    dropped them from the gallery entirely - the report said seven species and
    showed four.
    """
    if run.kind == "subject":
        return True
    return run.slices >= MIN_WINDOW_ROWS


def _merge_nearby(runs: Sequence[Window]) -> List[Window]:
    merged: List[Window] = []
    for run in runs:
        open_run = None
        for candidate in reversed(merged):
            if run.start - candidate.end - 1 > MERGE_GAP_ROWS:
                break
            if candidate.kind == run.kind and candidate.label == run.label:
                open_run = candidate
                break
        if open_run:
            open_run.end = max(open_run.end, run.end)
        else:
            merged.append(Window(kind=run.kind, label=run.label,
                                 start=run.start, end=run.end))
    return merged


def summarise(timeline: Sequence[Slice], fps: Optional[float]) -> Verdicts:
    """A recording's verdicts, and how many seconds each one accounts for.

    Seconds rather than slices, because a report holds recordings sampled at
    different rates and a share of slices is not a share of footage.
    """
    windows = find_windows(timeline)
    step = (timeline[1].t - timeline[0].t) if len(timeline) > 1 else 1
    to_seconds = (lambda frames: frames / fps) if fps else (lambda frames: float(frames))
    seconds = {kind: 0.0 for kind in VERDICTS}
    for window in windows:
        seconds[window.kind] = seconds.get(window.kind, 0.0) + to_seconds(
            window.to_t - window.from_t)
    return Verdicts(per_slice=verdict_per_slice(timeline), windows=windows,
                    seconds=seconds,
                    total_seconds=to_seconds(len(timeline) * step))
