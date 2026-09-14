"""One report out of many, and the collection out of those.

    python -m pixel_patrol_deepsea.collect merge EX2107 parts/*.parquet -o EX2107.parquet

Two joins, one after the other. `merge` puts an expedition's recordings into one
report, and `combine` puts the expeditions into the report that spans them.

Both are streamed a row group at a time, because the obvious way to join parquet -
read them all, concatenate, write the pile - needs as much memory as the result is
big, and a report is mostly pictures. That is what killed EX1702 at an 8 GB limit
with 2.9 GB of parts, and GOA2004's 17 GB would have needed a machine nobody has.
Nothing here holds more than one row group at a time, so the memory a join needs
has stopped having anything to do with how much was analysed.

The other thing they both do is refuse to fail on one bad file. A report cut short
by a killed copy is named and skipped rather than thrown from a parquet reader
several minutes in, which says nothing about which file or what to do about it.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

from pixel_patrol_deepsea.catalogue import (
    Expedition, catalogue_path, find_expedition, load_catalogue,
)
from pixel_patrol_deepsea.reports import ROW_GROUP_BYTES

logger = logging.getLogger(__name__)

EVERYTHING = "_everything"


# How the merge is cut on the way through. Rows are read in small batches and
# written in row groups of about this many bytes - by weight rather than by count,
# because a slice row is anything from three kilobytes to half a megabyte depending
# on how many animals were in it, and a row group should be neither a tenth of a
# megabyte nor the whole expedition. This is also the whole of what a merge holds.
MERGE_ROWS = 64

# What makes a report big is the pictures: a slice carries a cached still, a
# close-up of its most convincing animal, and ten frames of clip for each of six
# animals. On the Axial Seamount report that is 190 MB of the 363 MB in the
# collection, and none of it is what a combined report is for.
PICTURE_COLUMNS = ("slice_thumbnail", "detection_crop", "detections")


def merge(expedition_id: str, parts: List[Path], output: Path,
          clips: bool = False) -> int:
    """One parquet per expedition, from one parquet per recording.

    Rows are concatenated rather than recomputed: in pixel-patrol a video file is
    one image, so every level of the aggregation tree in a part already belongs to
    that recording alone and nothing needs re-rolling up.

    The parts are not guaranteed to share a schema: a recording with no detections
    has no detection columns, and a run from a week ago may have fewer of them than
    today's. So the schemas are unified first - by reading the schemas, which costs
    a footer each - and every batch is aligned to that before it is written.

    **The clip frames are left behind**, which is what `clips=False` means and why
    it is the default: they are 84% of a report, and the tile store cuts them again
    from the parts. A report carrying them is the second copy, and the one that has
    to be moved and opened over a network. `clips=True` keeps them, for a collection
    of one dive on a laptop where none of that matters.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    usable = [p for p in parts if p.exists() and p.stat().st_size > 0]
    if not usable:
        print("nothing to merge")
        return 1
    schema = _one_schema(usable)
    expedition = _describe(expedition_id)
    schema = schema.with_metadata({
        **(schema.metadata or {}),
        b"pp_project_name": expedition.title.encode(),
        b"pp_description": (
            f"{expedition.notes} {expedition.archive}, {expedition.vessel}, "
            f"{expedition.date}. {len(usable)} recordings. "
            # Said in the file rather than only on the page, because a report is
            # opened on its own, by people who never saw the page.
            f"Every species name in this report is an automated guess from an "
            f"object detector, not an identification. Footage: "
            f"{expedition.terms.credit} ({expedition.terms.licence})."
        ).strip().encode(),
        b"pp_loader": b"video",
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    rows, held, buffered, left = 0, 0, [], 0
    with pq.ParquetWriter(output, schema) as writer:
        for part in usable:
            # Not pre-buffered: it holds every chunk it reads ahead until the file
            # is closed, which for a part of a tape proxy is most of the part.
            source = pq.ParquetFile(part, pre_buffer=False)
            for batch in source.iter_batches(batch_size=MERGE_ROWS):
                table = _as_schema(pa.Table.from_batches([batch]), schema)
                if not clips:
                    table, dropped = _without_clips(table)
                    left += dropped
                buffered.append(table)
                rows, held = rows + batch.num_rows, held + table.nbytes
                if held >= ROW_GROUP_BYTES:
                    writer.write_table(pa.concat_tables(buffered))
                    buffered, held = [], 0
        if buffered:
            writer.write_table(pa.concat_tables(buffered))
    said = f"{expedition.title}: {len(usable)} recordings, {rows:,} rows"
    if left:
        said += f", {left:,} clip frames left in the parts"
    print(f"{said} -> {output}")
    return 0


# The detector writes its clips into `detections` beside the animals, one entry per
# frame, flagged. They are the frames the gallery animates with and they are most
# of a report's weight.
def _without_clips(table):
    """One batch with the clip frames taken out of its detections.

    The column is JSON, so this is a parse and a dump per row - the same cost the
    tile store already pays to read them, and it happens once per collection rather
    than every time somebody opens a report over a network.
    """
    import json

    import pyarrow as pa

    if "detections" not in table.column_names:
        return table, 0
    kept, dropped = [], 0
    for raw in table.column("detections").to_pylist():
        if not raw:
            kept.append(raw)
            continue
        try:
            animals = json.loads(raw)
        except Exception:
            kept.append(raw)
            continue
        theirs = [a for a in animals if not a.get("clip")]
        dropped += len(animals) - len(theirs)
        kept.append(json.dumps(theirs, separators=(",", ":")) if theirs else None)
    # In the column's own type: polars writes `large_string` and pyarrow writes
    # `string`, and a report can be either.
    at = table.column_names.index("detections")
    field = table.field(at)
    return table.set_column(at, field, pa.array(kept, type=field.type)), dropped


def _one_schema(parts: List[Path]):
    """The schema every part can be written under.

    `promote_options="permissive"` is what `diagonal_relaxed` was doing before: a
    column that is missing from a part is null in its rows, and a column that is an
    int in one and a float in another comes out as the wider of the two.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    schemas = []
    for part in parts:
        try:
            schemas.append(pq.read_schema(part))
        except Exception as exc:
            logger.warning("cannot read the schema of %s: %s", part.name, exc)
    return pa.unify_schemas(schemas, promote_options="permissive")


def _as_schema(table, schema):
    """One batch, under the schema the whole expedition is being written in."""
    import pyarrow as pa

    columns = []
    for field in schema:
        if field.name in table.column_names:
            column = table.column(field.name)
            columns.append(column if column.type.equals(field.type)
                           else column.cast(field.type))
        else:
            columns.append(pa.nulls(table.num_rows, type=field.type))
    return pa.Table.from_arrays(columns, schema=schema)


def _grouped_as(expedition_id: str) -> str:
    """What an expedition is called on an axis.

    Every widget in the combined report groups by this, so it is read sideways
    under a plot forty times over - and the titles are sentences: "Océano Profundo
    2018: Exploring Deep-sea Habitats off Puerto Rico" is sixty-four characters and
    took four fifths of the height of the plot it was labelling.

    The date first, because the question a collection spanning 2000 to 2026 invites
    is which of these is recent, and a label that sorts alphabetically into
    chronological order answers it without a legend. Then the name up to its colon,
    which is where these titles stop naming and start describing.
    """
    import re

    expedition = _describe(expedition_id)
    name = str(expedition.title or expedition_id).split(":")[0].strip()
    when = str(getattr(expedition, "date", "") or "").strip()
    if when:
        # "2004-07 Gulf of Alaska Seamount Expedition 2004" says the year twice.
        name = re.sub(r"[,\s]+(19|20)\d{2}(\s*[-\u2013]\s*(19|20)\d{2})?$", "", name)
    if len(name) > 34:
        name = name[:33].rstrip() + "\u2026"
    return f"{when} {name}".strip() if when else name


def _describe(expedition_id: str) -> Expedition:
    """What the catalogue says about this expedition, or the little we know.

    `LookupError` only: an expedition nobody has catalogued still gets a report,
    but a catalogue that will not parse is a thing to hear about rather than to
    quietly title after its own id.
    """
    try:
        return find_expedition(load_catalogue(catalogue_path()), expedition_id)
    except LookupError:
        return Expedition(id=expedition_id, listing="", name=expedition_id)



def combine(root: Path) -> Optional[Path]:
    """One report over every expedition, for the questions that span them.

    "Two dives in the same canyon ten years apart" is not a question any single
    expedition's report can answer, and it is the reason the reports carry a
    position and a clock at all. So the collection gets a report of its own.

    Without the pictures, deliberately. A combined report is read for counts,
    taxa, positions and times - what was found, where, and when - and those are a
    few megabytes across the whole collection where the images are hundreds. The
    per-expedition reports keep every still and every clip; this one keeps every
    row. A browser asked to open 363 MB to draw a sunburst is a browser that does
    not open.
    """
    import polars as pl
    import pyarrow as pq_mod  # noqa: F401
    import pyarrow.parquet as pq

    parquets = sorted(p for p in (root / "parquet").glob("*.parquet")
                      if p.stem != EVERYTHING)
    if len(parquets) < 2:
        return None
    frames, names = [], []
    for path in parquets:
        # Read without the pictures rather than reading them and dropping them.
        # They are most of the bytes - a 1.1 GB expedition report is mostly cached
        # stills - and this step holds every expedition at once, so materialising
        # them to discard them a line later is what made the page build need more
        # memory than the analysis did.
        try:
            wanted = [c for c in pl.read_parquet_schema(path) if c not in PICTURE_COLUMNS]
            table = pl.read_parquet(path, columns=wanted)
        except Exception as exc:
            # One report nobody can read is one expedition missing from the
            # combined one. It is not a reason to build no page at all - and the
            # usual cause is a copy that was interrupted, which says so here
            # rather than as a traceback from a parquet reader.
            logger.warning("skipping %s: %s", path.name, _why_unreadable(path, exc))
            continue
        # Which expedition a row came from has to survive the concatenation, or
        # the combined report can group by everything except the thing a reader
        # most wants to group by.
        table = table.with_columns(pl.lit(_grouped_as(path.stem)).alias("expedition"))
        frames.append(table)
        names.append(path.stem)
    together = pl.concat(frames, how="diagonal_relaxed")
    arrow = together.to_arrow()
    arrow = arrow.replace_schema_metadata({
        **(arrow.schema.metadata or {}),
        b"pp_project_name": b"Every expedition together",
        b"pp_description": (
            f"All {len(names)} expeditions in one report: {', '.join(names)}. "
            "Cached stills, close-ups and clips are left out - they are hundreds of "
            "megabytes and a combined report is read for counts, taxa, positions and "
            "times. Open an expedition's own report for the pictures.").encode(),
        b"pp_loader": b"video",
    })
    output = root / "parquet" / f"{EVERYTHING}.parquet"
    pq.write_table(arrow, output)
    print(f"everything together: {len(names)} expeditions, {len(together):,} rows, "
          f"{output.stat().st_size / 1e6:.0f} MB -> {output}")
    return output


def _why_unreadable(path: Path, exc: Exception) -> str:
    """Say what is wrong with a parquet in the words of the thing that broke it.

    A parquet ends with the same four bytes it starts with, so a file that does not
    is one whose writing stopped early - which on a cluster means a `publishDir`
    copy that was interrupted, and the fix is to merge that expedition again rather
    than to read anything about parquet specifications.
    """
    try:
        with path.open("rb") as file:
            file.seek(-4, 2)
            if file.read(4) != b"PAR1":
                size = path.stat().st_size / 1e9
                return (f"the file is cut short ({size:.2f} GB and no footer) - "
                        f"writing it was interrupted. Merge {path.stem} again.")
    except Exception:
        pass
    return str(exc)


def unreadable(root: Path) -> List[Path]:
    """The reports in a collection that no reader is going to get through.

    Checked before anything long starts, because the alternative is finding out
    from a traceback several minutes into a site build.
    """
    broken = []
    for path in sorted((root / "parquet").glob("*.parquet")):
        try:
            with path.open("rb") as file:
                if file.read(4) != b"PAR1":
                    broken.append(path)
                    continue
                file.seek(-4, 2)
                if file.read(4) != b"PAR1":
                    broken.append(path)
        except Exception:
            broken.append(path)
    return broken


