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

import functools
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
    "summary",
    "sunburst",                  # a file-and-folder ring: an expedition is one directory
    "violin-basic",
)


def _report_url(parquet: str) -> str:
    """A link into the static viewer beside this page, opened on what is worth reading."""
    return f"viewer/index.html?data={parquet}&hidden={'.'.join(HIDDEN_WIDGETS)}"


@dataclass
class Progress:
    """How far one expedition has got, and what came out of it."""
    id: str
    title: str
    archive: str = ""
    vessel: str = ""
    date: str = ""
    notes: str = ""
    listed: int = 0
    processed: int = 0
    recordings: int = 0      # distinct recordings inside the merged report
    seconds: float = 0.0
    sightings: int = 0
    animals: int = 0
    tracks: List = field(default_factory=list)   # one per animal, with its crops
    clips: Dict = field(default_factory=dict)    # (recording, slice) -> steady frames
    vehicle: int = 0         # tracks that turned out to be the ROV's own hardware
    best: Dict[str, object] = field(default_factory=dict)   # taxon -> best look at it
    stills: int = 0
    slices: int = 0
    sources: List[Dict[str, str]] = field(default_factory=list)
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


def _progress_of(root: Path, expedition_id: str, entry) -> Progress:
    row = Progress(id=expedition_id,
                   title=(entry.title if entry else expedition_id),
                   archive=(entry.archive if entry else ""),
                   vessel=(entry.vessel if entry else ""),
                   date=(entry.date if entry else ""),
                   notes=(entry.notes if entry else ""))
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
        row.report = report
        _read_report(report, row)
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


def read_animals(report: Path):
    """Every animal in a report, and the steady clip belonging to each slice.

    The detector writes one entry per detection into `detections`, each with its own
    crop, plus a clip: consecutive frames cropped with one box, which is the thing
    worth animating. This needs no second pass over the footage - it reads the report
    and links the detections into individuals the same way the refine pass does.

    Clip frames are deliberately kept out of the tracking. They are crops of frames
    the model never looked at, so counting them as sightings would inflate every
    number on the page by however many frames a clip happens to be.

    Clips are returned keyed by recording, slice and animal, because a slice holds
    as many clips as it held animals. Keying them by slice alone handed every animal
    on a crowded seabed the same film.
    """
    import json

    import polars as pl

    from pixel_patrol_deepsea.identity import (
        ANIMAL, ANIMAL_AGREEMENT, ANIMAL_TAXON, animals_from, has_ids)
    from pixel_patrol_deepsea.refine import Sighting, track_sightings

    # Three columns out of sixty, and none of them the cached stills: this is
    # called once per expedition on reports where the pictures are most of the
    # gigabyte, and the animals are all inside `detections`.
    available = pl.read_parquet_schema(report)
    if "detections" not in available:
        return [], {}
    wanted = [c for c in ("detections", "dim_t", "child_id", "name") if c in available]
    table = pl.read_parquet(report, columns=wanted)
    slices = table.filter(pl.col("detections").is_not_null()
                          & pl.col("dim_t").is_not_null())
    recording = "child_id" if "child_id" in slices.columns else "name"
    sightings, clips, stored, settled, backing = [], {}, [], [], []
    for row in slices.iter_rows(named=True):
        try:
            animals = json.loads(row["detections"])
        except Exception:
            continue
        where = str(row.get(recording) or row.get("name") or "")
        key = (where, int(row["dim_t"]))
        for animal in animals:
            crop = _decode(animal.get("crop"))
            if animal.get("clip"):
                if crop:
                    mine = (*key, animal.get("of", 0))
                    clips.setdefault(mine, _Clip(tuple(animal.get("box") or ()))).frames.append(
                        (animal.get("second") or 0.0, crop))
                continue
            sightings.append(Sighting(
                recording=where,
                second=float(animal.get("second") or 0.0),
                frame=int(animal.get("frame") or 0),
                slice_t=int(row["dim_t"]),
                taxon=str(animal.get("class") or "?"),
                confidence=float(animal.get("conf") or 0.0),
                box=tuple(animal.get("box") or (0, 0, 0, 0)),
                crop=crop,
            ))
            stored.append(animal.get(ANIMAL))
            settled.append(animal.get(ANIMAL_TAXON))
            backing.append(animal.get(ANIMAL_AGREEMENT))
    for clip in clips.values():
        clip.frames.sort()
    # The report says which detections are one animal, where it was written by a
    # version that knew. Deriving it again here would be a second opinion on a
    # question already answered - and the page would disagree with the file it is
    # made from the moment either rule changed.
    if sightings and all(number is not None for number in stored):
        return animals_from(sightings, stored, settled, backing), clips
    if sightings:
        logging.getLogger(__name__).info(
            "%s predates stored animal ids; linking them here instead. "
            "`collect identify` writes them in.", report.name)
    return track_sightings(sightings), clips


def animals_in_report(report: Path):
    """Just the animals, for callers that do not need the clips."""
    return read_animals(report)[0]


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


def _sources_of(report: Path, summary) -> List[Dict[str, str]]:
    """Where this expedition's recordings came from.

    The report knows, where the loader wrote a source_url onto each record - which
    is the case for anything read out of a manifest. Otherwise fall back to the
    sources.json a fetch script leaves behind.
    """
    import polars as pl

    from pixel_patrol_deepsea.reports import read_sources

    try:
        table = pl.read_parquet(report, columns=["source_url"])
        urls = sorted({u for u in table["source_url"].drop_nulls().to_list() if u})
    except Exception:
        urls = []
    if urls:
        return [{"url": url, "label": Path(url.split("?")[0]).name} for url in urls]
    noted = read_sources(report.parent.parent)
    if noted:
        return [{"url": entry["url"], "label": entry.get("cited_as") or name}
                for name, entry in sorted(noted.items()) if name in summary.names]
    return _sources_from_manifest(report, summary)


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
    row.best = dict(summary.taxa)
    row.sources = _sources_of(report, summary)
    row.placed = summary.placed
    # Animals straight out of the report, where the detector left them - minus the
    # ROV's own arm, which a fish detector reports as a fish for as long as it is
    # deployed and which was the largest "animal" on this page.
    from pixel_patrol_deepsea.refine import is_an_animal, looks_like_vehicle

    width, height = _frame_size(report)
    tracks, row.clips = read_animals(report)
    if tracks:
        hardware = [t for t in tracks
                    if not is_an_animal(t.taxon) or looks_like_vehicle(t, width, height)]
        animals = [t for t in tracks if t not in hardware]
        row.tracks = animals
        row.vehicle = len(hardware)
        row.animals = len(animals)
        row.sightings = sum(t.frames for t in animals)
        row.taxa = sorted({t.taxon for t in animals})
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


def write_catalogue_page(root: Path, output: Optional[Path] = None) -> Path:
    output = output or root / "index.html"
    rows = read_progress(root)
    output.write_text(render(rows))
    return output


FRAMES_PER_TILE = 8
FLIPBOOK_MS = 220
# The detector writes down anything it half-suspects, because a box it never wrote
# down is one nothing downstream can recover. The wall is the other end of that
# bargain: it shows the ones worth a person's glance and says how many it held back.
# Same floor the report's own slider starts at.
def render(rows: List[Progress]) -> str:
    listed = sum(r.listed for r in rows)
    processed = sum(r.processed for r in rows)
    hours = sum(r.seconds for r in rows) / 3600
    animals = sum(r.animals for r in rows)
    species = _best_per_species(rows)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deep-sea footage collection</title><style>{STYLE}</style></head>
<body>
  <header>
    <h1>Deep-sea footage collection</h1>
    <p class="lede">Expedition video nobody has watched, read end to end and asked two
       questions of: what moved, and what was it.</p>
    <div class="stats">
      {_stat(f"{processed:,}", f"of {listed:,} recordings", processed / listed if listed else 0)}
      {_stat(_clock(sum(r.seconds for r in rows)), "of footage analysed")}
      {_stat(f"{animals:,}", "animals found")}
      {_stat(f"{len(species)}", "classes named")}
    </div>
  </header>

  {_everything_link(rows)}
  {_species_wall(species)}

  <h2>Expeditions</h2>
  {"".join(_expedition_card(r) for r in rows)}

  {_CAVEAT if species else ""}
  <footer>Written by <code>python -m pixel_patrol_deepsea.collect site</code> on
     {datetime.now().strftime('%Y-%m-%d %H:%M')}. Reports open in the viewer beside this
     page, which needs a static file server and nothing else - serve this folder and
     follow a link.</footer>
</body></html>"""


def _everything_link(rows: List[Progress]) -> str:
    """The report over every expedition, first, because it answers what they cannot.

    "The same canyon ten years apart", "which of these has the most fish", "how
    deep was everything found" - none of those is a question a single expedition's
    report can answer, and they are the reason the reports carry a position and a
    clock at all.
    """
    from pixel_patrol_deepsea.collect import EVERYTHING

    with_reports = [r for r in rows if r.report is not None]
    if len(with_reports) < 2:
        return ""
    target = _report_url(f"../parquet/{EVERYTHING}.parquet")
    hours = _clock(sum(r.seconds for r in with_reports))
    return f"""
    <section class="everything">
      <h2>All {len(with_reports)} expeditions in one report</h2>
      <p class="desc">{hours} of footage from every expedition below, on one map, one
         timeline and one taxonomy - which is the only place a question that spans them
         can be asked. Without the cached stills and clips: those are hundreds of
         megabytes and belong in an expedition's own report, which is one click down
         this page.</p>
      <a class="open big" href="{html.escape(target, quote=True)}">Open the whole collection &rarr;</a>
    </section>"""


def _stat(value: str, label: str, share: Optional[float] = None) -> str:
    bar = (f'<div class="bar"><span style="width:{share * 100:.1f}%"></span></div>'
           if share is not None else "")
    return f'<div class="stat"><b>{value}</b><span>{label}</span>{bar}</div>'


def _best_per_species(rows: List[Progress]) -> Dict[str, object]:
    """The best look at each species across the whole collection."""
    best: Dict[str, object] = {}
    for row in rows:
        for taxon, look in row.best.items():
            if taxon not in best or look.best > best[taxon].best:
                best[taxon] = look
    return dict(sorted(best.items(), key=lambda kv: -kv[1].best))


@functools.lru_cache(maxsize=1)
def _lineages() -> Dict[str, dict]:
    from pixel_patrol_deepsea.fetch_taxonomy import load_taxonomy
    return load_taxonomy()


# The rank to group the wall under. Phylum is the one that separates a seabed of
# cnidarians and echinoderms from a midwater tape of ctenophores and salps, which
# is the distinction a reader of this page is making.
GROUP_RANK = "phylum"


def _by_phylum(species: Dict[str, object]):
    """The named classes grouped by phylum, the biggest group first.

    The detector's non-taxonomic classes - its own sampler, marine snow, an
    eggcase - go in a group of their own rather than being dropped, because "it
    photographed its equipment forty times" is worth seeing on the page.
    """
    placed = _lineages()
    groups: Dict[str, list] = {}
    for name, look in species.items():
        entry = placed.get(name)
        under = (entry or {}).get(GROUP_RANK) or ("Unplaced" if entry else "Not an animal")
        groups.setdefault(under, []).append((name, look))
    ordered = sorted(groups.items(),
                     key=lambda kv: (kv[0] in ("Not an animal", "Unplaced"), -len(kv[1])))
    return [(under, sorted(found, key=lambda kv: -kv[1].best)) for under, found in ordered]


def _species_wall(species: Dict[str, object]) -> str:
    """One tile per class, grouped by phylum: the name says what, the picture whether.

    Ordered rather than ranked by confidence. A hundred names sorted by how sure
    the detector was is a list; the same names under Cnidaria, Chordata,
    Echinodermata is a description of what lives there.
    """
    if not species:
        return ""
    groups = _by_phylum(species)
    blocks = []
    for under, found in groups:
        tiles = "".join(
            f'<figure class="big">{_img(look.thumbnail, name)}'
            f'<figcaption>{html.escape(name)}<em>{look.best:.2f}</em></figcaption></figure>'
            for name, look in found)
        blocks.append(f'<h3 class="phylum">{html.escape(under)}'
                      f'<span class="muted"> &middot; {len(found)}</span></h3>'
                      f'<div class="wall species">{tiles}</div>')
    return (f'<h2>{len(species)} classes named, in {len(groups)} groups</h2>'
            f'<p class="desc">Grouped by phylum, from the World Register of Marine '
            f'Species. The detector\'s own non-animal classes - its sampler, marine snow, '
            f'an eggcase - are a group of their own rather than left out.</p>'
            + "".join(blocks))


def _img(source: str, alt: str) -> str:
    return (f'<img src="{source}" alt="{html.escape(alt, quote=True)}" loading="lazy">'
            if source else '<span class="noimage"></span>')


def _expedition_card(row: Progress) -> str:
    # Everything here came out of a hand-edited YAML file, where `date: 2023` is an
    # int and html.escape has no idea what to do with it.
    where = " &middot; ".join(html.escape(str(bit))
                              for bit in (row.archive, row.vessel, row.date) if bit)
    return f"""
    <article>
      <div class="head">
        <div>
          <h3>{html.escape(str(row.title))}</h3>
          <p class="where">{where}</p>
          {f'<p class="note">{html.escape(str(row.notes))}</p>' if row.notes else ''}
        </div>
        {_link(row)}
      </div>
      <dl>
        <div><dt>Recordings</dt><dd>{row.processed:,} of {row.listed:,} analysed
             <div class="bar"><span style="width:{row.share * 100:.1f}%"></span></div></dd></div>
        <div><dt>Footage</dt><dd>{_clock(row.seconds)}
             {f'<span class="muted">&middot; {row.slices:,} slices, {row.stills:,} with a cached still</span>' if row.slices else ''}</dd></div>
        <div><dt>Animals</dt><dd>{_animals_cell(row)}</dd></div>
        <div><dt>Species</dt><dd>{_taxa_count(row)}</dd></div>
        {_where_and_when(row)}
      </dl>
      {_scores_table(row)}
      {_sources_list(row)}
    </article>"""


def _where_and_when(row: Progress) -> str:
    """The position, depth and date the footage carries, where it carries any.

    Two reports are only comparable if both know this, which is why the source of
    the fix is named rather than assumed: a per-second vehicle track and one
    position for a whole dive are both shown here and are not the same claim.
    """
    placed = row.placed
    if placed is None:
        return ""
    depth = f' &middot; {html.escape(placed.depth)} deep' if placed.depth else ""
    return f"""
        <div><dt>Where</dt><dd>{html.escape(placed.position)}{depth}
             <div class="muted">{html.escape(placed.when)}</div>
             <div class="muted">{html.escape(placed.source)}</div></dd></div>"""


def _scores_table(row: Progress) -> str:
    """The one expedition whose numbers can be checked rather than believed.

    Precision is what fraction of the detections landed on a real annotated animal;
    recall is what fraction of the animals present were found - and only on the
    frames the detector actually looked at, since how often to look is a separate
    decision from whether the model can see.
    """
    if not row.scores:
        return ""
    rows = "".join(
        f'<tr><td>{html.escape(str(s["recording"]))}</td>'
        f'<td>{s["frames"]:,}</td><td>{s["detections"]:,}</td><td>{s["truth_boxes"]:,}</td>'
        f'<td><b>{_ratio(s.get("precision"))}</b></td><td><b>{_ratio(s.get("recall"))}</b></td>'
        f'<td>{_reachable(s, 95)}</td><td>{_reachable(s, 99)}</td></tr>'
        for s in row.scores)
    return f"""
      <details class="scored" open>
        <summary>Checked against per-frame ground truth</summary>
        <table>
          <thead><tr><th>recording</th><th>frames judged</th><th>found</th>
            <th>actually there</th><th>precision</th><th>recall</th>
            <th>at 95% right</th><th>at 99% right</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        <p class="note">Judged only on the frames the detector looked at. Precision is
           how often it was right; recall is how much of what was there it found - both
           of everything in the report. The last two columns are the trade a reader can
           actually make with the confidence control: how much is still found while
           staying that clean, and the confidence to set for it.</p>
        <p class="note">Read the precision as a floor. The most confident boxes with no
           annotation under them, cropped and looked at, turned out to be real sea pens
           and shrimp this benchmark did not label - it tracks a chosen set of animals
           rather than claiming nothing else is in frame.</p>
      </details>"""


def _reachable(score: Dict, percent: int) -> str:
    """How much is still found at a given precision, and the floor that gets there."""
    at = (score.get("reachable") or {}).get(f"precision_{percent}") or {}
    recall, above = at.get("recall"), at.get("above_confidence")
    if recall is None:
        return "&ndash;"
    return (f'<b>{recall:.2f}</b> <span class="muted">above {above:.2f}</span>'
            if above is not None else f"<b>{recall:.2f}</b>")


def _ratio(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "&ndash;"


def _taxa_count(row: Progress) -> str:
    """How many, not which. Sixty names in a card is a wall nobody reads, and the
    report's own taxonomy widget shows them arranged by what they are."""
    if not row.best:
        return '<span class="muted">&ndash;</span>'
    placed = _lineages()
    animals = sum(1 for name in row.best if name in placed)
    kinds = ", ".join(sorted({placed[name].get("phylum") or "?"
                              for name in row.best if name in placed})[:4])
    return (f'<b>{len(row.best)}</b> classes named'
            + (f' <span class="muted">&middot; {animals} placed in the taxonomy'
               + (f', mostly {html.escape(kinds)}' if kinds else '') + '</span>'
               if animals else ''))


def _sources_list(row: Progress) -> str:
    if not row.sources:
        return ""
    items = "".join(
        f'<li><a href="{html.escape(s["url"], quote=True)}">{html.escape(s["label"])}</a></li>'
        for s in row.sources[:40])
    more = (f'<li class="muted">and {len(row.sources) - 40} more</li>'
            if len(row.sources) > 40 else "")
    return (f'<details class="sources"><summary>{len(row.sources)} source recording'
            f'{"" if len(row.sources) == 1 else "s"}</summary>'
            f'<ul>{items}{more}</ul></details>')


def _animals_cell(row: Progress) -> str:
    """Animals where they have been counted, slices where they have not.

    The two are different claims and the difference matters: a slice with animals in
    it is one moment a detector fired, while an animal is one creature however many
    moments it appeared in. Reporting the first as the second inflates everything.
    """
    if row.animals:
        return (f'<b>{row.animals:,}</b> animals'
                f'<div class="muted small">{row.sightings:,} detections</div>')
    if row.with_animals:
        return (f'<b>{row.with_animals:,}</b> slices'
                f'<div class="muted small">of {row.scored:,} a detector saw</div>')
    return '<span class="muted">&ndash;</span>'


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}" if total else "&ndash;"


def _link(row: Progress) -> str:
    if row.report is None:
        return '<span class="muted small">no report yet</span>'
    target = _report_url(f"../parquet/{row.id}.parquet")
    return f'<a class="open" href="{html.escape(target, quote=True)}">Open report &rarr;</a>'


# Said once, where the names are read. The same detector that is right on midwater
# footage called an ROV's laser dots a jellyfish at 0.98 on the seafloor.
_CAVEAT = """
  <p class="caveat">Species names come from a FathomNet detector. Each knows only the
     categories it was trained on - the midwater model has twenty-two and <b>no fish</b> -
     so anything outside that vocabulary is either missed or called something it is not,
     and on footage outside its domain it is confidently wrong. Treat a name as a prompt
     to look, not as a label.</p>"""


STYLE = """
.everything { border:1px solid #c8d6e5; background:#f4f8fc; border-radius:8px;
  padding:14px 16px; margin:18px 0; }
.everything h2 { margin:0 0 6px; }
.open.big { display:inline-block; font-size:1.05rem; padding:7px 14px; }
h3.phylum { margin:18px 0 6px; font-size:0.95rem; letter-spacing:0.02em;
  text-transform:uppercase; color:#334; border-bottom:1px solid #dde3ea; padding-bottom:3px; }

  :root { color-scheme: light dark; --ink: #0d6efd; --line: rgba(128,128,128,.2);
          --dim: #6c757d; --deep: #06101c; }
  * { box-sizing: border-box; }
  body { font: 15px/1.55 system-ui, -apple-system, sans-serif; margin: 0 auto;
         padding: 2.5rem 1.4rem 5rem; max-width: 1080px; }
  h1 { font-size: 1.75rem; margin: 0 0 .35rem; letter-spacing: -.02em; }
  h2 { font-size: 1.1rem; margin: 2.4rem 0 .8rem; letter-spacing: -.01em; }
  h3 { font-size: 1.02rem; margin: 0 0 .15rem; }
  .lede { color: var(--dim); margin: 0 0 1.6rem; max-width: 46rem; }

  /* headline numbers */
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
           gap: .9rem; margin-bottom: .5rem; }
  .stat { border: 1px solid var(--line); border-radius: 10px; padding: .8rem .9rem; }
  .stat b { display: block; font-size: 1.5rem; line-height: 1.1; letter-spacing: -.02em; }
  .stat span { display: block; color: var(--dim); font-size: .8rem; margin-top: .15rem; }
  .bar { height: 5px; border-radius: 3px; background: var(--line); overflow: hidden;
         margin-top: .5rem; }
  .bar span { display: block; height: 100%; background: var(--ink); }

  /* the animals, and the species */
  .wall { display: flex; flex-wrap: wrap; gap: .5rem; }
  .wall figure { margin: 0; width: 92px; }
  .wall figure.big { width: 116px; }
  .wall img, .wall .noimage { width: 92px; height: 92px; object-fit: cover; display: block;
                              border-radius: 8px; background: var(--deep); }
  .wall figure.big img, .wall figure.big .noimage { width: 116px; height: 116px; }
  .wall figcaption { font-size: .72rem; line-height: 1.3; margin-top: .3rem;
                     word-break: break-word; }
  .wall figcaption em { display: block; font-style: normal; color: var(--dim); }
  .species figcaption { font-weight: 600; }
  .sorts { margin-left: .4rem; }
  .sorts button { font: inherit; font-size: .82rem; background: none; cursor: pointer;
                  border: 0; border-bottom: 2px solid transparent; color: var(--ink);
                  padding: 0 .1rem; margin: 0 .3rem 0 0; }
  .sorts button.on { border-bottom-color: var(--ink); font-weight: 600; }

  /* one card per expedition */
  article { border: 1px solid var(--line); border-radius: 10px; padding: 1.1rem 1.2rem;
            margin-bottom: .9rem; }
  .head { display: flex; justify-content: space-between; align-items: flex-start;
          gap: 1rem; margin-bottom: .9rem; }
  .where, .note { color: var(--dim); font-size: .82rem; margin: 0; }
  .note { margin-top: .25rem; }
  .open { color: var(--ink); text-decoration: none; font-weight: 600; white-space: nowrap;
          border: 1px solid var(--ink); border-radius: 7px; padding: .3rem .7rem;
          font-size: .85rem; }
  .open:hover { background: var(--ink); color: #fff; }
  dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
       gap: .7rem 1.4rem; margin: 0; }
  dl div { font-size: .88rem; }
  dt { color: var(--dim); font-size: .72rem; text-transform: uppercase;
       letter-spacing: .05em; margin-bottom: .15rem; }
  dd { margin: 0; }
  .taxa { display: flex; flex-wrap: wrap; gap: .3rem; }
  .taxon { display: inline-flex; align-items: center; gap: .35rem; font-size: .78rem;
           background: rgba(13,110,253,.09); color: var(--ink); border-radius: 999px;
           padding: .12rem .6rem .12rem .12rem; }
  .taxon:not(:has(img)) { padding-left: .6rem; }
  .taxon img { width: 26px; height: 26px; object-fit: cover; border-radius: 50%;
               background: var(--deep); flex: none; }
  .taxon em { font-style: normal; opacity: .65; }

  .scored { font-size: .82rem; margin-top: .9rem; }
  .scored summary { cursor: pointer; color: var(--ink); font-weight: 600; }
  .scored table { width: 100%; border-collapse: collapse; margin-top: .5rem; }
  .scored th { text-align: left; font-size: .68rem; text-transform: uppercase;
               letter-spacing: .05em; color: var(--dim); padding: 0 .5rem .2rem 0;
               border-bottom: 1px solid var(--line); font-weight: 600; }
  .scored td { padding: .3rem .5rem .3rem 0; border-bottom: 1px solid var(--line); }
  .sources { font-size: .82rem; margin-top: .9rem; }
  .sources summary { cursor: pointer; color: var(--dim); }
  .sources ul { margin: .45rem 0 0; padding-left: 1.1rem; columns: 2; }
  .sources li { margin-bottom: .15rem; break-inside: avoid; }

  .muted { color: var(--dim); }
  .small { font-size: .82rem; }
  .caveat { font-size: .84rem; color: #856404; background: rgba(255,193,7,.12);
            border-left: 3px solid #ffc107; padding: .7rem 1rem; border-radius: 6px;
            margin-top: 2rem; }
  footer { color: #adb5bd; font-size: .8rem; margin-top: 2rem; }
  code { background: rgba(128,128,128,.14); padding: .1rem .35rem; border-radius: 3px;
         font-size: .84rem; }
"""
