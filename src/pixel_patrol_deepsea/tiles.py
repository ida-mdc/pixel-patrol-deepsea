"""The pictures the collection page shows, written out so it can show all of them.

The page used to embed one crop per species - a wall of thirty tiles built into the
HTML - because that is what fits in a page. A collection holds tens of thousands of
animals, and the interesting question is not "what is the best sponge" but "show me
every sponge", which is a different shape of page: a browser over a store, not a
document with pictures in it.

So the crops come out of the reports and into a store beside them, paged:

    tiles/index.json          the taxonomy, with a count on every node
    tiles/<taxon>/p0.json     sixty animals, most confident first, and their sizes
    tiles/<taxon>/p0.jpgs     their sixty crops, end to end, raw JPEG
    tiles/<taxon>/p0.clips    the frames that animate them, end to end

Three files per page, not one per picture. A page is one request for a screenful of
stills, and a page nobody scrolls to is never fetched; hovering a tile fetches its
page's animations once and then every tile on that page moves for free.

The pictures are raw JPEG laid end to end rather than base64 inside the JSON,
because base64 is a third more bytes and the clips are most of the store. Nothing
says where one picture ends - the JSON carries the lengths in tile order and the
page adds them up, so the metadata stays small and the offsets cannot disagree with
the bytes.

This shape is what lets the store grow. Measured on nine expeditions - 2.7% of what
they published - the collection held 45,000 animals: 129 MB of stills and 0.84 GB
of animations. All of it read out would be some 1.7 million animals, which as a file
per animal is 1.7 million files and 37 GB. Packed and unencoded it is about 72,000
files and a quarter less on disk, and no more work for the browser: what is fetched
is still only what somebody looked at.

Everything here is written once, by `collect site`, from reports that already hold
it. No footage is read.
"""

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Animals per page. Sixty is about a screenful and a half at the grid's tile size,
# so scrolling fetches ahead of the reader rather than in step with them.
PER_PAGE = 60
# Frames kept per animation. The detector cuts about ten, the difference between six
# and ten is not visible in a tile this size, and the bytes are linear in it.
MOST_FRAMES = 6
# Tiles below this confidence are still written - the page has a threshold - but
# they sort last, so a reader who never scrolls sees the detector's best work.
LOW_CONFIDENCE = 0.0

RANKS = ("kingdom", "phylum", "class", "order", "family", "genus")


def slug(name: str) -> str:
    """A taxon name as a directory: lowercase, dashes, nothing surprising."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return cleaned or "unnamed"


def _lineage(name: str, taxonomy: Dict) -> List[str]:
    from pixel_patrol_deepsea.fetch_taxonomy import lineage_of

    return lineage_of(name, taxonomy)


def build(root: Path, expeditions: Optional[List[Path]] = None) -> Dict:
    """Write the tile store for a collection, and return what it holds.

    One pass per expedition report. Each animal contributes one still - its most
    confident look - and, where the detector cut one, an animation.
    """
    from pixel_patrol_deepsea.catalogue_page import read_animals
    from pixel_patrol_deepsea.fetch_taxonomy import load_taxonomy
    from pixel_patrol_deepsea.refine import is_an_animal

    taxonomy = load_taxonomy()
    store = root / "tiles"
    reports = expeditions if expeditions is not None else sorted(
        p for p in (root / "parquet").glob("*.parquet") if not p.stem.startswith("_"))

    # taxon -> the animals found of it, best first
    found: Dict[str, List[dict]] = {}
    frames: Dict[str, List[List[str]]] = {}
    for report in reports:
        expedition = report.stem
        try:
            tracks, clips = read_animals(report)
        except Exception as exc:
            logger.warning("tiles: cannot read %s: %s", report.name, exc)
            continue
        for number, track in enumerate(tracks):
            if not is_an_animal(track.taxon):
                continue
            best = track.best
            if not best.crop:
                continue
            entry = {
                "t": track.taxon,
                "e": expedition,
                "r": track.recording,
                "s": round(best.second, 1),
                "c": round(best.confidence, 3),
                "a": round(getattr(track, "agreement", 1.0), 2),
                "n": len(track.sightings),
                "i": best.crop,
            }
            clip = clips.get((track.recording, best.slice_t, 0))
            found.setdefault(track.taxon, []).append(entry)
            frames.setdefault(track.taxon, []).append(
                list(clip.crops[:MOST_FRAMES]) if clip else [])
        logger.info("tiles: %s gave %d animals", expedition, len(tracks))

    # The store is written whole. Leaving a previous build's pages behind would
    # leave the index pointing at some of them and the taxonomy at others.
    if store.exists():
        shutil.rmtree(store)
    store.mkdir(parents=True, exist_ok=True)

    counts: Dict[str, int] = {}
    for taxon, animals in found.items():
        order = sorted(range(len(animals)), key=lambda i: -animals[i]["c"])
        animals = [animals[i] for i in order]
        moving = [frames[taxon][i] for i in order]
        where = store / slug(taxon)
        where.mkdir(parents=True, exist_ok=True)
        for page in range((len(animals) + PER_PAGE - 1) // PER_PAGE):
            first = page * PER_PAGE
            _write_page(where, page, animals[first:first + PER_PAGE],
                        moving[first:first + PER_PAGE])
        counts[taxon] = len(animals)

    index = {"tree": _tree(counts, taxonomy), "taxa": {
        taxon: _about(taxon, n, taxonomy) for taxon, n in sorted(counts.items())}}
    (store / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    logger.info("tiles: %d taxa, %d animals", len(counts), sum(counts.values()))
    return index


def _write_page(where: Path, page: int, animals: List[dict],
                moving: List[List[bytes]]) -> None:
    """One page of the store: the sizes in JSON, the pictures in two blobs.

    The JSON never repeats a byte of a picture. `l` is how long this animal's still
    is and `f` how long each of its frames is, both in tile order, so the page
    recovers every offset by adding up what came before it.
    """
    stills, clips, entries = bytearray(), bytearray(), []
    for entry, animation in zip(animals, moving):
        still = entry.pop("i")
        stills += still
        listed = dict(entry, l=len(still), m=1 if animation else 0)
        if animation:
            listed["f"] = [len(frame) for frame in animation]
            for frame in animation:
                clips += frame
        entries.append(listed)
    (where / f"p{page}.json").write_text(json.dumps(entries, separators=(",", ":")))
    (where / f"p{page}.jpgs").write_bytes(bytes(stills))
    if clips:
        (where / f"p{page}.clips").write_bytes(bytes(clips))


def _about(taxon: str, count: int, taxonomy: Dict) -> dict:
    """What the page can say about a name besides how often it appeared.

    The register's own id is the useful part: a reader who has never heard of
    `Asbestopluma` should be one click from the people whose business it is, rather
    than from a search engine's guess at the word. Names the register does not know
    carry no link, which is itself worth seeing - it usually means the detector's
    class is not a taxon at all.
    """
    entry = taxonomy.get(taxon) or {}
    about = {"slug": slug(taxon), "count": count,
             "pages": (count + PER_PAGE - 1) // PER_PAGE}
    if entry.get("aphia_id"):
        about["aphia"] = int(entry["aphia_id"])
    if entry.get("rank"):
        about["rank"] = str(entry["rank"])
    lineage = [entry[rank] for rank in RANKS if entry.get(rank)]
    if lineage:
        about["above"] = lineage
    return about


def _tree(counts: Dict[str, int], taxonomy: Dict) -> dict:
    """The taxonomy as a tree of nodes, each carrying what is under it.

    Built from the lineages of the names actually found, so it has the shape of the
    collection rather than the shape of the register: a branch nothing was found in
    is not a branch anyone can click.
    """
    root: dict = {"name": "everything", "count": 0, "children": {}, "taxa": []}
    for taxon, count in counts.items():
        node = root
        node["count"] += count
        for step in _lineage(taxon, taxonomy):
            node = node["children"].setdefault(
                step, {"name": step, "count": 0, "children": {}, "taxa": []})
            node["count"] += count
        node["taxa"].append(taxon)
    return _plain(root)


def _plain(node: dict) -> dict:
    """The tree as plain lists, smallest branches last, for the page to draw."""
    children = [_plain(child) for child in node["children"].values()]
    children.sort(key=lambda child: -child["count"])
    out = {"name": node["name"], "count": node["count"]}
    if children:
        out["children"] = children
    if node["taxa"]:
        out["taxa"] = sorted(node["taxa"])
    return out
