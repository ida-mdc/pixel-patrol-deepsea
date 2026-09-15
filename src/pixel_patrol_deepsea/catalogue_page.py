"""The page over a whole collection: expeditions, progress, and what was found.

`reports.py` reads one report. This is the page over all of them - which expeditions are we collecting, how far through each one are we, and is
there anything in what has been done so far. It reads three things:

    manifests/<id>.json    what listing the expedition found, so progress has a
                           denominator even before anything is processed
    parts/<id>/*.parquet   one per recording analysed, so progress has a numerator
    parquet/<id>.parquet   the merged expedition report the links point at

Every link is a `?data=` URL into the static viewer beside it, so opening a report
needs a file server and nothing else - no Python, no port, no viewer process.
"""

import html
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from pixel_patrol_deepsea.catalogue import catalogue_path, load_catalogue

# What a deep-sea report does not open on. The viewer reads `hidden=` as a
# dot-separated list of widget ids, so this decides what a reader lands on and
# nothing more: every one of them is still listed in the sidebar, one click away.
# Choosing it here, on the links this page writes, is what keeps the preference out
# of pixel-patrol's viewer - none of these widgets is wrong, they are the general
# ones, and a report of deep-sea footage opens better on the ones that know what
# the footage is.
HIDDEN_WIDGETS = (
    "custom-plot",               # an empty plot for a reader to build, not to land on
    "file-stats",                # bytes and extensions of files that are all one format
    "image-table",               # the table the other widgets exist to save reading
    "metadata",                  # the loader's own fields, already in the header line
    "stats-across-dims-basic",   # generic intensity statistics across dims
    "sunburst",                  # a file-and-folder ring: an expedition is one directory
    "violin-basic",
)


def data_at(base: str, path: str) -> str:
    """Where a heavy file lives: beside the page, or wherever it was put.

    A collection is fifteen gigabytes of pictures and reports and a hundred
    kilobytes of page. Those do not want the same home: the page is a file in a
    repository, and the rest belongs on storage that charges nothing to hold it.
    So the store and the reports can be addressed absolutely, and the default -
    everything in one folder, served together - is the empty string.

    The relative form is `../`-prefixed because the thing resolving it is the
    viewer, which lives one directory down from the page. An absolute base has no
    such question to answer.
    """
    return f"{base.rstrip('/')}/{path}" if base.strip() else f"../{path}"


def tiles_at(base: str) -> str:
    """Where the tile store is, as the page itself resolves it - from the page's
    own directory, not the viewer's."""
    return f"{base.rstrip('/')}/tiles" if base.strip() else "tiles"


def _report_url(parquet: str, group: str = "") -> str:
    """A link into the static viewer beside this page, opened on what is worth reading.

    `group` is the column the viewer splits every widget by. Worth setting on the
    combined report and on nothing else: one expedition's report has no column that
    tells its rows apart that way, and the combined one has `expedition`, which is
    the whole reason a reader opens it.
    """
    url = f"viewer/index.html?data={parquet}&hidden={'.'.join(HIDDEN_WIDGETS)}"
    return f"{url}&group={group}" if group else url


@dataclass
class Progress:
    """How far one expedition has got, and what came out of it."""
    id: str
    title: str
    archive: str = ""
    vessel: str = ""
    date: str = ""
    notes: str = ""
    link: str = ""      # the expedition's own page, for credit and for context
    listed: int = 0
    processed: int = 0
    recordings: int = 0      # distinct recordings inside the merged report
    seconds: float = 0.0
    sightings: int = 0
    animals: int = 0
    vehicle: int = 0         # tracks that turned out to be the ROV's own hardware
    stills: int = 0
    slices: int = 0
    placed: Optional[object] = None   # where and when, from reports.Placed
    scores: List[Dict] = field(default_factory=list)   # against per-frame ground truth
    scored: int = 0          # slices a detector looked at
    with_animals: int = 0    # ...and found something in
    taxa: List[str] = field(default_factory=list)
    report: Optional[Path] = None
    listed_at: str = ""

    @property
    def share(self) -> float:
        return self.processed / self.listed if self.listed else 0.0


def read_progress(root: Path) -> List[Progress]:
    """One row per expedition in the catalogue, plus any found only on disk."""
    catalogue = {e.id: e for e in load_catalogue(catalogue_path())}
    seen = set(catalogue)
    for folder in sorted((root / "parts").glob("*")):
        if folder.is_dir():
            seen.add(folder.name)
    rows = []
    for expedition_id in sorted(seen):
        entry = catalogue.get(expedition_id)
        rows.append(_progress_of(root, expedition_id, entry))
    return rows


# What the expedition table is, as a file. Reading it out of the reports means
# reading 4.7 GB of parquet, which is a minute on this machine and impossible on a
# runner with no collection on it. Written beside the page by `collect site`, it is
# fifty kilobytes and the page can be rebuilt from it alone.
PROGRESS = "collection.json"


def save_progress(rows: List[Progress], where: Path) -> Path:
    """The expedition table, small enough to keep."""
    import json
    from dataclasses import asdict

    def plain(row: Progress) -> Dict:
        out = asdict(row)
        out["report"] = row.report.name if row.report else None
        out["placed"] = asdict(row.placed) if row.placed is not None else None
        return out

    where.write_text(json.dumps([plain(r) for r in rows], indent=1, default=str))
    return where


def load_progress(where: Path) -> List[Progress]:
    """...and back, for a build that has the page but not the collection."""
    import json

    from pixel_patrol_deepsea.reports import Placed

    rows = []
    for raw in json.loads(where.read_text()):
        placed = raw.pop("placed", None)
        row = Progress(**{k: v for k, v in raw.items() if k != "report"})
        row.report = Path(raw["report"]) if raw.get("report") else None
        row.placed = Placed(**placed) if placed else None
        rows.append(row)
    return rows


def _progress_of(root: Path, expedition_id: str, entry) -> Progress:
    row = Progress(id=expedition_id,
                   title=(entry.title if entry else expedition_id),
                   archive=(entry.archive if entry else ""),
                   vessel=(entry.vessel if entry else ""),
                   date=(entry.date if entry else ""),
                   notes=(entry.notes if entry else ""),
                   link=(entry.link if entry else ""))
    manifest = root / "manifests" / f"{expedition_id}.json"
    if manifest.is_file():
        try:
            found = json.loads(manifest.read_text())
            row.listed = len(found.get("videos", []))
            row.listed_at = found.get("listed_at", "")
        except Exception:
            pass
    report = root / "parquet" / f"{expedition_id}.parquet"
    if report.is_file():
        try:
            _read_report(report, row)
            row.report = report
        except Exception as exc:
            # A report that cannot be read is an expedition with no report, which
            # this page has always known how to say. Dying here would mean no page
            # for any of the others.
            logging.getLogger(__name__).warning(
                "cannot read %s, leaving it out: %s", report.name, exc)
    # How many recordings are in the report, not how many part files happen to sit
    # next to it: the report is the artefact, and a scheduler that runs the page in
    # its own directory will have the report there and not the parts.
    row.processed = row.recordings or len(list((root / "parts" / expedition_id).glob("*.parquet")))
    row.scores = _read_scores(root / "scores" / f"{expedition_id}.json")
    return row


def _read_scores(path: Path) -> List[Dict]:
    """What `collect score` measured, if this expedition has ground truth."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


@dataclass
class _Clip:
    """A run of frames cut with one animal's box, and that box."""
    box: tuple
    frames: List = field(default_factory=list)

    @property
    def crops(self) -> List[bytes]:
        return [crop for _second, crop in self.frames]


ITS_OWN_CLIP = 0.3

# Rows read from a report at a time. A slice row carries its pictures, so this is
# tens of megabytes rather than a number of rows worth tuning.
READ_ROWS = 64


def _overlap(one, other) -> float:
    """Intersection over union of two boxes, 0 when they do not touch."""
    if len(one) != 4 or len(other) != 4:
        return 0.0
    ax0, ay0, ax1, ay1 = (float(v) for v in one)
    bx0, by0, bx1, by1 = (float(v) for v in other)
    wide = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    high = max(0.0, min(ay1, by1) - max(ay0, by0))
    both = wide * high
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - both
    return both / union if union > 0 else 0.0


def animals_by_recording(report: Path, pictures: bool = True):
    """Every animal in a report, one recording at a time.

    A recording's rows are contiguous - a merged report is its parts, concatenated,
    and a part is one recording - so the reading can stop at each boundary, link
    that recording's sightings into animals, hand them over and let them go. An
    expedition is then never in memory; one recording is, and the largest of those
    is a couple of hundred megabytes.

    Linking is per recording anyway: `animals_from` groups on `(recording, id)` and
    `track_sightings` never joins two recordings, so this yields exactly what
    reading the whole report and linking it at the end does.
    """
    import json

    import pyarrow.parquet as pq

    from pixel_patrol_deepsea.identity import (
        ANIMAL, ANIMAL_AGREEMENT, ANIMAL_TAXON, animals_from)
    from pixel_patrol_deepsea.refine import Sighting, track_sightings

    # Three columns out of sixty, and none of them the cached stills: the animals
    # are all inside `detections`, which is most of the report's weight on its own.
    try:
        # `pre_buffer` defaults to reading column chunks ahead and holding them for
        # as long as the file is open, so a report read start to finish ends up
        # entirely in memory however small the batches are: EX1702 came to 3.3 GB
        # of arrow buffers for a loop that dropped every batch it was handed. Off,
        # the same loop peaks at 475 MB.
        source = pq.ParquetFile(report, pre_buffer=False)
    except Exception as exc:
        logging.getLogger(__name__).warning("cannot open %s: %s", report.name, exc)
        return
    available = set(source.schema_arrow.names)
    if "detections" not in available:
        return
    wanted = [c for c in ("detections", "dim_t", "child_id", "name",
                          # Where and when the slice was filmed. A crop of a frame
                          # says nothing about either, and they are the two things
                          # anybody asks about a deep-sea picture.
                          "recorded_at", "depth_m") if c in available]
    names = "child_id" if "child_id" in wanted else "name"

    held, seen = _Recording(pictures=pictures), set()
    for batch in source.iter_batches(batch_size=READ_ROWS, columns=wanted):
        for row in batch.to_pylist():
            if row.get("detections") is None or row.get("dim_t") is None:
                continue
            where = str(row.get(names) or row.get("name") or "")
            if where != held.name:
                if held.name is not None:
                    yield held.finish()
                    seen.add(held.name)
                if where in seen:
                    # Not fatal, but it means this report was not written as parts
                    # in order, and one animal either side of the boundary is going
                    # to be counted as two.
                    logging.getLogger(__name__).warning(
                        "%s: %s comes back after another recording", report.name, where)
                held = _Recording(where, pictures=pictures)
            held.add(row)
    if held.name is not None:
        yield held.finish()


class _Recording:
    """One recording's sightings and clips, until the rows move on to the next."""

    def __init__(self, name=None, pictures=True):
        self.name = name
        # A caller counting animals wants none of the crops, and decoding every
        # one of them is most of what reading a report costs.
        self.pictures = pictures
        self.sightings, self.clips = [], {}
        self.stored, self.settled, self.backing = [], [], []
        # slice -> when it was filmed and how deep, for the moments that have it
        self.moments = {}

    def add(self, row):
        import json

        from pixel_patrol_deepsea.identity import ANIMAL, ANIMAL_AGREEMENT, ANIMAL_TAXON
        from pixel_patrol_deepsea.refine import Sighting

        try:
            animals = json.loads(row["detections"])
        except Exception:
            return
        key = (self.name, int(row["dim_t"]))
        when, deep = row.get("recorded_at"), row.get("depth_m")
        if when or deep is not None:
            self.moments[key[1]] = (str(when) if when else "",
                                    None if deep is None else round(float(deep), 1))
        for animal in animals:
            if animal.get("clip") and not self.pictures:
                continue          # a clip is a picture and nothing else
            crop = _decode(animal.get("crop")) if self.pictures else None
            if animal.get("clip"):
                if crop:
                    mine = (*key, animal.get("of", 0))
                    self.clips.setdefault(
                        mine, _Clip(tuple(animal.get("box") or ()))).frames.append(
                            (animal.get("second") or 0.0, crop))
                continue
            self.sightings.append(Sighting(
                recording=self.name,
                second=float(animal.get("second") or 0.0),
                frame=int(animal.get("frame") or 0),
                slice_t=int(row["dim_t"]),
                taxon=str(animal.get("class") or "?"),
                confidence=float(animal.get("conf") or 0.0),
                box=tuple(animal.get("box") or (0, 0, 0, 0)),
                crop=crop,
            ))
            self.stored.append(animal.get(ANIMAL))
            self.settled.append(animal.get(ANIMAL_TAXON))
            self.backing.append(animal.get(ANIMAL_AGREEMENT))

    def finish(self):
        from pixel_patrol_deepsea.identity import animals_from
        from pixel_patrol_deepsea.refine import track_sightings

        for clip in self.clips.values():
            clip.frames.sort()
        # The report says which detections are one animal, where it was written by
        # a version that knew. Deriving it again here would be a second opinion on
        # a question already answered.
        if self.sightings and all(number is not None for number in self.stored):
            tracks = animals_from(self.sightings, self.stored, self.settled, self.backing)
        elif self.sightings:
            tracks = track_sightings(self.sightings)
        else:
            tracks = []
        return self.name, tracks, self.clips, self.moments


def _frame_size(report: Path) -> tuple:
    """Frame width and height, so a box can be told it is against the edge."""
    import polars as pl

    for wide, high in (("X_size", "Y_size"), ("size_X", "size_Y")):
        try:
            table = pl.read_parquet(report, columns=[wide, high])
            return int(table[wide][0]), int(table[high][0])
        except Exception:
            continue
    return 640, 360


def _sources_from_manifest(report: Path, summary) -> List[Dict[str, str]]:
    """The URLs the manifest listed, for the recordings that got analysed.

    A recording staged to a scratch directory and deleted leaves no source_url on
    the report, but the manifest that named it is still sitting next to the report
    and it holds the address. Matched on the recording name, which is what the
    parquet carries either way.
    """
    manifest = report.parent.parent / "manifests" / f"{report.stem}.json"
    if not manifest.is_file():
        return []
    try:
        listed = json.loads(manifest.read_text()).get("videos", [])
    except Exception:
        return []
    analysed = {Path(name).stem for name in summary.names}
    # Prefix rather than equality: a report from an older run carries the name of a
    # transcoded copy, `..._ROVHD_Low_10fps`, and the recording it came from is
    # still the one the manifest listed.
    return [{"url": url, "label": Path(url.split("?")[0]).name}
            for url in listed
            if any(name.startswith(Path(url.split("?")[0]).stem) for name in analysed)]


def _decode(encoded):
    import base64
    try:
        return base64.b64decode(encoded) if encoded else None
    except Exception:
        return None


def _read_report(report: Path, row: Progress) -> None:
    from pixel_patrol_deepsea.reports import summarise

    summary = summarise(report)
    if summary is None:
        return
    row.seconds = summary.seconds
    row.taxa = sorted(summary.taxa)
    row.scored = summary.scored
    row.with_animals = summary.with_animals
    row.recordings = summary.recordings
    row.stills = summary.stills
    row.slices = summary.slices
    row.placed = summary.placed
    # Animals straight out of the report, where the detector left them - minus the
    # ROV's own arm, which a fish detector reports as a fish for as long as it is
    # deployed and which was the largest "animal" on this page.
    #
    # Counted as they go past rather than collected: this used to hold every track
    # and every clip frame of every expedition on the row, which on a collection
    # this size is the page build reading - and keeping - thirty gigabytes to print
    # four numbers per expedition. Nothing ever read them back.
    from pixel_patrol_deepsea.refine import is_an_animal, looks_like_vehicle

    width, height = _frame_size(report)
    animals = vehicle = sightings = 0
    taxa = set()
    for _recording, tracks, _clips, _when in animals_by_recording(report, pictures=False):
        for track in tracks:
            if not is_an_animal(track.taxon) or looks_like_vehicle(track, width, height):
                vehicle += 1
                continue
            animals += 1
            sightings += track.frames
            taxa.add(track.taxon)
    if animals or vehicle:
        row.vehicle = vehicle
        row.animals = animals
        row.sightings = sightings
        row.taxa = sorted(taxa)
    sightings = report.parent.parent / "sightings" / f"{row.id}.parquet"
    if sightings.is_file():
        try:
            import polars as pl

            from pixel_patrol_deepsea.reports import animals_in_sightings
            rows = pl.read_parquet(sightings)
            row.sightings = len(rows)
            row.animals = len(animals_in_sightings(rows))
            row.taxa = sorted(set(row.taxa) | set(rows["taxon"].unique().to_list()))
        except Exception:
            pass


def write_assets(root: Path) -> Path:
    """The drawing the landing page opens on, copied beside it.

    Shipped with the package rather than fetched, because a collection served from
    a folder with no network is the normal case - a cluster's scratch, a laptop on
    a ship - and a hero image that 404s is worse than none.
    """
    import shutil

    source = Path(__file__).parent / "assets" / "pixel-patrol-deepsea.png"
    if not source.is_file():
        return root
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, assets / source.name)
    return assets


def write_catalogue_page(root: Path, output: Optional[Path] = None,
                         index: Optional[Dict] = None, data_url: str = "") -> Path:
    """The landing page, over the tile store if one was written.

    The store is read back from disk when it was not just built, so writing the
    page on its own - after an edit to it, say - still shows everything the last
    build found rather than an empty wall.

    `data_url` is where the store and the reports will be served from, for a
    collection whose page and whose gigabytes do not live in the same place.
    """
    import json

    output = output or root / "index.html"
    if index is None:
        beside = root / "tiles" / "index.json"
        if beside.is_file():
            try:
                index = json.loads(beside.read_text())
            except Exception:
                index = None
    # From the reports where they are, from the file they were saved to where they
    # are not - which is how a runner with neither builds the same page.
    beside = root / PROGRESS
    rows = load_progress(beside) if beside.is_file() and not (root / "parquet").is_dir() \
        else read_progress(root)
    if not beside.is_file() or (root / "parquet").is_dir():
        save_progress(rows, beside)
    output.write_text(render(rows, index, data_url=data_url))
    return output


# The detector writes down anything it half-suspects, because a box it never wrote
# down is one nothing downstream can recover. The wall is the other end of that
# bargain: it shows the ones worth a person's glance and says how many it held back.
# Same floor the report's own slider starts at.
def render(rows: List[Progress], index: Optional[Dict] = None,
           data_url: str = "") -> str:
    """The landing page, over whatever the tile store holds."""
    from pixel_patrol_deepsea.landing import render as landing

    return landing(rows, index, data_url=data_url)


def _ratio(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "&ndash;"


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}" if total else "&ndash;"


def _link(row: Progress, data_url: str = "") -> str:
    if row.report is None:
        return '<span class="muted small">no report yet</span>'
    target = _report_url(data_at(data_url, f"parquet/{row.id}.parquet"))
    return f'<a class="open" href="{html.escape(target, quote=True)}">Open report &rarr;</a>'


