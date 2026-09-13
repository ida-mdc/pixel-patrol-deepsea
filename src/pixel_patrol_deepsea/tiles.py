"""The pictures the collection page shows, written out so it can show all of them.

The page used to embed one crop per species - a wall of thirty tiles built into the
HTML - because that is what fits in a page. A collection holds tens of thousands of
animals, and the interesting question is not "what is the best sponge" but "show me
every sponge", which is a different shape of page: a browser over a store, not a
document with pictures in it.

So the crops come out of the reports and into a store beside them, paged:

    tiles/index.json          the taxonomy with a count on every node, and where
                              each recording can be played from
    tiles/<taxon>/p0.json     sixty animals, most confident first, their sizes,
                              and what the tile says about each: name, expedition,
                              second, confidence, agreement, looks, duration
    tiles/<taxon>/p0.jpgs     their sixty crops, end to end, raw JPEG
    tiles/<taxon>/p0.clips    the frames that animate them, end to end
    tiles/reels/<recording>    every animal in one recording, and every look the
                               detector had at it, for the viewer the page opens

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
import os
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
    from pixel_patrol_deepsea.catalogue_page import animals_by_recording
    from pixel_patrol_deepsea.fetch_taxonomy import load_taxonomy
    from pixel_patrol_deepsea.refine import is_an_animal

    taxonomy = load_taxonomy()
    store = root / "tiles"
    sources = ([(report.stem, [report]) for report in expeditions]
               if expeditions is not None else _sources(root))

    # taxon -> the animals found of it, best first. Metadata only: where each
    # animal's pictures went is two numbers, and the pictures are in the spool.
    found: Dict[str, List[dict]] = {}
    # expedition -> the recordings its animals came out of, for the URLs and the
    # frame size the page needs to play a moment back and draw a box on it.
    seen: Dict[str, set] = {}
    # (expedition, recording) -> every animal in it, for the page's own little
    # viewer: one dive, playing, with every box that belongs on screen.
    reels: Dict[tuple, List[dict]] = {}
    # The pictures go straight to disk as they are read and come back when the page
    # they belong to is written. Held in memory instead, a collection's worth of
    # stills and clips is gigabytes that grow with every expedition added - and
    # what that looks like is `Killed`, after twenty minutes, with no page.
    spool = _Spool(root)
    try:
        for expedition, paths in sources:
            counted = 0
            for path in paths:
                for _recording, tracks, clips, moments in _animals_of(
                        path, animals_by_recording):
                    for track in tracks:
                        if not is_an_animal(track.taxon):
                            continue
                        best = track.best
                        if not best.crop:
                            continue
                        counted += 1
                        when, deep = moments.get(best.slice_t, ("", None))
                        clip = _its_clip(clips, track)
                        film = list(clip.crops[:MOST_FRAMES]) if clip else []
                        entry = {
                            "t": track.taxon,
                            "e": expedition,
                            "r": track.recording,
                            "s": round(best.second, 1),
                            "c": round(best.confidence, 3),
                            "a": round(getattr(track, "agreement", 1.0), 2),
                            "n": len(track.sightings),
                            # How long it stayed in view. The tile says this beside the
                            # confidence, because "0.94, and gone in a tenth of a second"
                            # and "0.94, and there for a minute" are not the same claim.
                            "d": round(track.seconds, 1),
                            # Where in the frame it was, so a reader who opens the
                            # recording at this second is shown which of the things on
                            # screen was meant.
                            "b": [int(v) for v in (best.box or ())][:4],
                            # When it was filmed and how deep, which is what anybody
                            # asks of a deep-sea picture after what it is.
                            "w": when,
                            "z": deep,
                            # Where the pictures went, rather than the pictures.
                            "i": spool.keep(best.crop),
                            "f": spool.keep_all(film),
                        }
                        found.setdefault(track.taxon, []).append(entry)
                        seen.setdefault(expedition, set()).add(track.recording)
                        # ...and again by the recording it came out of, which is how the
                        # page shows a reader watching one dive what else is on screen.
                        reels.setdefault((expedition, track.recording), []).append({
                            "t": track.taxon, "s": entry["s"], "c": entry["c"],
                            "d": entry["d"], "n": entry["n"], "w": when, "z": deep,
                            "was": (track.taxon, len(found[track.taxon]) - 1),
                            "k": _looks(track),
                        })
            logger.info("tiles: %s gave %d animals", expedition, counted)

        # The store is written whole. Leaving a previous build's pages behind
        # would leave the index pointing at some of them and the taxonomy at
        # others.
        if store.exists():
            shutil.rmtree(store)
        store.mkdir(parents=True, exist_ok=True)

        counts: Dict[str, int] = {}
        # (taxon, the order it was found in) -> the page and place it ended up in,
        # so a reel can point at a tile without a second copy of its picture.
        placed: Dict[tuple, Tuple[int, int]] = {}
        for taxon, animals in found.items():
            order = sorted(range(len(animals)), key=lambda i: -animals[i]["c"])
            for at, was in enumerate(order):
                placed[(taxon, was)] = (at // PER_PAGE, at % PER_PAGE)
            animals = [animals[i] for i in order]
            where = store / slug(taxon)
            where.mkdir(parents=True, exist_ok=True)
            for page in range((len(animals) + PER_PAGE - 1) // PER_PAGE):
                first = page * PER_PAGE
                _write_page(where, page, animals[first:first + PER_PAGE], spool)
            counts[taxon] = len(animals)

        takes = _write_reels(store, reels, placed)
    finally:
        spool.close()
    index = {"tree": _tree(counts, taxonomy),
             "taxa": {taxon: _about(taxon, n, taxonomy)
                      for taxon, n in sorted(counts.items())},
             "where": {expedition: _where(root, expedition, recordings,
                                          takes.get(expedition, {}))
                       for expedition, recordings in sorted(seen.items())}}
    (store / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    logger.info("tiles: %d taxa, %d animals", len(counts), sum(counts.values()))
    return index


class _Spool:
    """Every picture a build meets, in the order it met them, on disk.

    A collection's stills and clips are gigabytes - 580 MB of them at nine
    expeditions and growing with every one added - and the build needs them twice:
    once as they come out of the reports, and again when the page they belong to is
    written, which cannot happen until every animal of that taxon has been seen and
    sorted. Holding them in between is what a node kills. Holding an offset each is
    forty bytes.

    Written beside the collection rather than in `/tmp`, because `/tmp` on a compute
    node is small and this is the size of the store it is about to write. Where the
    collection's own disk is the full one - which is the usual way round for a disk
    holding a collection - `PP_TILES_SPOOL` names somewhere else to put it.
    """

    def __init__(self, root: Path):
        elsewhere = os.environ.get("PP_TILES_SPOOL", "").strip()
        self.where = (Path(elsewhere) / f".tiles-spool-{os.getpid()}" if elsewhere
                      else root / ".tiles-spool")
        self.where.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.where.open("w+b")
        self.at = 0

    def keep(self, picture: bytes) -> Tuple[int, int]:
        """Put one picture away, and say where it went."""
        if not picture:
            return (self.at, 0)
        self.file.write(picture)
        at, self.at = self.at, self.at + len(picture)
        return (at, len(picture))

    def keep_all(self, pictures: List[bytes]) -> Tuple[int, List[int]]:
        """A clip's frames, end to end: one offset and the lengths."""
        first = self.at
        for picture in pictures:
            self.keep(picture)
        return (first, [len(picture) for picture in pictures])

    def read(self, at: int, length: int) -> bytes:
        if not length:
            return b""
        self.file.seek(at)
        return self.file.read(length)

    def close(self) -> None:
        try:
            self.file.close()
        finally:
            self.where.unlink(missing_ok=True)


def _animals_of(report: Path, reader):
    """One report's animals, recording by recording, or a warning and none.

    A report that cannot be read is one expedition missing from the page, which the
    page says for itself. It is not a reason to write no store.
    """
    try:
        yield from reader(report)
    except Exception as exc:
        logger.warning("tiles: cannot read %s: %s", report.name, exc)


def _its_clip(clips: Dict, track):
    """The clip cut with this animal's own box, or none.

    The detector cuts one clip per animal - the few most convincing in a slice -
    and numbers them `of` in its own order, which is not an index into anything
    this side of the report knows. What identifies a clip is the box it was cut
    with: the same box the animal was detected in. Assuming the number was zero
    handed every animal in a slice the first animal's film, so a bottom covered in
    sea pens animated as the same sea pen over and over - the tile showed one
    animal and moved as another.

    Matched against the animal's own looks, most confident first: the still is its
    best look, so that slice is where to try, and a look a second later is still
    this animal rather than its neighbour. An animal the slice cut no clip for -
    only the few most convincing get one - gets none, which is the honest answer.
    A tile that does not move is better than one that moves as something else.
    """
    from pixel_patrol_deepsea.catalogue_page import ITS_OWN_CLIP, _overlap

    for look in sorted(track.sightings, key=lambda s: -s.confidence):
        here = [clip for key, clip in clips.items()
                if key[0] == track.recording and key[1] == look.slice_t]
        mine = max(here, key=lambda clip: _overlap(clip.box, look.box), default=None)
        if mine is not None and _overlap(mine.box, look.box) >= ITS_OWN_CLIP:
            return mine
    return None


def _write_page(where: Path, page: int, animals: List[dict], spool: "_Spool") -> None:
    """One page of the store: the sizes in JSON, the pictures in two blobs.

    The JSON never repeats a byte of a picture. `l` is how long this animal's still
    is and `f` how long each of its frames is, both in tile order, so the page
    recovers every offset by adding up what came before it.

    The pictures come back out of the spool here, sixty at a time, which is the
    only moment a build holds any of them.
    """
    stills, clips, entries = bytearray(), bytearray(), []
    for entry in animals:
        entry = dict(entry)
        at, length = entry.pop("i")
        still = spool.read(at, length)
        stills += still
        first, sizes = entry.pop("f")
        listed = dict(entry, l=len(still), m=1 if sizes else 0)
        if sizes:
            listed["f"] = sizes
            clips += spool.read(first, sum(sizes))
        entries.append(listed)
    (where / f"p{page}.json").write_text(json.dumps(entries, separators=(",", ":")))
    (where / f"p{page}.jpgs").write_bytes(bytes(stills))
    if clips:
        (where / f"p{page}.clips").write_bytes(bytes(clips))


def _sources(root: Path) -> List[Tuple[str, List[Path]]]:
    """Where the pictures are, per expedition: the parts if they are here.

    A merged report no longer carries the clip frames - they are 84% of it and this
    store is where they belong - so the store is cut from the parts, one parquet per
    recording, which keep everything. A collection copied without them still builds:
    the merged reports have the stills, and the animations are what is missing.
    """
    parts = root / "parts"
    found: List[Tuple[str, List[Path]]] = []
    for report in sorted((root / "parquet").glob("*.parquet")):
        if report.stem.startswith("_"):
            continue
        its_parts = sorted((parts / report.stem).glob("*.parquet"))
        found.append((report.stem, its_parts or [report]))
        if not its_parts:
            logger.info("tiles: no parts for %s, reading the merged report instead",
                        report.stem)
    return found


def _looks(track) -> List[List]:
    """Every look the detector had at one animal: when, and where in the frame.

    The page draws the box as the recording plays, so one box for the whole time an
    animal was in view is no good - an animal that swims leaves it within a second.
    These are the moments the detector actually looked, in order, and the page moves
    the box between them.
    """
    looks = []
    for sighting in sorted(track.sightings, key=lambda s: s.second):
        box = [int(v) for v in (sighting.box or ())][:4]
        if len(box) == 4:
            looks.append([round(float(sighting.second), 1), *box])
    return looks


def _write_reels(store: Path, reels: Dict[tuple, List[dict]],
                 placed: Dict[tuple, Tuple[int, int]]) -> Dict[str, Dict[str, str]]:
    """One file per recording: every animal found in it, and when.

    The pages are cut by taxon, which is the right shape for "show me every sponge"
    and the wrong one for "what else is in this dive" - and that is the question
    somebody has the moment a recording is playing in front of them. Both are the
    same forty-five thousand animals; this is the other index over them.

    A reel holds no pictures. Each animal points at the tile it already has, by page
    and place, so the viewer can show the crop of whatever is clicked without the
    store carrying it twice.
    """
    where = store / "reels"
    where.mkdir(parents=True, exist_ok=True)
    takes: Dict[str, Dict[str, str]] = {}
    taken = set()
    for (expedition, recording), animals in sorted(reels.items()):
        name = slug(f"{expedition}--{recording}")
        # Two recordings whose names differ only in punctuation would slug to one
        # file, and the second would quietly become the first one's reel.
        while name in taken:
            name += "-2"
        taken.add(name)
        listed = []
        for animal in sorted(animals, key=lambda a: a["s"]):
            page, at = placed.get(animal["was"], (None, None))
            if page is None:
                continue
            listed.append({"t": animal["t"], "s": animal["s"], "c": animal["c"],
                           "d": animal["d"], "n": animal["n"],
                           "w": animal.get("w", ""), "z": animal.get("z"),
                           "g": slug(animal["t"]), "p": page, "at": at,
                           "k": animal["k"]})
        (where / f"{name}.json").write_text(json.dumps(listed, separators=(",", ":")))
        takes.setdefault(expedition, {})[recording] = name
    return takes


def _where(root: Path, expedition: str, recordings, takes: Dict[str, str]) -> dict:
    """How to play a moment back: the recordings' URLs, and the frame they are in.

    Nothing is copied or re-hosted. The page opens the archive's own file at the
    second the animal was found, which is the only honest way to show somebody what
    a detection actually was - a crop of a 640-pixel frame proves very little on its
    own, and the surrounding seconds are the evidence.

    The URL comes from the manifest the recordings were listed from, joined on the
    file name the report carries. Matched by prefix rather than by equality: a
    report from an older run names a transcoded copy, `..._ROVHD_Low_10fps`, and
    the recording it came from is the one the manifest listed. The frame size is
    the box's coordinate system, so the page can scale it onto a video element of
    any size.
    """
    from pixel_patrol_deepsea.catalogue_page import _frame_size

    wide, high = _frame_size(root / "parquet" / f"{expedition}.parquet")
    listed = []
    manifest = root / "manifests" / f"{expedition}.json"
    if manifest.is_file():
        try:
            listed = list(json.loads(manifest.read_text()).get("videos", []))
        except Exception as exc:
            logger.warning("tiles: cannot read %s: %s", manifest.name, exc)
    # Longest stem first, so `DIVE01_PART02` is not claimed by `DIVE01`.
    by_stem = sorted(((Path(url.split("?")[0]).stem, url) for url in listed),
                     key=lambda pair: -len(pair[0]))
    videos = {}
    for recording in sorted(recordings):
        stem = Path(str(recording)).stem
        for listed_stem, url in by_stem:
            # Exactly, or the listed name plus a suffix a transcode would add. A
            # bare prefix would let `..._DIVE0` claim `..._DIVE01`.
            if stem == listed_stem or stem.startswith(listed_stem + "_") \
                    or stem.startswith(listed_stem + "-"):
                videos[str(recording)] = url
                break
    if not videos and recordings:
        logger.warning("tiles: %s has no manifest beside it, so none of its %d "
                       "recordings can be opened from the page",
                       expedition, len(recordings))
    elif len(videos) < len(recordings):
        logger.info("tiles: %s has no listed URL for %d of %d recordings",
                    expedition, len(recordings) - len(videos), len(recordings))
    return {"frame": [wide, high], "videos": videos,
            "takes": {name: takes[name] for name in sorted(takes)},
            **_terms_of(expedition)}


def _terms_of(expedition: str) -> dict:
    """What this expedition's footage may be used for, and who to credit.

    Carried per expedition rather than said once at the foot of the page, because
    the page shows crops of three archives at once and they do not agree: one is
    public domain, one is share-alike, and one has no licence at all and a required
    acknowledgement. Whatever is on screen, the credit for it should be too.
    """
    from pixel_patrol_deepsea.catalogue import catalogue_path, load_catalogue

    try:
        known = {e.id: e for e in load_catalogue(catalogue_path())}
        terms = known[expedition].terms
    except Exception:
        return {}
    return {"licence": terms.licence, "credit": terms.credit,
            "cite": terms.cite, "terms": terms.url}


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
