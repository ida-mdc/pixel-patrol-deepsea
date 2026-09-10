"""Build a deep-sea footage report from public expedition video, end to end.

Fetches a little open footage, processes it into one parquet, and opens the report.
Everything is cached, so a second run is fast and a lost report is one command away.

    python deepsea_report.py                 # midwater sequences with animals in them
    python deepsea_report.py --with-atolla   # plus one named jellyfish, read out of a remote zip
    python deepsea_report.py --with-noaa     # plus a full-length NOAA dive tape
    python deepsea_report.py --refine        # revisit detections closely and write crops
    python deepsea_report.py --with-specimens # a clip of every named midwater specimen
    python deepsea_report.py --index         # rewrite the page listing every report here
    python deepsea_report.py --view          # skip processing, just open what is there

The detector is optional. Without it you still get movement, events, stills and every
widget; with it you also get animal counts and names:

    python -m pixel_patrol_deepsea.fetch_detector
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional

DATA = Path(__file__).resolve().parent / "data"
RAW = DATA / "raw"
CLIPS = DATA / "clips"
REPORT = DATA / "midwater.parquet"
CROPS = DATA / "crops"
SIGHTINGS = DATA / "sightings.csv"
SIGHTINGS_TABLE = DATA / "sightings.parquet"
SOURCES = DATA / "sources.json"

HUGGINGFACE = "https://huggingface.co/datasets/MBARI-org/DeepSea-MOT/resolve/main/data"
NCEI = "https://www.ncei.noaa.gov/data/oceans/oer/video/DeepSeaCorals2004/Video/Compressed"

# MBARI midwater sequences: animals drifting in open water, which is what the
# FathomNet midwater detector was trained on. Each ships per-frame annotations, so
# what the detector claims can be checked rather than believed.
MIDWATER = ["MWD", "MWS"]

# One full-length benthic dive tape, for widgets that only mean something over an
# hour of footage. Expect no true detections here: a midwater model reads corals and
# equipment on a lit seafloor as jellyfish, confidently.
NOAA_TAPE = "DEEPSEACORALS2004_VID_20041002_ROS_DIVE_P5-582_TAPE1OF7_SEG2OF2_PROXY.MP4"

# Seven Zenodo records, each a zip holding one 4K ten-minute recording of a single
# midwater specimen, published CC-BY-4.0 by John Burns and Brennan Phillips (with
# David Gruber on the first). The records themselves name no cruise; their data
# descriptor (Scientific Data 11, 2024, doi 10.1038/s41597-024-03533-4) puts the
# collection in the Eastern Pacific in August 2021, which is Schmidt Ocean's
# "Designing the Future 2" - FK210812, R/V Falkor, ROV SuBastian. RAD2 is the
# Rotary Actuated Dodecahedron sampler the animals were caught with.
#
# The taxon in each title is the useful part here: it is a label the detector has
# never seen, which is what makes these worth processing.
SPECIMENS = {
    "10987660": ("RAD2-005_Atolla", 241.0),
    "10987828": ("RAD2-026_Apolemia", None),
    "10988012": ("RAD2-027_Praya", None),
    "10988194": ("RAD2-054_Halistemma", None),
    "10988949": ("RAD2-055_Bathochordaeus", None),
    "10998462": ("RAD2-059_Pyrosomatidae", None),
    "10998479": ("RAD2-061_Lampocteis", None),
}

# What the detector should be calling each specimen, where its vocabulary covers it.
# The records are named after the animal in them, which makes this the one place in the
# pipeline with a free ground truth - and gives the probes something better to look for
# than "whatever scored highest", which can easily be a different animal that happened
# to drift through the sample.
SPECIMEN_CLASSES = {
    "Atolla": ["scyphozoa"],
    "Apolemia": ["physonectae nectosome"],
    "Praya": ["prayidae nectosome", "calycophorae nectosome"],
    "Halistemma": ["physonectae nectosome"],
    "Bathochordaeus": ["bathochordaeus", "bathochordaeus inner filter",
                       "bathochordaeus outer filter"],
    "Pyrosomatidae": ["pyrosoma"],
    "Lampocteis": ["lobata"],
}


def _taxon_of(label: str) -> str:
    """`RAD2-005_Atolla` -> `Atolla`: the part the detector could agree with."""
    return label.split("_")[-1]


ATOLLA_RECORD = "10987660"
ATOLLA_AT_SECOND = 241.0


def download(url: str, destination: Path) -> Path:
    if destination.exists():
        print(f"  have {destination.name}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {destination.name}")
    partial = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(url, timeout=600) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    partial.rename(destination)
    return destination


def transcode(source: Path, destination: Path, width: int = 1920) -> Path:
    """Shrink 4K ProRes to something a report can be built from repeatedly."""
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
         "-vf", f"scale={width}:-2", "-c:v", "libx264", "-crf", "20", "-an", str(destination)],
        check=True)
    return destination


def note_source(clip: Path, url: str, cited_as: str, second: Optional[float] = None) -> None:
    """Record where a clip came from, beside the report rather than inside it.

    A tile in the gallery is worth nothing to someone who cannot say which archive
    it came out of. The clips are cut from public recordings, so the link back is
    the whole provenance - and it belongs somewhere the viewer and the index page
    can both read without re-deriving it.
    """
    existing = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    existing[clip.name] = {"url": url, "cited_as": cited_as,
                           **({"second": round(second, 1)} if second is not None else {})}
    SOURCES.parent.mkdir(parents=True, exist_ok=True)
    SOURCES.write_text(json.dumps(dict(sorted(existing.items())), indent=2))


def gather(with_noaa: bool) -> None:
    """Fetch what is needed, keep only what is small.

    The MBARI sequences ship as ~570 MB of 4K ProRes each and shrink to about
    15 MB once transcoded, so the original is discarded as soon as the clip
    exists. That keeps the checked-out copy something you can afford to keep
    around, and re-running costs nothing.
    """
    for sequence in MIDWATER:
        clip = CLIPS / f"MBARI_{sequence}_midwater.mp4"
        if not clip.exists():
            raw = download(f"{HUGGINGFACE}/{sequence}/{sequence}.mov", RAW / f"{sequence}.mov")
            transcode(raw, clip)
            raw.unlink()
        else:
            print(f"  have {clip.name}")
        download(f"{HUGGINGFACE}/{sequence}/gt.txt", RAW / f"{sequence}_gt.txt")
        note_source(clip, f"{HUGGINGFACE}/{sequence}/{sequence}.mov",
                    f"MBARI DeepSea-MOT, sequence {sequence}")
    if with_noaa:
        tape = download(f"{NCEI}/{NOAA_TAPE}", CLIPS / "NOAA_P5-582_benthic_dive.mp4")
        note_source(tape, f"{NCEI}/{NOAA_TAPE}",
                    "NOAA Ocean Exploration, Deep Sea Corals 2004, dive P5-582")


def fetch_atolla() -> None:
    """Pull ~20 s out of a 7.5 GB Zenodo archive without downloading the archive."""
    from pixel_patrol_deepsea.remote_archive import RemoteVideo, zenodo_file_url

    clip = CLIPS / f"{SPECIMENS[ATOLLA_RECORD][0]}.mp4"
    if clip.exists():
        print(f"  have {clip.name}")
        return
    sparse = RAW / "atolla_sparse.mp4"
    sparse.parent.mkdir(parents=True, exist_ok=True)
    video = RemoteVideo(zenodo_file_url(ATOLLA_RECORD), sparse)
    print(f"  {video.name}: {video.size / 1e9:.1f} GB in the archive")
    video.fetch_index()
    video.fetch_seconds(ATOLLA_AT_SECOND, span=18)
    print(f"  fetched {video.disk_used_mb():.0f} MB of it")
    clip.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(ATOLLA_AT_SECOND - 2),
         "-t", "16", "-i", str(sparse), "-vf", "scale=1920:-2",
         "-c:v", "libx264", "-crf", "20", "-an", str(clip)], check=True)
    sparse.unlink()


def fetch_specimens(records=None, probe_mb: int = 90, clip_seconds: float = 16.0) -> None:
    """Pull one clip per specimen record, without downloading the archives.

    Each record is a 7-22 GB zip holding one 4K ten-minute recording. Where the
    animal is inside it is not published, so for the ones we do not already know,
    the recording is sampled at a handful of points and the detector says which
    sample is worth keeping. Only the winning stretch is then materialised.
    """
    wanted = records or SPECIMENS
    for record, (label, known_second) in wanted.items():
        try:
            _fetch_specimen(record, label, known_second, probe_mb, clip_seconds)
        except Exception as exc:      # Zenodo 504s under load; one record is not the run
            print(f"  {label}: {type(exc).__name__}: {str(exc)[:80]}")
    _fill_gaps(wanted, clip_seconds)


FALLBACK_SECOND = 240.0


def _citation(record: str, clip: Path) -> str:
    """How to cite a record, without letting the lookup decide whether the run happens.

    Zenodo answers 504 under any real load, and a clip we already have on disk does
    not need the network to be usable. So an answer we recorded earlier wins, and a
    failed lookup leaves the DOI rather than an exception.
    """
    from pixel_patrol_deepsea.remote_archive import zenodo_citation

    try:
        known = json.loads(SOURCES.read_text()).get(clip.name, {}).get("cited_as")
    except Exception:
        known = None
    if known:
        return known
    try:
        return zenodo_citation(record)
    except Exception as exc:
        print(f"    citation lookup failed ({type(exc).__name__}); keeping the DOI")
        return f"Zenodo record {record}"


def _fetch_specimen(record: str, label: str, known_second: Optional[float],
                    probe_mb: int, clip_seconds: float) -> None:
    from pixel_patrol_deepsea.remote_archive import RemoteVideo, zenodo_file_url

    clip = CLIPS / f"{label}.mp4"
    doi = f"https://doi.org/10.5281/zenodo.{record}"
    if clip.exists():
        print(f"  have {clip.name}")
        note_source(clip, doi, _citation(record, clip), known_second)
        return
    # Per process: the file is deleted when the record is done, so two runs going at
    # once would otherwise pull the ground out from under each other.
    sparse = RAW / f"zenodo_{record}_{os.getpid()}.mp4"
    try:
        video = RemoteVideo(zenodo_file_url(record), sparse)
        print(f"  {label}: {video.size / 1e9:.1f} GB archive")
        video.fetch_index()
        second = (known_second if known_second is not None
                  else _find_animal(video, probe_mb, SPECIMEN_CLASSES.get(_taxon_of(label), [])))
        if second is None:
            print("    nothing found in the samples; skipping")
            return
        video.fetch_seconds(second, span=clip_seconds)
        _cut(sparse, clip, second - 2, clip_seconds)
        note_source(clip, doi, _citation(record, clip), second)
        print(f"    {label} at {second:.0f}s -> {clip.name} ({video.disk_used_mb():.0f} MB fetched)")
    except Exception as exc:
        print(f"  {label}: {type(exc).__name__}: {str(exc)[:80]}")
    finally:
        sparse.unlink(missing_ok=True)


def _fill_gaps(records, clip_seconds: float) -> None:
    """Take a clip anyway from every record the first pass came back empty on.

    A record is missing here because the fetch failed or the probes saw nothing -
    never because the animal is absent, since the archive is named after it. Cutting
    from the middle costs one fetch and no inference, and an unlabelled clip of a
    named specimen beats no clip at all.
    """
    missing = [(record, label) for record, (label, _) in records.items()
               if not (CLIPS / f"{label}.mp4").exists()]
    if not missing:
        return
    print(f"\n  {len(missing)} record(s) with no clip yet; cutting from the middle")
    for record, label in missing:
        _fetch_specimen(record, label, FALLBACK_SECOND, 0, clip_seconds)


PROBE_SECONDS = [60, 150, 240, 330, 420, 510]


def _find_animal(video, probe_mb: int, wanted: Optional[List[str]] = None) -> Optional[float]:
    """Sample the recording at intervals and keep the most convincing moment.

    The bytes are what cost - each probe pulls a few seconds of 4K over the wire -
    so once a stretch is local, decode a decent number of frames out of it rather
    than four. An animal that drifts through half a second is missed by a sparse
    read of footage we have already paid for.

    Where the record names its specimen, a moment showing that species wins over a
    more confident moment showing something else: an Atolla record cut at the frame
    where a passing shrimp scored 0.9 is not a clip of Atolla.
    """
    import av

    from pixel_patrol_deepsea import detector

    _, names = detector.load_detector()
    matching = (0.0, None)      # best moment showing the named specimen
    anything = (0.0, None)      # best moment showing anything at all
    reached = []
    for second in PROBE_SECONDS:
        if second > video.duration - 10:
            continue
        video.fetch_seconds(second, span=probe_mb / 12.0, lead=2.0)
        try:
            with av.open(str(video.path)) as container:
                stream = container.streams.video[0]
                container.seek(int(second / stream.time_base), stream=stream)
                for index, frame in enumerate(container.decode(video=0)):
                    if index > 150:
                        break
                    if index % 10:
                        continue
                    for confidence, class_index in detector.detect(frame.to_ndarray(format="rgb24")):
                        if confidence > anything[0]:
                            anything = (confidence, second)
                        if names[class_index] in (wanted or []) and confidence > matching[0]:
                            matching = (confidence, second)
        except Exception:
            continue
        reached.append(second)
        print(f"    t={second:4.0f}s  best {anything[0]:.2f}"
              + (f", named specimen {matching[0]:.2f}" if wanted else ""))
    return _pick(matching, anything, reached)


def _pick(matching, anything, reached) -> Optional[float]:
    if matching[1] is not None:
        return matching[1]
    if anything[1] is not None:
        return anything[1]
    return _middle_of(reached)


def _middle_of(seconds: List[float]) -> Optional[float]:
    """Where to cut when the probes found nothing.

    These records are specimen recordings - the archive is named after the animal
    it holds, so it is in there and the probes simply missed it. A clip of the
    named specimen that the detector cannot see is still worth having; skipping the
    record leaves nothing at all. Reuse a stretch already on disk.
    """
    if not seconds:
        return None
    chosen = seconds[len(seconds) // 2]
    print(f"    no detection in the probes; keeping t={chosen:.0f}s anyway")
    return chosen


def _cut(source: Path, destination: Path, start: float, length: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(max(0.0, start)),
         "-t", str(length), "-i", str(source), "-vf", "scale=1920:-2",
         "-c:v", "libx264", "-crf", "20", "-an", str(destination)], check=True)


SOURCE_NAMES = {
    "MBARI": "MBARI's annotated midwater sequences",
    "RAD2": "single-specimen midwater recordings from Schmidt Ocean FK210812, published on Zenodo by Burns and Phillips",
    "NOAA": "NOAA Ocean Exploration dive tapes",
}


def _describe_sources() -> str:
    """Name the expeditions actually in the clips folder, not the ones once used."""
    present = [name for prefix, name in SOURCE_NAMES.items()
               if any(CLIPS.glob(f"{prefix}*"))]
    return "Open expedition video: " + ", ".join(present) + "." if present else "Open expedition video."


def process(detect_every: int) -> None:
    from pixel_patrol_deepsea import detector

    print(f"\ndetector: {'ready' if detector.is_available() else 'not set up - metrics only'}")
    environment = {
        **os.environ,
        "PIXEL_PATROL_DETECTOR_SIZE": "1280",
        # 0.25 measured 0.92 precision on MWS and 1.00 on MWD against their own
        # per-frame annotations; lower thresholds trade that away for little recall.
        "PIXEL_PATROL_DETECTOR_CONFIDENCE": "0.25",
        "PIXEL_PATROL_DETECTOR_EVERY": str(detect_every),
    }
    subprocess.run(
        ["pixel-patrol", "process", str(CLIPS), "-o", str(REPORT),
         "--loader", "video", "--slice-size", "T=30",
         # The colour axis stays whole: see dive_report for why.
         "--slice-size", "C=-1",
         "--name", "Deep-sea footage",
         "--description", _describe_sources(),
         # raster-quality's spectral_slope runs an FFT on every frame of every
         # slice - 3.8 s per HD slice, more than twice what the detector costs. The
         # widgets fall back to intensity spread without it, and a rebuild drops
         # from twenty minutes to three.
         "--processors-exclude", "raster-quality",
         "--max-workers", "2", "--mb-per-task", "6144"],
        check=True, env=environment)


def refine() -> None:
    """Second pass: look closely only where the coarse pass saw something.

    The first pass samples sparsely because inference is expensive and animals stay
    in frame for seconds at a time. This walks just those stretches at a higher rate
    and writes a crop of every detection, which is what you actually want to look at.
    """
    import polars as pl

    from pixel_patrol_deepsea.reports import slice_rows
    from pixel_patrol_deepsea.refine import (
        refine_windows, windows_with_detections,
        write_sightings_csv, write_sightings_parquet,
    )

    table = pl.read_parquet(REPORT)
    slices = slice_rows(table)
    if "detection_count" not in slices.columns:
        print("no detector ran, nothing to refine")
        return

    everything = []
    for name in sorted(slices["name"].unique().to_list()):
        rows = slices.filter(pl.col("name") == name)
        fps = float(rows["fps"][0])
        windows = windows_with_detections(rows.to_dicts(), fps)
        if not windows:
            print(f"  {name}: nothing detected in the coarse pass")
            continue
        covered = sum(w.end - w.start + 4 for w in windows)
        total = len(rows) * 30 / fps
        print(f"  {name}: {len(windows)} windows, {covered:.0f}s of {total:.0f}s to revisit")
        found = refine_windows(CLIPS / name, windows, CROPS)
        print(f"    {len(found)} sightings, crops in {CROPS.name}/")
        everything.extend(found)

    if everything:
        write_sightings_csv(everything, SIGHTINGS)
        write_sightings_parquet(everything, SIGHTINGS_TABLE)
        taxa = {}
        for sighting in everything:
            taxa[sighting.taxon] = max(taxa.get(sighting.taxon, 0), sighting.confidence)
        print(f"\n{len(everything)} sightings -> {SIGHTINGS.name}, {SIGHTINGS_TABLE.name}")
        for taxon, best in sorted(taxa.items(), key=lambda kv: -kv[1]):
            print(f"   {taxon:30} best confidence {best:.2f}")


def check_against_the_record_names() -> None:
    """Score the detector against the one label these recordings come with.

    Each specimen archive is named after the animal in it, so every clip cut from one
    is a test the detector never saw. This says, per clip, what it called the animal
    and whether that is what the record says it is - the only accuracy number in this
    example that does not come from MBARI's own annotations.
    """
    import polars as pl

    if not SIGHTINGS_TABLE.exists():
        print("no sightings yet; run with --refine first")
        return
    sightings = pl.read_parquet(SIGHTINGS_TABLE)
    print(f"\n{'clip':30} {'expected':24} {'detector said':26} verdict")
    agreed = tested = 0
    for label, _ in sorted(SPECIMENS.values()):
        taxon = _taxon_of(label)
        wanted = SPECIMEN_CLASSES.get(taxon, [])
        found = sightings.filter(pl.col("name") == f"{label}.mp4")
        if not len(found):
            print(f"{label:30} {taxon:24} {'- nothing detected -':26}")
            continue
        tested += 1
        best = found.sort("confidence", descending=True).row(0, named=True)
        said = f"{best['taxon']} {best['confidence']:.2f}"
        hit = best["taxon"] in wanted
        # A miss at the top is still worth knowing if the right answer is in there.
        rank = ([t for t in found.sort("confidence", descending=True)["taxon"].to_list()
                 if t in wanted] or [None])[0]
        agreed += hit
        verdict = "agrees" if hit else ("right answer present, ranked below" if rank else "disagrees")
        print(f"{label:30} {taxon:24} {said:26} {verdict}")
    if tested:
        print(f"\ntop call agrees with the record name on {agreed} of {tested} clips")


def write_index() -> None:
    """The page over every report in data/, not just the one this run built."""
    from pixel_patrol_deepsea.catalogue_page import write_catalogue_page

    write_catalogue_page(DATA)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--with-noaa", action="store_true",
                        help="also fetch a full-length benthic dive tape (170 MB)")
    parser.add_argument("--with-atolla", action="store_true",
                        help="also pull the Atolla jellyfish out of its remote archive")
    parser.add_argument("--with-specimens", action="store_true",
                        help="pull a clip of every named midwater specimen record on Zenodo")
    parser.add_argument("--view", action="store_true", help="open the existing report and stop")
    parser.add_argument("--refine", action="store_true",
                        help="revisit the detected stretches at a higher rate and write crops")
    parser.add_argument("--check-names", action="store_true",
                        help="score the detector against the taxon each record is named after")
    parser.add_argument("--index", action="store_true",
                        help="rewrite data/index.html, the page listing every report here")
    parser.add_argument("--no-view", action="store_true",
                        help="build only; do not start the viewer (for scripted runs)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--detect-every", type=int, default=1, metavar="SECONDS",
                        help="how often to run the detector; raise it for long tapes")
    args = parser.parse_args(argv)

    if not args.view:
        gather(args.with_noaa)
        if args.with_atolla:
            fetch_atolla()
        if args.with_specimens:
            fetch_specimens()
        process(args.detect_every)
    if args.refine:
        refine()
    if args.check_names:
        check_against_the_record_names()
    if args.index:
        write_index()
    if not REPORT.exists():
        return print(f"no report at {REPORT}; run without --view first") or 1
    print(f"\nreport: {REPORT}  ({REPORT.stat().st_size / 1e6:.0f} MB)")
    if args.no_view:
        return 0
    subprocess.run(["pixel-patrol", "view", str(REPORT), "--port", str(args.port)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
