"""Reading footage: one recording, or a whole expedition of them.

    python -m pixel_patrol_deepsea.collect one <video-url> -o parts/x.parquet -e EX2107
    python -m pixel_patrol_deepsea.collect run EX2107 collection/

This is the expensive half of the pipeline and the only part that touches video.
A recording is staged into a scratch directory, thinned to the frame rate the
detector is going to read anyway, analysed, and deleted - so a run of any length
costs one recording of disk rather than the whole archive. Under Nextflow that
scratch directory is the task's own and the deletion is free.

What comes out is one parquet per recording, with the animals already identified
and the footage already judged, because both need the whole recording in hand and
this is the only place that has it.
"""

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
    catalogue_path, find_expedition, load_catalogue,
)
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
    from pixel_patrol_deepsea.triage import describe

    try:
        animals = identify(output)
    except Exception as exc:                 # a report is worth more than its ids
        logger.warning("could not identify animals in %s: %s", output, exc)
        animals = 0
    # What each slice was doing, decided here for the same reason: two of its
    # thresholds are percentiles of this recording's own movement, so it cannot be
    # judged one slice at a time. See `triage`.
    try:
        describe(output)
    except Exception as exc:
        logger.warning("could not judge the footage in %s: %s", output, exc)
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


