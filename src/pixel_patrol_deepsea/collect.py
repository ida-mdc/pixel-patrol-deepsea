"""Collect an archive of expedition video into one parquet per expedition.

Each verb does one thing, so a scheduler can run them independently and only redo
what changed:

    python -m pixel_patrol_deepsea.collect list  EX2107 -o manifests/EX2107.json
    python -m pixel_patrol_deepsea.collect one   <video-url> -o parts/x.parquet -e EX2107
    python -m pixel_patrol_deepsea.collect merge EX2107 parts/*.parquet -o parquet/EX2107.parquet
    python -m pixel_patrol_deepsea.collect site  collection/
    python -m pixel_patrol_deepsea.collect serve collection/

`judge` and `slim` are the odd ones out: they rewrite reports that are already
written, reading no footage. `judge` re-decides what the footage was doing;
`slim` throws away the pictures a report holds twice.

This module is the verbs and what they take. The work is next door, one module
per job, so that each can be read without the others: `analyse` reads footage,
`merge` joins reports, `triage` judges them, `slim` cuts their spare pictures,
`tiles` builds the store the page browses, `serve` hands the lot to a browser.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from pixel_patrol_deepsea.catalogue import (
    Manifest, catalogue_path, discover, find_expedition, load_catalogue,
)
from pixel_patrol_deepsea.remote_file import open_with_retry
from pixel_patrol_deepsea.analyse import (
    DETECT_EVERY, DETECTOR, DETECTOR_FRAMES, DETECTOR_SIZES, SLICE_FRAMES,
    analyse_one, run_expedition,
)
from pixel_patrol_deepsea.merge import combine, merge, unreadable

logger = logging.getLogger(__name__)

PROCESSORS = ("raster-temporal", "raster-motion", "slice-thumbnail",
              "slice-colour", "slice-colour-spread", "slice-location",
              "raster-detections", "raster-basic")


# ── list ──────────────────────────────────────────────────────────────────────

def list_videos(expedition_id: str, output: Path) -> int:
    catalogue = load_catalogue(catalogue_path())
    try:
        expedition = find_expedition(catalogue, expedition_id)
    except LookupError:
        known = ", ".join(e.id for e in catalogue)
        print(f"no expedition {expedition_id!r}; the catalogue has: {known}", file=sys.stderr)
        return 2
    manifest = discover(expedition)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(manifest.to_json())
    print(f"{expedition.title}: {len(manifest.videos)} recordings -> {output}")
    return 0


# ── choose ────────────────────────────────────────────────────────────────────

def choose_videos(expedition_id: str, output: Path, manifest: Optional[Path] = None,
                  dives: int = 0, per_dive: int = 0, most: int = 0) -> int:
    """Which of an expedition's recordings are worth analysing, as its own manifest.

    `run` makes this choice inside itself, which is right when one machine does a
    whole expedition. A scheduler needs it as a step: a stage that turns the full
    listing into the subset worth fanning out. Without one, "the first N
    recordings" is the only cheap answer a workflow can give, and on a deep cruise
    the first N recordings are the vehicle descending through open water.

    The output is manifest-shaped, so everything downstream reads it exactly like
    the full listing - and the full listing is left alone, because the catalogue
    page counts what an expedition published, not what we picked out of it.
    """
    from pixel_patrol_deepsea import selection

    catalogue = load_catalogue(catalogue_path())
    try:
        expedition = find_expedition(catalogue, expedition_id)
    except LookupError:
        known = ", ".join(e.id for e in catalogue)
        print(f"no expedition {expedition_id!r}; the catalogue has: {known}", file=sys.stderr)
        return 2

    if manifest and manifest.is_file():
        listed = json.loads(manifest.read_text())
        videos, listed_at = listed["videos"], listed.get("listed_at", "")
    else:
        found = discover(expedition)
        videos, listed_at = found.videos, found.listed_at

    chosen = selection.choose(expedition, videos, dives=dives, per_dive=per_dive, most=most)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(Manifest(expedition=expedition_id, videos=chosen,
                               listed_at=listed_at).to_json())
    print(f"{selection.report(expedition, videos, chosen)} -> {output}")
    return 0


# ── one ───────────────────────────────────────────────────────────────────────

# How hard to compress the thinned copy. Deep-sea animals are small, low-contrast
# and exactly what a video encoder is designed to decide is not worth the bits, so
# this is not a free knob. Against MBARI's annotated benthic sequence:
#
#   crf 30   precision 0.92   recall 0.58     <- what this used to be
#   crf 22   precision 0.91   recall 0.69
#   crf 18   precision 0.90   recall 0.70
#
# A ninth of the animals in the footage were being thrown away by the transcode that
# was only ever meant to drop frame rate. 22 is the knee; 18 costs bits for nothing.
THINNED_QUALITY = os.environ.get("PIXEL_PATROL_THINNED_QUALITY", "22")


# ── identify ──────────────────────────────────────────────────────────────────

def judge_reports(target: Path) -> int:
    """Judge reports that were written under an older set of rules.

    Only the triage - what each slice of footage was doing, and how many seconds
    of each verdict a recording holds. Nothing is re-read and no footage is
    touched: the verdicts are made of nine columns of numbers that are already in
    the file, so this is minutes over a whole collection rather than the weeks the
    analysis took.

    Separate from `identify` because the two are needed at different times. A
    change to the rules here means every report ever written says the wrong thing
    about its own footage, and re-deriving the animal identities as well is work
    nobody asked for on files measured in gigabytes.
    """
    from pixel_patrol_deepsea.triage import describe

    reports = _reports_under(target)
    if not reports:
        print(f"no reports under {target}", file=sys.stderr)
        return 1
    judged = 0
    for report in reports:
        try:
            recordings = describe(report)
        except Exception as exc:
            print(f"{report}: {exc}", file=sys.stderr)
            continue
        judged += recordings
        print(f"{report.relative_to(target) if target.is_dir() else report.name}: "
              f"{recordings} recordings judged")
    print(f"{len(reports)} reports, {judged} recordings judged")
    return 0


def _reports_under(target: Path) -> List[Path]:
    """Every report at or under a path - but not the sightings written beside them."""
    return ([target] if target.is_file()
            else sorted(p for p in target.rglob("*.parquet")
                        if p.parent.name != "sightings"))


def identify_reports(target: Path) -> int:
    """Bring reports up to date with what `collect one` writes now.

    Two things, both derived from what is already in the file - the animals its
    detections belong to, and what each slice of footage was doing. A report
    analysed last week gets exactly what it would have been given at the time.
    Nothing is re-read and no footage is touched.
    """
    from pixel_patrol_deepsea.identity import identify
    from pixel_patrol_deepsea.triage import describe

    reports = _reports_under(target)
    if not reports:
        print(f"no reports under {target}", file=sys.stderr)
        return 1
    total = judged = 0
    for report in reports:
        try:
            animals = identify(report)
            recordings = describe(report)
        except Exception as exc:
            print(f"{report}: {exc}", file=sys.stderr)
            continue
        total += animals
        judged += recordings
        print(f"{report.relative_to(target) if target.is_dir() else report.name}: "
              f"{animals} animals, {recordings} recordings judged")
    print(f"{len(reports)} reports, {total} animals, {judged} recordings judged")
    return 0


# ── score ─────────────────────────────────────────────────────────────────────

def score(expedition_id: str, root: Path) -> int:
    """Match an expedition's detections against its ground truth, where it has any."""
    import json

    import polars as pl

    from pixel_patrol_deepsea.catalogue import truth_urls
    from pixel_patrol_deepsea.groundtruth import (
        FLOORS, first_annotated_frame, pool, read_mot, score_recording)
    from pixel_patrol_deepsea.remote_file import open_with_retry
    from pixel_patrol_deepsea.reports import slice_rows

    expedition = find_expedition(load_catalogue(catalogue_path()), expedition_id)
    if not expedition.truth:
        print(f"{expedition.title} has no ground truth to score against")
        return 1
    report = root / "parquet" / f"{expedition_id}.parquet"
    if not report.is_file():
        print(f"no report at {report}")
        return 1

    found = _detections_by_recording(slice_rows(pl.read_parquet(report)))
    scores = []
    for video in discover(expedition).videos:
        name = Path(video.split("?")[0]).stem
        detections = found.get(name)
        if not detections:
            continue
        truth = _read_truth(expedition, video, read_mot)
        if not truth:
            continue
        # The original recording's frame rate, read from its header over one range
        # request. Ground truth counts frames of the original; the report may have
        # analysed a thinned copy, so the two are joined on seconds and this is what
        # turns a truth frame number into a second.
        scores.append(score_recording(name, detections, truth, fps=_fps_of(video),
                                      first_frame=first_annotated_frame(truth)))

    if not scores:
        print("nothing to score: no recording in the report has ground truth beside it")
        return 1
    print(f"\n{expedition.title} against its own ground truth:\n")
    for one in scores:
        print("  " + str(one))
    if len(scores) > 1:
        scores.append(pool(scores))
        print("  " + "-" * 150)
        print("  " + str(scores[-1]))
    where = root / "scores" / f"{expedition_id}.json"
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps([{
        "recording": s.recording, "frames": s.frames_judged, "detections": s.detections,
        "truth_boxes": s.truth_boxes, "matched": s.matched,
        "precision": s.precision, "recall": s.recall,
        # The curve is what the numbers above are one point of. Kept as the
        # reachable operating points rather than in full: a page wants "0.63 of
        # them, staying 95% right, above confidence 0.06", not ten thousand
        # points of it.
        "truth_boxes_all": s.truth_boxes_all,
        "frames_annotated": s.frames_annotated,
        "recall_overall": s.recall_overall,
        "looked_at": s.looked_at,
        "reachable": {f"precision_{int(floor * 100)}": {
            "recall": s.at_precision(floor)[0],
            "recall_overall": s.overall_at_precision(floor),
            "above_confidence": s.at_precision(floor)[1],
        } for floor in FLOORS},
    } for s in scores], indent=1))
    print(f"\n-> {where}")
    return 0


def _detections_by_recording(slices) -> dict:
    """Every detection in the report, keyed by recording, with its absolute frame."""
    import json

    recording = "child_id" if "child_id" in slices.columns else "name"
    out: dict = {}
    for row in slices.iter_rows(named=True):
        if not row.get("detections") or row.get("dim_t") is None:
            continue
        try:
            animals = json.loads(row["detections"])
        except Exception:
            continue
        name = Path(str(row.get(recording) or row.get("name") or "")).stem
        for animal in animals:
            if animal.get("clip"):
                continue        # crops of frames the model never looked at
            out.setdefault(name, []).append(
                dict(animal))
    return out


def _fps_of(video: str) -> float:
    """The frame rate of the original recording, from its header alone."""
    import av

    from pixel_patrol_deepsea.remote_file import remote_video

    try:
        with av.open(remote_video(video)) as container:
            stream = container.streams.video[0]
            return float(stream.average_rate) if stream.average_rate else 0.0
    except Exception as exc:
        logger.warning("cannot read the frame rate of %s: %s", video, exc)
        return 0.0


def _read_truth(expedition, video: str, read_mot):
    """The ground truth beside one recording, from wherever that archive keeps it.

    Both layouts are tried before giving up, and a recording with none is a
    recording that is not scored - not a failure of the expedition.
    """
    from pixel_patrol_deepsea.catalogue import truth_urls

    for url in truth_urls(expedition, video):
        try:
            truth = read_mot(_fetch_text(url))
        except Exception as exc:                     # 404 on the layout it does not use
            logger.debug("no ground truth at %s: %s", url, exc)
            continue
        if truth:
            return truth
    return {}


def _fetch_text(url: str) -> str:
    import urllib.request

    from pixel_patrol_deepsea.remote_file import open_with_retry

    with open_with_retry(urllib.request.Request(url), 120) as response:
        return response.read().decode(errors="replace")



# ── run ───────────────────────────────────────────────────────────────────────

# What one recording needs while it is being analysed, measured: a worker running
# the fused detector on HD footage is resident at about 4.8 GB, and on a 640x360
# proxy at about 3.5 GB. Six is that plus room for the pipeline around it.
GIGABYTES_PER_JOB = 6.0


# ── merge ─────────────────────────────────────────────────────────────────────

# The row group is sized once, in `reports`, because every pass that writes a report
# is making the same bargain with the viewer's range reads.



# ── combine ───────────────────────────────────────────────────────────────────

# ── site ──────────────────────────────────────────────────────────────────────

def build_site(root: Path, data_url: str = "") -> int:
    """A static viewer beside the parquets, and the page that indexes them.

    `data_url` says where the store and the reports will be served from, for a
    collection whose page and whose gigabytes part company: the page, the viewer,
    the taxonomy and the assets are a few tens of megabytes and go wherever pages
    go, while `tiles/` and `parquet/` are fifteen gigabytes and go on storage.
    The page then addresses those two absolutely and everything else beside
    itself. Left out, everything is one folder, which is what a laptop wants.
    """
    from pixel_patrol_base import api

    from pixel_patrol_deepsea.catalogue_page import write_assets, write_catalogue_page

    # Said once, at the top, where it can still be acted on.
    for path in unreadable(root):
        print(f"! {path.name}: {_why_unreadable(path, ValueError('unreadable'))}")
    # A recording's URL is joined out of the manifest it was listed from, so a
    # collection copied without its manifests builds a page whose pictures open
    # nothing. Silently, until now: the tiles are all there and every one of them
    # is a dead end.
    for report in sorted((root / "parquet").glob("*.parquet")):
        if report.stem.startswith("_"):
            continue
        if not (root / "manifests" / f"{report.stem}.json").is_file():
            print(f"! no manifests/{report.stem}.json - nothing in that expedition "
                  f"will have a recording to open. Copy it beside the reports.")

    # Always rebuilt, never skipped if it happens to exist. The site carries its own
    # copy of every widget, so a viewer left over from an earlier run serves the
    # widgets as they were then - and does it silently, which is the worst way for a
    # page to be wrong. Copying it again costs a second.
    #
    # build_viewer makes its own `viewer/` inside what it is given, so it gets the
    # collection root rather than the folder it is about to create.
    viewer = root / "viewer"
    api.build_viewer(root)
    _stamp_plugin_urls(viewer)
    print(f"viewer -> {viewer}")
    combine(root)
    _write_taxonomy(root)
    write_assets(root)
    # The collection's own colours, one stripe per recording, which the page opens
    # on. Seconds, and it reads no pictures - the colours are columns the processors
    # already measured.
    try:
        from pixel_patrol_deepsea.banner import build as build_banner

        drawn = build_banner(root)
        if drawn:
            print(f"banner -> {drawn}")
    except Exception as exc:
        logger.warning("could not draw the colour banner: %s", exc)
    # Every animal's picture, out of the reports and into a store the page can read
    # a screenful at a time. This is the slow part of a site build - it reads every
    # report - and it is what makes the page independent of how much was found.
    try:
        from pixel_patrol_deepsea.tiles import build as build_tiles

        index = build_tiles(root)
        print(f"tiles -> {root / 'tiles'} "
              f"({len(index['taxa'])} taxa, {sum(t['count'] for t in index['taxa'].values()):,} animals)")
    except Exception as exc:
        logger.warning("could not write the tile store: %s", exc)
        index = None
    page = write_catalogue_page(root, index=index, data_url=data_url)
    print(f"catalogue -> {page}")
    return 0


def build_page(root: Path, data_url: str = "") -> int:
    """The light half of a site: the page and the viewer, and no collection.

    `site` needs the reports - it cuts the tile store out of them, reads the
    expedition table out of them, draws the banner from their colours. That is
    fifteen gigabytes and a minute of reading, and it is exactly what a runner
    building a page does not have.

    This builds the same page from what `site` left behind: `collection.json` for
    the table, `tiles/index.json` for the taxonomy, and the code for everything
    else. Both are small enough to keep, and the pictures stay wherever
    `--data-url` says they are.
    """
    from pixel_patrol_base import api

    from pixel_patrol_deepsea.catalogue_page import PROGRESS, write_catalogue_page

    root.mkdir(parents=True, exist_ok=True)
    if not (root / PROGRESS).is_file():
        print(f"no {PROGRESS} in {root} - `collect site` writes it beside the page",
              file=sys.stderr)
        return 1
    api.build_viewer(root)
    _stamp_plugin_urls(root / "viewer")
    _write_taxonomy(root)
    page = write_catalogue_page(root, data_url=data_url)
    said = "over " + data_url if data_url else "with everything beside it"
    print(f"{page} {said}")
    return 0


def _stamp_plugin_urls(viewer: Path) -> None:
    """Put each plugin's own content hash in the URL the viewer asks for.

    The viewer reads `pp_extension_urls.json` with `no-store` and then fetches the
    manifests and plugins it names as ordinary URLs. Those paths never change, so a
    browser that has seen `plugin_deepsea.js` once keeps serving the copy it has:
    the page rebuilds, `index.html` is new because it is written inline, and the
    widgets are yesterday's - which looks exactly like a build that did not pick up
    the change, and was one afternoon spent proving that it had.

    A hash in the query string makes the URL change when the file does and stay put
    when it does not, so the browser reloads a rebuilt plugin and keeps a cached
    unchanged one. Done here rather than in the manifest the package ships, because
    it is a fact about a built copy and not about the source.
    """
    for manifest in sorted(viewer.glob("extensions/*/extension.json")):
        try:
            listed = json.loads(manifest.read_text())
        except Exception:
            continue
        stamped, changed = [], False
        for plugin in listed.get("plugins", []):
            name = str(plugin).split("?", 1)[0]
            beside = manifest.parent / name
            if not beside.is_file():
                stamped.append(plugin)
                continue
            digest = hashlib.sha256(beside.read_bytes()).hexdigest()[:12]
            stamped.append(f"{name}?v={digest}")
            changed = True
        if changed:
            listed["plugins"] = stamped
            manifest.write_text(json.dumps(listed, indent=2))


def _write_taxonomy(root: Path) -> None:
    """The detector's taxonomy beside the parquets, for the tree widget to read.

    Shipped as a file rather than compiled into the widget because it is 100 KB
    and most reports use a few dozen entries of it. The widget looks for it
    relative to the report it is showing and falls back to one ring of bare names
    when it is absent, so a report opened on its own still works.
    """
    from pixel_patrol_deepsea.fetch_taxonomy import taxonomy_path

    source = taxonomy_path()
    if not source.is_file():
        print("no taxonomy fetched; the tree will show bare names "
              "(python -m pixel_patrol_deepsea.fetch_taxonomy)")
        return
    (root / "taxonomy.json").write_bytes(source.read_bytes())
    print(f"taxonomy -> {root / 'taxonomy.json'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest="verb", required=True)

    listing = verbs.add_parser("list", help="write the video manifest for one expedition")
    listing.add_argument("expedition")
    listing.add_argument("-o", "--output", type=Path, required=True)

    choosing = verbs.add_parser("choose", help="the recordings of one expedition "
                                              "worth analysing, as a manifest")
    choosing.add_argument("expedition")
    choosing.add_argument("-o", "--output", type=Path, required=True)
    choosing.add_argument("-m", "--manifest", type=Path,
                          help="a manifest from `list`; listed afresh when absent")
    choosing.add_argument("--dives", type=int, default=0,
                          help="how many dives to take, deepest first (0 for all)")
    choosing.add_argument("--per-dive", type=int, default=0,
                          help="recordings per dive, spread over its bottom time")
    choosing.add_argument("--most", type=int, default=0, help="cap on recordings overall")

    single = verbs.add_parser("one", help="analyse one recording, keeping no copy")
    single.add_argument("url")
    single.add_argument("-o", "--output", type=Path, required=True)
    single.add_argument("-e", "--expedition", required=True)
    single.add_argument("--fps", type=float, default=10.0,
                        help="thin to this frame rate first (0 to keep the original)")
    single.add_argument("--slice-frames", type=int, default=SLICE_FRAMES)
    single.add_argument("--detector", default=DETECTOR)
    single.add_argument("--detector-sizes", default=DETECTOR_SIZES,
                        help="input sizes to read each frame at and fuse")
    single.add_argument("--detect-every", type=int, default=DETECT_EVERY,
                        help="seconds of footage between looks; 1 looks at every slice")
    single.add_argument("--detector-frames", type=int, default=DETECTOR_FRAMES,
                        help="frames per slice the detector looks at; more crops per "
                             "animal, proportionally more inference")

    nightly = verbs.add_parser("run", help="choose, analyse and merge one expedition")
    nightly.add_argument("expedition")
    nightly.add_argument("root", type=Path)
    nightly.add_argument("--jobs", type=int, default=4,
                         help="recordings analysed at once; each one is a core")
    nightly.add_argument("--dives", type=int, default=0,
                         help="how many dives to take, deepest first (0 for all)")
    nightly.add_argument("--per-dive", type=int, default=0,
                         help="recordings per dive, spread over its bottom time")
    nightly.add_argument("--most", type=int, default=0, help="cap on recordings overall")
    nightly.add_argument("--fps", type=float, default=10.0)
    nightly.add_argument("--slice-frames", type=int, default=SLICE_FRAMES)
    nightly.add_argument("--detector", default=DETECTOR)
    nightly.add_argument("--detector-frames", type=int, default=DETECTOR_FRAMES)
    nightly.add_argument("--detect-every", type=int, default=DETECT_EVERY,
                         help="seconds of footage between looks; 1 looks at every slice")
    nightly.add_argument("--detector-sizes", default=DETECTOR_SIZES,
                         help="input sizes to read each frame at and fuse; drop the "
                              "largest for a low-resolution archive")

    joining = verbs.add_parser("merge", help="one parquet per expedition")
    joining.add_argument("expedition")
    joining.add_argument("parts", nargs="+", type=Path)
    joining.add_argument("-o", "--output", type=Path, required=True)
    joining.add_argument("--with-clips", action="store_true",
                         # argparse runs help through %-formatting, so a literal
                         # per cent has to be doubled or `%%o` asks it for an octal
                         # integer and the verb cannot even print its own help.
                         help="keep the clip frames in the report. They are 84%% of "
                              "it and the tile store has them; this is for a "
                              "collection nobody has to move.")

    naming = verbs.add_parser("identify", help="write animal ids into reports that "
                                              "predate them")
    naming.add_argument("target", type=Path,
                        help="a parquet, or a collection root to walk")

    judging = verbs.add_parser("judge", help="re-judge what the footage in a report "
                                            "was doing, without touching anything else")
    judging.add_argument("target", type=Path,
                         help="a parquet, or a collection root to walk")

    slimming = verbs.add_parser("slim", help="drop the pictures a report holds twice "
                                            "and write the rest at the size they were seen")
    slimming.add_argument("target", type=Path,
                          help="a parquet, or a collection root to walk")

    scoring = verbs.add_parser("score", help="check an expedition against its ground truth")
    scoring.add_argument("expedition")
    scoring.add_argument("root", type=Path)

    site = verbs.add_parser("site", help="build the viewer and the catalogue page")
    site.add_argument("root", type=Path)
    site.add_argument("--data-url", default="",
                      help="where tiles/ and parquet/ will be served from, if not "
                           "from beside the page")

    paging = verbs.add_parser("page", help="the page and the viewer alone, from what "
                                          "`site` wrote beside them")
    paging.add_argument("root", type=Path)
    paging.add_argument("--data-url", default="",
                        help="where tiles/ and parquet/ are served from")

    serving = verbs.add_parser("serve", help="serve a built collection on localhost, "
                                            "byte ranges and all")
    serving.add_argument("root", type=Path)
    serving.add_argument("-p", "--port", type=int, default=8000)
    serving.add_argument("--host", default="127.0.0.1",
                         help="0.0.0.0 to offer it to the network as well")

    args = parser.parse_args(argv)
    if args.verb == "list":
        return list_videos(args.expedition, args.output)
    if args.verb == "choose":
        return choose_videos(args.expedition, args.output, args.manifest,
                             args.dives, args.per_dive, args.most)
    if args.verb == "one":
        return analyse_one(args.url, args.output, args.expedition,
                           args.fps or None, args.slice_frames, args.detector,
                           args.detector_frames, args.detector_sizes,
                           args.detect_every)
    if args.verb == "run":
        return run_expedition(args.expedition, args.root, args.jobs, args.dives,
                              args.per_dive, args.most, args.fps or None,
                              args.slice_frames, args.detector, args.detector_frames,
                              args.detector_sizes, args.detect_every)
    if args.verb == "identify":
        return identify_reports(args.target)
    if args.verb == "judge":
        return judge_reports(args.target)
    if args.verb == "slim":
        from pixel_patrol_deepsea.slim import slim_reports
        return slim_reports(args.target)
    if args.verb == "merge":
        return merge(args.expedition, args.parts, args.output, clips=args.with_clips)
    if args.verb == "score":
        return score(args.expedition, args.root)
    if args.verb == "page":
        return build_page(args.root, args.data_url)
    if args.verb == "serve":
        from pixel_patrol_deepsea.serve import serve
        return serve(args.root, args.port, args.host)
    return build_site(args.root, args.data_url)


if __name__ == "__main__":
    sys.exit(main())
