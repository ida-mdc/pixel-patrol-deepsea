"""Which detections are the same animal, decided once and written into the report.

A detection is one box in one frame. The thing a person counts is an animal, and
`refine.track_sightings` is what turns the first into the second. That answer used
to exist only while the collection page was being built: the page linked the
detections itself, the viewer grouped slices its own way in the browser, and the
parquet - the artifact the cluster actually produces, and the one anybody else
would open - carried no notion of an individual at all. Three consumers, three
answers, none of them written down.

So it is decided here, once, by `collect one`, which is the only thing that holds a
whole recording at a time. Each detection in `detections` gains an `animal`: an
integer, unique within its recording, shared by every detection of that individual
and by the clip frames cut for it. Grouping a report by `(name, animal)` is then
the same operation for the page, for a notebook and for anything written later.

Two things it deliberately does not do.

**No per-slice count of animals.** It would be the obvious column and it does not
survive the aggregation tree: a row is a slice, a slice is a leaf under a
recording, and every metric on it rolls up along image dimensions. An animal is
not one of those - one individual spans twenty slices - so a per-slice count
cannot be summed into a per-recording count without counting most animals many
times. The ids let a reader do that grouping correctly; a column would invite the
wrong sum.

**No renumbering across recordings.** Tracking is per recording, because nothing
links an animal that leaves one tape to one that enters another. So identity is
the pair, not the number: `(name, animal)`, and a merged expedition report holds
`animal = 0` once per recording.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from pixel_patrol_deepsea.refine import Sighting, Track, name_tracks, track_sightings

logger = logging.getLogger(__name__)

ANIMAL = "animal"
# What the animal's looks agreed it was, and how much of their confidence backed
# it. Written beside the id because it is decided from the whole track and a
# reader holding one detection cannot work it out: a detection says `Keratoisis`,
# and whether that animal is a Keratoisis or one of the 23% whose looks disagreed
# is a property of the other detections it belongs with. See `refine.resolve_taxon`.
ANIMAL_TAXON = "animal_taxon"
ANIMAL_AGREEMENT = "animal_agreement"


def _recording_column(columns: Sequence[str]) -> str:
    return "child_id" if "child_id" in columns else "name"


def _sightings(rows: Sequence[Optional[str]], names: Sequence, slices: Sequence
               ) -> Tuple[List[Sighting], List[Tuple[int, int]], Dict[int, list]]:
    """Every real detection in a report, and where each one came from.

    The positions are the point: a sighting has to be written back to the exact
    entry it was read from, and the entries are inside a JSON string in a column.
    """
    found: List[Sighting] = []
    where: List[Tuple[int, int]] = []
    parsed: Dict[int, list] = {}
    for index, blob in enumerate(rows):
        if not blob or slices[index] is None:
            continue
        try:
            animals = json.loads(blob)
        except Exception:
            continue
        parsed[index] = animals
        recording = Path(str(names[index] or "")).stem
        for position, animal in enumerate(animals):
            if animal.get("clip"):
                continue
            found.append(Sighting(
                recording=recording,
                second=float(animal.get("second") or 0.0),
                frame=int(animal.get("frame") or 0),
                slice_t=int(slices[index]),
                taxon=str(animal.get("class") or "?"),
                confidence=float(animal.get("conf") or 0.0),
                box=tuple(animal.get("box") or (0, 0, 0, 0)),
                crop=None))
            where.append((index, position))
    return found, where, parsed


def identify(report: Path) -> int:
    """Link a report's detections into individuals and write the ids back into it.

    Returns how many animals the report turned out to hold. Rewriting the file is
    done through arrow rather than polars' own writer so the `pp_*` metadata the
    pipeline put on it - the project name, the description, the loader - survives;
    a report that loses those opens as an untitled table.
    """
    import polars as pl
    import pyarrow.parquet as pq

    table = pl.read_parquet(report)
    if "detections" not in table.columns or "dim_t" not in table.columns:
        return 0
    column = _recording_column(table.columns)
    blobs = table.get_column("detections").to_list()
    found, where, parsed = _sightings(blobs, table.get_column(column).to_list(),
                                      table.get_column("dim_t").to_list())
    if not found:
        return 0

    tracks = track_sightings(found)
    position_of = {id(sighting): index for index, sighting in enumerate(found)}
    # Numbered within the recording, not within the file. A merged expedition
    # report is a concatenation of parts that were each numbered on their own, so
    # numbering globally here would mean a part and the merged report it ends up
    # in disagree about what `animal = 3` is. Per recording, they cannot.
    counters: Dict[str, int] = {}
    for track in tracks:
        number = counters.get(track.recording, 0)
        counters[track.recording] = number + 1
        for sighting in track.sightings:
            row, entry = where[position_of[id(sighting)]]
            parsed[row][entry][ANIMAL] = number
            parsed[row][entry][ANIMAL_TAXON] = track.taxon
            parsed[row][entry][ANIMAL_AGREEMENT] = round(track.agreement, 3)

    # A clip is several frames cut around one animal with a single box, and it says
    # which animal by its index within the slice. It gets the same id, so the film
    # and the individual it is of are joined by the same key as everything else.
    for row, animals in parsed.items():
        real = [a for a in animals if not a.get("clip")]
        for animal in animals:
            if not animal.get("clip"):
                continue
            of = animal.get("of")
            if isinstance(of, int) and 0 <= of < len(real) and ANIMAL in real[of]:
                for field in (ANIMAL, ANIMAL_TAXON, ANIMAL_AGREEMENT):
                    if field in real[of]:
                        animal[field] = real[of][field]

    rewritten = [json.dumps(parsed[i], separators=(",", ":")) if i in parsed else blob
                 for i, blob in enumerate(blobs)]
    table = table.with_columns(pl.Series("detections", rewritten))
    arrow = table.to_arrow()
    arrow = arrow.replace_schema_metadata({
        **(pq.read_schema(report).metadata or {}),
        **(arrow.schema.metadata or {}),
    })
    pq.write_table(arrow, report)
    logger.info("%s: %d detections are %d animals", report.name, len(found), len(tracks))
    return len(tracks)


def has_ids(rows: Sequence[Optional[str]]) -> bool:
    """Whether a report was written with identity in it, or predates this.

    Any tagged detection is enough, and it has to be: a report holds one
    aggregate row per recording whose `detections` is a summary of the rows
    beneath it rather than a sighting, and that one is deliberately left
    untagged - an id on it would be a second copy of an animal already counted
    on the slice it was actually seen in. It also happens to come first in the
    file, so asking only the first detection answered the wrong question.
    """
    for blob in rows:
        if not blob:
            continue
        try:
            animals = json.loads(blob)
        except Exception:
            continue
        if any(ANIMAL in animal for animal in animals if not animal.get("clip")):
            return True
    return False


def animals_from(sightings: Sequence[Sighting], ids: Sequence[int],
                 names: Optional[Sequence] = None,
                 agreements: Optional[Sequence] = None) -> List[Track]:
    """The tracks a report already decided on, rebuilt from the stored ids.

    Same shape as `track_sightings` returns, so a caller cannot tell which of the
    two it got - except that this one costs a dictionary rather than a pass over
    every detection, and gives the same answer every time it is asked.
    """
    grouped: Dict[Tuple[str, int], List[Sighting]] = {}
    named: Dict[Tuple[str, int], Tuple[str, float]] = {}
    for index, (sighting, number) in enumerate(zip(sightings, ids)):
        key = (sighting.recording, int(number))
        grouped.setdefault(key, []).append(sighting)
        if names is not None and index < len(names) and names[index]:
            agreement = 1.0
            if agreements is not None and index < len(agreements):
                try:
                    agreement = float(agreements[index])
                except (TypeError, ValueError):
                    agreement = 1.0
            named[key] = (str(names[index]), agreement)
    tracks = []
    for key, mine in grouped.items():
        recording, _number = key
        mine.sort(key=lambda s: s.second)
        # The name the report settled on, where it says. Deriving it again here
        # would be a second opinion on a question already answered, and the two
        # would disagree the moment the rule changed.
        if key in named:
            taxon, agreement = named[key]
        else:
            taxon, agreement = max(mine, key=lambda s: s.confidence).taxon, 1.0
        tracks.append(Track(recording=recording, taxon=taxon,
                            first_second=mine[0].second, last_second=mine[-1].second,
                            sightings=mine, agreement=agreement))
    return tracks
