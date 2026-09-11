"""Collect an archive of expedition video into one parquet per expedition.

Four verbs, each doing one thing, so a scheduler can run them independently and
only redo what changed:

    python -m pixel_patrol_deepsea.collect list  EX2107 -o manifests/EX2107.json
    python -m pixel_patrol_deepsea.collect one   <video-url> -o parts/x.parquet -e EX2107
    python -m pixel_patrol_deepsea.collect merge EX2107 parts/*.parquet -o parquet/EX2107.parquet
    python -m pixel_patrol_deepsea.collect site  collection/

`one` is the expensive verb and the only one that touches video. It stages the
recording into a scratch directory, analyses it, and deletes it - so a run of any
length costs one recording of disk rather than the whole archive. Under Nextflow
that scratch directory is the task's own and the deletion is free.

Truly not touching disk would mean handing the pipeline a URL instead of a path,
and file discovery in pixel-patrol-base is filesystem-bound: os.walk for the tree,
os.stat for size and modification date, commonpath for the shared root. That wants
the source abstraction the S3 work is adding, not a patch here. Nothing is kept
either way; the difference is one recording of transient disk.
"""

import argparse
import functools
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import as_completed
from pathlib import Path
from typing import List, Optional

from pixel_patrol_deepsea.catalogue import (
    Manifest, catalogue_path, discover, find_expedition, load_catalogue,
)
from pixel_patrol_deepsea import locations
from pixel_patrol_deepsea.remote_file import open_with_retry

logger = logging.getLogger(__name__)

SLICE_FRAMES = 10            # 1 s at 10 fps, which is what --fps thins to
# Frames per slice the detector actually looks at. One, because the budget now goes
# on reading that frame at three sizes and fusing the answers, which is worth much
# more than three near-identical looks at the same moment: fusing took recall at 95%
# precision from 0.596 to 0.634 on the annotated sequences, where the extra frames
# bought nothing measurable. The tiles still move - a clip is consecutive frames
# cropped with one fixed box, and cropping is free.
DETECTOR_FRAMES = 1
# The 499-class generalist, not the fish-only model. On the same thirty frames of
# benthic dive footage the fish model returned six detections all called "fish",
# while this one named sponges Porifera, tube anemones Ceriantharia, feather stars
# Crinoidea and the eels Anguilliformes - and it has a class for the ROV's own
# equipment. A wall of animals is only as honest as the vocabulary behind it.
DETECTOR = "general"
# Which input sizes to read each frame at and fuse. The default suits HD footage.
# On a low-resolution archive it is worth overriding: measured on the annotated
# sequences shrunk to 640x360, the size of the NOAA proxies, recall at 95%
# precision was 0.581 for 640 alone, 0.593 adding 960 and 0.595 adding 1280 - so
# the third pass costs twice as much again for two thousandths. There is no detail
# in a proxy for a larger input to find; upscaling cannot put back what the
# encoder threw away. On full-resolution footage the same three sizes are worth
# 0.596 -> 0.634, which is why they are the default.
DETECTOR_SIZES = "640,960,1280"
# How many seconds of footage between looks. This was five, on the reasoning that
# an animal stays in frame for a median of 182 frames in midwater and 290 on the
# bottom, so a coarse read would still see essentially every distinct animal.
# Measured against DeepSea-MOT, counting distinct animals rather than boxes, that
# reasoning was wrong:
#
#   0.30 s apart   0.83 of the animals
#   0.60 s         0.83
#   0.90 s         0.79
#   1.50 s         0.76
#   3.75 s         0.63
#
# Five seconds is off the bottom of that. One second costs five times the inference
# and is worth it; below half a second the curve is flat and it is not. Precision
# is 0.96-0.97 at every interval, so this buys recall and risks nothing - a frame
# nobody reads cannot produce a false positive.
#
# Independent of the slice length since `_moments_to_read`: a five-second slice
# with a one-second interval is read at five moments spread through it.
DETECT_EVERY = 1
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

def analyse_one(url: str, output: Path, expedition_id: str, fps: Optional[float],
                slice_frames: int, detector: str, detector_frames: int,
                detector_sizes: str = DETECTOR_SIZES,
                detect_every: int = DETECT_EVERY) -> int:
    """Analyse a single recording, keeping no copy of it."""
    _insist_on_the_detector(detector)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="footage_", dir=os.environ.get("TMPDIR")) as scratch:
        stage = Path(scratch) / "video"
        stage.mkdir()
        local = stage / _filename_of(url)
        _fetch(url, local)
        if not fps and _frame_count_is_wrong(local):
            # Analysing the original is the better read - the thinning transcode
            # costs real animals - but it depends on the container's frame count
            # being true, and some are not. The loader promises dask a chunk of
            # the length the header claims and then decodes fewer, and the whole
            # recording is lost to a shape mismatch. Re-encoding rewrites the
            # count from what is actually there, so it is the repair as well as
            # the frame-rate knob.
            logger.warning("%s: the container's frame count is wrong; re-encoding it",
                           local.name)
            local = _thin(local, 0)
        elif fps:
            local = _thin(local, fps)
        # Resolved before the pipeline starts and passed in by environment: the
        # position depends on the recording's name and on the archive's dive log,
        # neither of which a processor can see. `_thin` keeps the published name,
        # so the timestamp in it survives the transcode.
        where = locations.stage(_location_recipe(expedition_id), url, Path(scratch))
        _process(stage, output, expedition_id, url, slice_frames, detector,
                 detector_frames, where, detector_sizes, detect_every)
    # The pipeline warns and carries on when a loader cannot read a file, so a run
    # that processed nothing still exits zero. Saying "written" about a file that is
    # not there sends a scheduler on to the merge with a hole in it.
    if not output.exists() or output.stat().st_size == 0:
        print(f"nothing was processed for {url} - no report written", file=sys.stderr)
        return 1
    # Which detections are the same animal, decided here because this is the only
    # place that holds a whole recording, and written into the report so the page,
    # a notebook and anything else all read one answer instead of each deriving
    # their own. See `identity`.
    from pixel_patrol_deepsea.identity import identify

    try:
        animals = identify(output)
    except Exception as exc:                 # a report is worth more than its ids
        logger.warning("could not identify animals in %s: %s", output, exc)
        animals = 0
    print(f"{output} written; the recording was not kept"
          + (f"; {animals} animals" if animals else ""))
    return 0


def _insist_on_the_detector(wanted: str) -> None:
    """Refuse to analyse footage with the detector missing, rather than quietly not.

    A processor that cannot run is not registered, and `--processors-include` for a
    name nothing registered asks for nothing and gets it. So a worker without the
    detector produces a perfectly good report with no animals in it, exits zero,
    and the failure is invisible until someone opens the collection - which is how
    282 recordings came back from a cluster with positions, colour, motion and not
    one detection, because the compute nodes never saw XDG_CACHE_HOME.

    Analysing without it is a legitimate thing to want; it just has to be asked for.
    """
    from pixel_patrol_deepsea import detector as engine

    if wanted in ("", "none", "off"):
        return
    # Loading it, not just finding it. `is_available` asks whether the weights, the
    # code, cv2 and torch are present, and all four were the day the detector
    # warned "No module named 'requests'" on every slice of 282 recordings and
    # wrote no detections. Whatever the next missing piece turns out to be, trying
    # the thing catches it; enumerating its dependencies does not. It costs a
    # couple of seconds, once, against an hour of analysis.
    try:
        engine.load_detector()
        return
    except Exception as exc:
        if engine.is_available():
            raise SystemExit(
                f"the detector is there but will not load: {exc}\n"
                f"  cache: {engine.CACHE}\n"
                f"This is usually a missing dependency of the YOLOv5 code it loads. "
                f"Reinstalling the package pulls them in:\n"
                f"    python -m pip install -e <this checkout>\n"
                f"To analyse without a detector on purpose, pass --detector none.")
    raise SystemExit(
        f"no detector, so nothing would be found and the report would not say so.\n"
        f"  looked in : {engine.CACHE}\n"
        f"  set by    : XDG_CACHE_HOME={os.environ.get('XDG_CACHE_HOME', '<unset>')}\n"
        f"Fetch one, or point XDG_CACHE_HOME at the cache that has it:\n"
        f"    python -m pixel_patrol_deepsea.fetch_detector --model {wanted or 'general'}\n"
        f"To analyse without a detector on purpose, pass --detector none.")


def _filename_of(url: str) -> str:
    name = Path(urllib.parse.urlparse(url).path).name
    return name or "video.mp4"


def _fetch(url: str, destination: Path) -> None:
    with open_with_retry(urllib.request.Request(url), 600) as response, \
            open(destination, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)


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


def _frame_count_is_wrong(video: Path) -> bool:
    """Whether the container claims more frames than it will actually decode.

    It has to be the decoded count, not the packet count: MD_FLN.mp4 of
    DeepSea-MOT claims 617 frames and holds 618 packets, and decodes 600. The
    surplus packets carry no picture, so anything short of decoding agrees with
    the header and misses it.

    That makes this a whole decode pass, which is why it only runs when the
    recording is about to be analysed unthinned. A thinning transcode already
    rewrites the count as a side effect, so there is nothing to check; and when
    the count is wrong the alternative is losing the recording entirely.
    """
    try:
        import av
        with av.open(str(video)) as container:
            stream = container.streams.video[0]
            claimed = stream.frames or 0
            if not claimed:
                return False
            stream.thread_type = "AUTO"
            decoded = sum(1 for _ in container.decode(stream))
        if decoded < claimed:
            logger.info("%s: header claims %d frames, %d decode",
                        video.name, claimed, decoded)
            return True
        return False
    except Exception as exc:
        logger.warning("cannot check the frame count of %s: %s", video.name, exc)
        return False


@functools.lru_cache(maxsize=1)
def ffmpeg() -> str:
    """The ffmpeg to thin recordings with, wherever this environment keeps one.

    Not a hard dependency of the package and not something pip installs, so on a
    cluster it is missing about as often as it is present - and what that looked
    like was a FileNotFoundError from deep inside subprocess, once per task, after
    the recording had already been fetched. PyAV's bundled libraries are no help
    here: they are libraries, and this needs the program.

    `PIXEL_PATROL_FFMPEG` wins, then PATH, then the static binary that comes with
    imageio-ffmpeg if that happens to be installed.
    """
    stated = os.environ.get("PIXEL_PATROL_FFMPEG")
    if stated:
        return stated
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        pass
    raise RuntimeError(
        "no ffmpeg, which is what thins a recording before it is analysed. Install "
        "one into this environment:\n"
        "    micromamba install -c conda-forge ffmpeg\n"
        "    pip install imageio-ffmpeg\n"
        "or point PIXEL_PATROL_FFMPEG at one. Analysing without thinning (--fps 0) "
        "still needs it wherever a container's frame count has to be repaired.")


def _thin(video: Path, fps: float) -> Path:
    """Re-encode to a lower frame rate, in place of the original.

    Motion and detection both work fine at 10 fps and the decode is the largest
    single cost in the pass, so this is usually worth its own transcode. A rate of
    zero keeps every frame and re-encodes anyway, which is what repairs a
    container whose frame count cannot be trusted.
    """
    # Written aside and renamed back, so the recording keeps its published name. A
    # thinned copy called `..._10fps.mp4` puts a transcoding detail into the name
    # every widget shows and stops it matching the manifest that listed it.
    thinned = video.with_name(f"thinning_{video.name}")
    # Dropping the frame rate is a decision about cost and accepts a quality
    # setting with it. Repairing a frame count is not a decision about anything -
    # nobody asked for a worse picture - so that path re-encodes to FFV1 and the
    # result is identical to the source, checked frame by frame.
    #
    # It has to be FFV1 rather than x264 in a lossless mode, and finding that out
    # took measuring rather than reading. x264 at `-qp 0` is genuinely bit-exact in
    # the source's own colour planes - but it tags the output's colour range where
    # the source left it unstated, and everything downstream decodes to RGB, so the
    # pixels the pipeline actually sees came out up to 26 levels different and 1.1
    # different on average. `-crf 0` is not even lossless on a 10-bit source, where
    # the quantiser range extends below zero. FFV1 measured zero difference in RGB.
    #
    # The price is size: about 2.7 MB a frame for HD 4:2:2 10-bit, so this is only
    # affordable because it is reached only on the unthinned path, for short
    # sequences, into a scratch directory that is deleted either way. The muxer is
    # forced rather than taken from the name, because the recording keeps its
    # published name - which every widget shows and the manifest matches on.
    if fps:
        codec = ["-r", str(fps), "-c:v", "libx264", "-crf", THINNED_QUALITY,
                 "-preset", "veryfast"]
    else:
        codec = ["-c:v", "ffv1", "-f", "matroska"]
    subprocess.run([ffmpeg(), "-nostdin", "-v", "error", "-y", "-i", str(video),
                    *codec, "-an", str(thinned)], check=True)
    video.unlink()
    thinned.rename(video)
    return video


def _location_recipe(expedition_id: str) -> dict:
    """How to place this expedition's footage, from the catalogue entry."""
    try:
        return find_expedition(load_catalogue(catalogue_path()), expedition_id).location or {}
    except Exception as exc:
        logger.warning("no location recipe for %s: %s", expedition_id, exc)
        return {}


def _process(folder: Path, output: Path, expedition_id: str, url: str,
             slice_frames: int, detector: str, detector_frames: int,
             where: Optional[dict] = None,
             detector_sizes: str = DETECTOR_SIZES,
             detect_every: int = DETECT_EVERY) -> None:
    environment = {
        **os.environ,
        **(where or {}),
        # Measured: many single-threaded processes beat one many-threaded one by
        # 1.8x, and torch would otherwise take a thread per core for each task.
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "1"),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS", "1"),
        "PIXEL_PATROL_DETECTOR": detector,
        # Three sizes, fused, rather than one chosen. The size decides which animals
        # can be seen at all and no single one is right: 640 finds the obvious, 1280
        # the small, and full resolution is worse than either because the model has a
        # scale it expects animals to arrive at. Fusing the three beat every single
        # size on all five annotated sequences. See `detector.FUSED_SIZES` for the
        # numbers and for why tiling the frame, the obvious alternative, was worse.
        "PIXEL_PATROL_DETECTOR_SIZES": detector_sizes,
        # The confidence floor is deliberately not set here. It belongs to the
        # detector, which documents what it costs, and pinning a second copy of it
        # next to the first is how this one sat at 0.25 - throwing away a fifth of
        # the animals in the footage - long after that stopped being the intent.
        "PIXEL_PATROL_DETECTOR_EVERY": str(detect_every),
        "PIXEL_PATROL_DETECTOR_FRAMES": str(detector_frames),
    }
    # Run the pipeline through *this* interpreter rather than through whatever
    # `pixel-patrol` PATH resolves to. They are not always the same install - a
    # conda environment with an older copy of this package sitting ahead of the one
    # that launched the run is an ordinary way to have a machine set up - and when
    # they differ the failure is silent: the processors this package registers come
    # from the other copy, so a processor added here is simply absent from the
    # report, with no error anywhere. `--processors-include` for a name that does
    # not exist there asks for nothing and gets it.
    command = [sys.executable, "-m", "pixel_patrol_base.cli",
               "process", str(folder), "-o", str(output),
               "--loader", "video", "--slice-size", f"T={slice_frames}",
               # Colour axis whole, so the detector works in RGB and its crops
               # need no second pass to become colour.
               "--slice-size", "C=-1",
               "--name", expedition_id,
               "--description", f"Expedition {expedition_id}. Source: {url}",
               # One worker, one thread. The parallelism lives one level up, in
               # whatever runs `collect one` for each recording: dask workers here
               # would each load the model again and then fight the scheduler's
               # tasks for the same cores.
               # --mb-per-task is also the worker's memory limit, and reading one
               # frame at three sizes needs more than reading it at one: the
               # measured resident size of a worker running the fused detector on
               # HD footage is around 4.8 GB, which the old 4096 sat right on top
               # of. A worker killed for memory produces no rows and says so only
               # in the pipeline's log, so the headroom is worth more than the
               # number looks. It also caps how many recordings fit at once -
               # roughly (RAM / 6 GB), not the core count.
               "--max-workers", "1", "--mb-per-task", "8192", "--omit-base-dir"]
    for processor in PROCESSORS:
        command += ["--processors-include", processor]
    subprocess.run(command, check=True, env=environment)


# ── identify ──────────────────────────────────────────────────────────────────

def identify_reports(target: Path) -> int:
    """Write animal ids into reports made before `collect one` wrote them itself.

    Backfill, and only that: the ids are derived from the detections already in the
    file, so a report analysed last week gets exactly the identity it would have
    been given at the time. Nothing is re-read and no footage is touched.
    """
    from pixel_patrol_deepsea.identity import identify

    reports = ([target] if target.is_file()
               else sorted(p for p in target.rglob("*.parquet")
                           if p.parent.name != "sightings"))
    if not reports:
        print(f"no reports under {target}", file=sys.stderr)
        return 1
    total = 0
    for report in reports:
        try:
            animals = identify(report)
        except Exception as exc:
            print(f"{report}: {exc}", file=sys.stderr)
            continue
        total += animals
        print(f"{report.relative_to(target) if target.is_dir() else report.name}: "
              f"{animals} animals")
    print(f"{len(reports)} reports, {total} animals")
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

def run_expedition(expedition_id: str, root: Path, jobs: int, dives: int,
                   per_dive: int, most: int, fps: Optional[float], slice_frames: int,
                   detector: str, detector_frames: int,
                   detector_sizes: str = DETECTOR_SIZES,
                   detect_every: int = DETECT_EVERY) -> int:
    """List an expedition, choose what is worth analysing, analyse it, merge it.

    The four verbs exist so a scheduler can drive them; this is the verb for when
    the scheduler is one machine and a night. It differs from running `one` in a
    loop in the two ways that decide whether the night was well spent: it chooses
    the recordings rather than taking the first few (see `selection`), and it runs
    several at once.

    Parallelism lives here rather than inside the pipeline on purpose. Each
    recording is a whole `pixel-patrol process` with one worker, so N recordings
    at once is N cores with no scheduler contention and no N copies of the model
    fighting for the same threads - which is what dask workers inside one
    recording turned into.
    """
    from concurrent.futures import ProcessPoolExecutor

    from pixel_patrol_deepsea import selection

    expedition = find_expedition(load_catalogue(catalogue_path()), expedition_id)
    manifest = root / "manifests" / f"{expedition_id}.json"
    if manifest.is_file():
        videos = json.loads(manifest.read_text())["videos"]
    else:
        listed = discover(expedition)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(listed.to_json())
        videos = listed.videos
    chosen = selection.choose(expedition, videos, dives=dives, per_dive=per_dive, most=most)
    print(selection.report(expedition, videos, chosen), flush=True)
    jobs = _jobs_that_fit(jobs)

    parts = root / "parts" / expedition_id
    parts.mkdir(parents=True, exist_ok=True)
    todo = [(url, parts / f"{Path(url.split('?')[0]).stem}.parquet") for url in chosen]
    # A recording already analysed is not analysed again, so a night that ran out
    # of time is resumed rather than restarted.
    todo = [(url, out) for url, out in todo if not (out.exists() and out.stat().st_size)]
    print(f"{len(todo)} to analyse, {len(chosen) - len(todo)} already done", flush=True)

    failed = 0
    with ProcessPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(_analyse_quietly, url, out, expedition_id, fps,
                               slice_frames, detector, detector_frames,
                               detector_sizes, detect_every): (url, out)
                   for url, out in todo}
        for done, future in enumerate(as_completed(futures), 1):
            url, out = futures[future]
            try:
                code = future.result()
            except Exception as exc:
                logger.warning("%s raised: %s", Path(url).name, exc)
                code = 1
            failed += bool(code)
            print(f"[{done}/{len(todo)}] {Path(url).name} "
                  f"{'ok' if not code else 'FAILED'}", flush=True)

    made = sorted(parts.glob("*.parquet"))
    if made:
        merge(expedition_id, made, root / "parquet" / f"{expedition_id}.parquet")
    if failed:
        print(f"{failed} recordings failed", file=sys.stderr)
    return 0 if made else 1


# What one recording needs while it is being analysed, measured: a worker running
# the fused detector on HD footage is resident at about 4.8 GB, and on a 640x360
# proxy at about 3.5 GB. Six is that plus room for the pipeline around it.
GIGABYTES_PER_JOB = 6.0


def _jobs_that_fit(asked: int) -> int:
    """As many recordings at once as there is memory for, not as there are cores.

    Worth a guard rather than a comment because of how this fails. A worker killed
    for memory does not raise: it produces no rows, the pipeline warns in a log
    nobody is reading, and the recording ends up in the report as a file that
    happened to contain nothing. That is indistinguishable from footage with no
    animals in it, which is the one thing this collection must not get wrong.

    Only ever reduces. Someone who asks for two on a large machine gets two.
    """
    try:
        available = _available_gigabytes()
    except Exception:
        return asked
    fits = max(1, int(available / GIGABYTES_PER_JOB))
    if fits < asked:
        print(f"{available:.0f} GB free: analysing {fits} recordings at once, "
              f"not {asked} - a worker killed for memory writes no rows and says so "
              f"only in its own log", flush=True)
        return fits
    return asked


def _available_gigabytes() -> float:
    """Memory the kernel says is available, page cache included as reclaimable."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / (1 << 20)
    raise LookupError("no MemAvailable in /proc/meminfo")


def _analyse_quietly(url: str, output: Path, expedition_id: str, fps, slice_frames: int,
                     detector: str, detector_frames: int, detector_sizes: str,
                     detect_every: int) -> int:
    """One recording, in its own process, with its own log file beside its parquet.

    The pipeline is talkative and several at once interleave into nonsense, so each
    recording's output goes to its own file - which is also where to look when one
    of them fails.
    """
    log = output.with_suffix(".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    # Redirected at the file descriptor, not just at sys.stdout. The expensive
    # part of analysing a recording is a `pixel-patrol process` subprocess, and a
    # subprocess inherits descriptors 1 and 2 rather than whatever Python has
    # rebound its own names to - so rebinding alone left every recording's real
    # output going to the shared terminal, interleaved with five others, and left
    # these files empty.
    with open(log, "w") as stream:
        kept = os.dup(1), os.dup(2)
        os.dup2(stream.fileno(), 1)
        os.dup2(stream.fileno(), 2)
        try:
            return analyse_one(url, output, expedition_id, fps, slice_frames,
                               detector, detector_frames, detector_sizes,
                               detect_every)
        except Exception as exc:
            print(f"failed: {exc}", flush=True)
            return 1
        finally:
            os.dup2(kept[0], 1)
            os.dup2(kept[1], 2)
            os.close(kept[0])
            os.close(kept[1])


# ── merge ─────────────────────────────────────────────────────────────────────

def merge(expedition_id: str, parts: List[Path], output: Path) -> int:
    """One parquet per expedition, from one parquet per recording.

    Rows are concatenated rather than recomputed: in pixel-patrol a video file is
    one image, so every level of the aggregation tree in a part already belongs to
    that recording alone and nothing needs re-rolling up.
    """
    import polars as pl
    import pyarrow as pa
    import pyarrow.parquet as pq

    usable = [p for p in parts if p.exists() and p.stat().st_size > 0]
    if not usable:
        print("nothing to merge")
        return 1
    table = pl.concat([pl.read_parquet(p) for p in usable], how="diagonal_relaxed")
    expedition = _describe(expedition_id)
    arrow = table.to_arrow()
    arrow = arrow.replace_schema_metadata({
        **(arrow.schema.metadata or {}),
        b"pp_project_name": expedition.title.encode(),
        b"pp_description": (f"{expedition.notes} {expedition.archive}, "
                            f"{expedition.vessel}, {expedition.date}. "
                            f"{len(usable)} recordings.").strip().encode(),
        b"pp_loader": b"video",
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(arrow, output)
    print(f"{expedition.title}: {len(usable)} recordings, {len(table):,} rows -> {output}")
    return 0


def _describe(expedition_id: str):
    from pixel_patrol_deepsea.catalogue import Expedition
    try:
        return find_expedition(load_catalogue(catalogue_path()), expedition_id)
    except Exception:
        return Expedition(id=expedition_id, listing="", name=expedition_id)



# ── combine ───────────────────────────────────────────────────────────────────

# What makes a report big is the pictures: a slice carries a cached still, a
# close-up of its most convincing animal, and ten frames of clip for each of six
# animals. On the Axial Seamount report that is 190 MB of the 363 MB in the
# collection, and none of it is what a combined report is for.
PICTURE_COLUMNS = ("slice_thumbnail", "detection_crop", "detections")

EVERYTHING = "_everything"


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
        table = pl.read_parquet(path)
        # Which expedition a row came from has to survive the concatenation, or
        # the combined report can group by everything except the thing a reader
        # most wants to group by.
        table = table.drop([c for c in PICTURE_COLUMNS if c in table.columns])
        table = table.with_columns(pl.lit(_describe(path.stem).title).alias("expedition"))
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


# ── site ──────────────────────────────────────────────────────────────────────

def build_site(root: Path) -> int:
    """A static viewer beside the parquets, and the page that indexes them."""
    from pixel_patrol_base import api

    from pixel_patrol_deepsea.catalogue_page import write_catalogue_page

    # Always rebuilt, never skipped if it happens to exist. The site carries its own
    # copy of every widget, so a viewer left over from an earlier run serves the
    # widgets as they were then - and does it silently, which is the worst way for a
    # page to be wrong. Copying it again costs a second.
    #
    # build_viewer makes its own `viewer/` inside what it is given, so it gets the
    # collection root rather than the folder it is about to create.
    viewer = root / "viewer"
    api.build_viewer(root)
    print(f"viewer -> {viewer}")
    combine(root)
    _write_taxonomy(root)
    page = write_catalogue_page(root)
    print(f"catalogue -> {page}")
    return 0


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

    naming = verbs.add_parser("identify", help="write animal ids into reports that "
                                              "predate them")
    naming.add_argument("target", type=Path,
                        help="a parquet, or a collection root to walk")

    scoring = verbs.add_parser("score", help="check an expedition against its ground truth")
    scoring.add_argument("expedition")
    scoring.add_argument("root", type=Path)

    site = verbs.add_parser("site", help="build the viewer and the catalogue page")
    site.add_argument("root", type=Path)

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
    if args.verb == "merge":
        return merge(args.expedition, args.parts, args.output)
    if args.verb == "score":
        return score(args.expedition, args.root)
    return build_site(args.root)


if __name__ == "__main__":
    sys.exit(main())
