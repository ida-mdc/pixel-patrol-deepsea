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
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pixel_patrol_deepsea.reports import ROW_GROUP_BYTES

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


@dataclass(slots=True)
class Slice:
    """One slice of a timeline, as the verdict needs it.

    With slots, because a report is judged one whole recording at a time and
    GOA2004 is 442,338 of these at once.
    """
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

    Counted a slice at a time and not a window at a time. A window is a stretch
    worth opening: short runs of the same kind are merged across gaps of up to ten
    slices so that a find is one entry in the gallery rather than five, and runs of
    two species interleave. Both are right for a gallery and wrong for an
    accounting - the merged gaps belong to whatever was actually in them, and two
    species' windows cover the same footage twice. Summed that way the verdicts
    came to more than the recording was long: one EX2107 recording of 300 seconds
    reported 340 seconds of `subject`, and a share of a recording plotted as 113%.
    A slice belongs to exactly one verdict, so counting slices cannot do that.
    """
    windows = find_windows(timeline)
    per_slice = verdict_per_slice(timeline)
    step = (timeline[1].t - timeline[0].t) if len(timeline) > 1 else 1
    to_seconds = (lambda frames: frames / fps) if fps else (lambda frames: float(frames))
    # Slices first and seconds after, so that forty thirds of a second add up to
    # the recording's own length rather than to a hair over it.
    counted: Dict[str, int] = {}
    for kind in per_slice:
        if kind:                        # a slice in none of the six counts for none
            counted[kind] = counted.get(kind, 0) + 1
    seconds = {kind: to_seconds(counted.get(kind, 0) * step) for kind in VERDICTS}
    return Verdicts(per_slice=per_slice, windows=windows, seconds=seconds,
                    total_seconds=to_seconds(len(timeline) * step))


# The columns a report carries once this has run. One string per slice, and one
# number per verdict on the recording's own row - which is where a fact about a
# recording belongs, and means a plot of "how much of each recording was dead" is
# a column name rather than the data itself travelling into the query.
SLICE_VERDICT = "slice_verdict"
FOOTAGE_SECONDS = "footage_seconds"


def seconds_column(kind: str) -> str:
    return f"verdict_seconds_{kind}"


VERDICT_COLUMNS = (FOOTAGE_SECONDS,) + tuple(seconds_column(k) for k in VERDICTS)


def _recording_column(columns: Sequence[str]) -> str:
    return "child_id" if "child_id" in columns else "name"


# A batch while the numbers are read, and how much of a batch is worth writing as
# one row group. Rows carry pictures, so the second is in bytes: see `collect.merge`,
# which cuts its row groups the same way and for the same reason.
READ_ROWS = 256


def describe(report: Path) -> int:
    """Judge every slice in a report and write the verdicts into it.

    Returns how many recordings were judged. Per slice, what it was doing; per
    recording, how many seconds each verdict accounts for, on the aggregate row
    the pipeline already writes for it.

    Two passes, and neither holds the report. The first reads the nine columns a
    verdict is made of - numbers, a few bytes a row - and decides everything. The
    second copies the file through, adding the answers to each row group as it
    goes. Reading it whole instead is what a report's pictures make impossible:
    GOA2004 is 3.37 GB on disk, several times that in memory, and judging it that
    way is a machine nobody has. Written beside the report and moved over it at
    the end, because the copy is reading the original while it runs.

    Rewritten through arrow so the `pp_*` metadata survives, for the same reason
    `identity` does it that way.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    source = pq.ParquetFile(report, pre_buffer=False)
    have = list(source.schema_arrow.names)
    if not {"dim_t", "frame_difference", "obs_level"} <= set(have):
        return 0
    key = _recording_column(have)
    detail = next((c for c in ("laplacian_variance", "std_intensity") if c in have), None)
    asked = [key, "dim_t", "frame_difference", detail, "detection_count",
             "moving_object_count", "detection_top_class", "fps", "obs_level"]
    wanted = list(dict.fromkeys(c for c in asked if c and c in have))

    # Slices by recording, in the order they were filmed, remembering which row
    # each came from so the verdict can be written back to it.
    lines: Dict[str, List[tuple]] = {}
    names: List[str] = []
    levels: List[Optional[int]] = []
    rates: Dict[str, float] = {}
    row = 0
    for batch in source.iter_batches(batch_size=READ_ROWS, columns=wanted):
        held = {name: batch.column(name).to_pylist() if name in wanted
                else [None] * batch.num_rows for name in set(asked) if name}
        for i in range(batch.num_rows):
            name = "" if held[key][i] is None else str(held[key][i])
            names.append(name)
            levels.append(held["obs_level"][i])
            rate = held["fps"][i]
            if rate and name not in rates:
                rates[name] = float(rate)
            moment, movement = held["dim_t"][i], held["frame_difference"][i]
            if moment is not None and movement is not None:
                structure = held[detail][i] if detail else None
                lines.setdefault(name, []).append((row + i, Slice(
                    t=int(moment), movement=float(movement),
                    structure=None if structure is None else float(structure),
                    detections=(None if held["detection_count"][i] is None
                                else float(held["detection_count"][i])),
                    movers=(None if held["moving_object_count"][i] is None
                            else float(held["moving_object_count"][i])),
                    top_class=(None if held["detection_top_class"][i] is None
                               else str(held["detection_top_class"][i])))))
        row += batch.num_rows

    height = row
    per_slice: List[Optional[str]] = [None] * height
    per_recording: Dict[str, Verdicts] = {}
    for recording, rows in lines.items():
        rows.sort(key=lambda pair: pair[1].t)
        verdicts = summarise([pair[1] for pair in rows], rates.get(recording))
        per_recording[recording] = verdicts
        for (at, _slice), verdict in zip(rows, verdicts.per_slice):
            per_slice[at] = verdict
    del lines

    if not per_recording:
        return 0

    # The seconds go on the recording's own aggregate row - the one with no slice
    # index - and nowhere else: repeated down every slice they would be summed by
    # something eventually, and a recording's total is not a sum over its slices.
    totals = {name: [None] * height for name in VERDICT_COLUMNS}
    for at in range(height):
        if levels[at] != 0:
            continue
        found = per_recording.get(names[at])
        if not found:
            continue
        totals[FOOTAGE_SECONDS][at] = found.total_seconds
        for kind in VERDICTS:
            totals[seconds_column(kind)][at] = found.seconds.get(kind, 0.0)

    answers = {SLICE_VERDICT: pa.array(per_slice, type=pa.string())}
    answers.update({name: pa.array(values, type=pa.float64())
                    for name, values in totals.items()})
    _rewrite_with(report, source, answers)
    logger.info("%s: %d recordings judged", report.name, len(per_recording))
    return len(per_recording)


def _rewrite_with(report: Path, source, answers: Dict[str, "object"]) -> None:
    """Copy a report through, carrying one more column per answer.

    A judged report is judged again whenever the rules change, so a column that is
    already there is replaced rather than written twice - two columns of the same
    name is a file that reads back as whichever one the reader happens to pick.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    keep = [name for name in source.schema_arrow.names if name not in answers]
    schema = pa.schema([source.schema_arrow.field(name) for name in keep]
                       + [pa.field(name, values.type) for name, values in answers.items()],
                       metadata=source.schema_arrow.metadata)
    beside = report.with_name(report.name + ".judging")
    at, buffered, held = 0, [], 0
    try:
        with pq.ParquetWriter(beside, schema) as writer:
            for batch in source.iter_batches(batch_size=READ_ROWS, columns=keep):
                table = pa.Table.from_arrays(
                    [batch.column(name) for name in keep]
                    + [values.slice(at, batch.num_rows) for values in answers.values()],
                    schema=schema)
                at += batch.num_rows
                buffered.append(table)
                held += table.nbytes
                if held >= ROW_GROUP_BYTES:
                    writer.write_table(pa.concat_tables(buffered))
                    buffered, held = [], 0
            if buffered:
                writer.write_table(pa.concat_tables(buffered))
    except BaseException:
        beside.unlink(missing_ok=True)
        raise
    beside.replace(report)
