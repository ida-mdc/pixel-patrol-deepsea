"""The page over a whole collection: progress per expedition and what was found."""

import json
import re
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from pixel_patrol_deepsea.catalogue_page import read_progress, render, write_catalogue_page

JPEG = bytes.fromhex("ffd8ffe000104a464946000101000001000100" + "00" * 4 + "ffd9")


def _collection(root, listed=0, parts=0, report_rows=None, sightings=None,
                expedition="EX2107"):
    """Lay out the folders the page reads, with only the pieces a test needs."""
    if listed:
        (root / "manifests").mkdir(parents=True, exist_ok=True)
        (root / "manifests" / f"{expedition}.json").write_text(json.dumps(
            {"expedition": expedition, "listed_at": "2026-09-09T00:00:00+00:00",
             "videos": [f"http://a/{i}.mp4" for i in range(listed)]}))
    if parts:
        folder = root / "parts" / expedition
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(parts):
            pl.DataFrame({"x": [i]}).write_parquet(folder / f"{i}.parquet")
    if report_rows is not None:
        (root / "parquet").mkdir(parents=True, exist_ok=True)
        table = pl.DataFrame(report_rows).to_arrow()
        table = table.replace_schema_metadata({b"pp_project_name": b"Windows to the Deep 2021"})
        pq.write_table(table, root / "parquet" / f"{expedition}.parquet")
    if sightings is not None:
        (root / "sightings").mkdir(parents=True, exist_ok=True)
        pl.DataFrame(sightings).write_parquet(root / "sightings" / f"{expedition}.parquet")
    return root


def _slices(n=4, fps=10.0, taxon="fish"):
    return [{"obs_level": 1, "dim_t": i * 50, "dim_c": None, "name": "d.mp4", "fps": fps,
             "detection_count": 1, "detection_top_class": taxon,
             "detection_confidence": 0.7, "detection_crop": JPEG,
             "slice_thumbnail": JPEG} for i in range(n)]


def _sightings(entries):
    return [{"name": n, "dim_t": int(sec) * 10, "frame": int(sec * 10), "second": sec,
             "timecode": "00:00", "taxon": t, "confidence": 0.8,
             "x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "crop": JPEG}
            for n, t, sec, b in entries]


def test_lists_every_expedition_in_the_catalogue(tmp_path):
    rows = read_progress(tmp_path)
    assert {"EX2107", "EX1903L2", "EX1605L1"} <= {r.id for r in rows}


def test_progress_has_a_denominator_before_anything_is_processed(tmp_path):
    _collection(tmp_path, listed=99)
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert (row.listed, row.processed) == (99, 0)
    assert row.share == 0


def test_counts_a_part_per_recording_analysed(tmp_path):
    _collection(tmp_path, listed=10, parts=4)
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert (row.listed, row.processed) == (10, 4)
    assert row.share == pytest.approx(0.4)


def test_reads_footage_length_and_species_from_the_merged_report(tmp_path):
    _collection(tmp_path, listed=10, parts=4, report_rows=_slices(6, fps=10.0))
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert row.seconds == pytest.approx(6 * 50 / 10.0)
    assert row.taxa == ["fish"]


def test_counts_animals_rather_than_detections(tmp_path):
    drifting = [("d.mp4", "fish", i * 0.25, (10 + i, 10, 30 + i, 30)) for i in range(12)]
    _collection(tmp_path, listed=10, parts=4, report_rows=_slices(),
                sightings=_sightings(drifting))
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert row.sightings == 12
    assert row.animals == 1


def test_links_the_report_through_the_viewer_with_a_data_parameter(tmp_path):
    # The whole point: opening a report needs a file server and nothing else.
    _collection(tmp_path, listed=10, parts=4, report_rows=_slices())
    page = render(read_progress(tmp_path))
    assert "viewer/index.html?data=../parquet/EX2107.parquet" in page


def test_the_link_opens_the_report_on_the_widgets_that_know_the_footage(tmp_path):
    # The general widgets are switched off by the link rather than by the viewer,
    # so the preference lives in this extension and not in pixel-patrol. Every one
    # of them is still in the sidebar, one click away. Escaped, because an
    # ampersand in an href is written &amp;.
    from pixel_patrol_deepsea.catalogue_page import HIDDEN_WIDGETS

    _collection(tmp_path, listed=10, parts=4, report_rows=_slices())
    page = render(read_progress(tmp_path))
    assert "&amp;hidden=" + ".".join(HIDDEN_WIDGETS) in page
    assert "sunburst" in HIDDEN_WIDGETS and "image-table" in HIDDEN_WIDGETS
    # ...and the ones this extension exists for are not among them
    assert not any(w.startswith("temporal-") for w in HIDDEN_WIDGETS)


def test_says_so_rather_than_linking_a_report_that_is_not_built(tmp_path):
    _collection(tmp_path, listed=10, parts=2)
    page = render(read_progress(tmp_path))
    assert "no report yet" in page
    assert "?data=" not in page


def test_totals_the_collection_in_its_own_summary(tmp_path):
    _collection(tmp_path, listed=10, parts=4, report_rows=_slices())
    page = render(read_progress(tmp_path))
    # One recording inside the report, whatever number of part files sit beside it.
    assert "<b>1 / 10</b><span>recordings</span>" in page
    # ...and the expedition's own row in the fleet table says the same.
    assert "<td class=\"num\">1<span class=\"sub\">of 10 · 10%</span></td>" in page


def test_counts_the_recordings_in_the_report_not_the_files_beside_it(tmp_path):
    # A scheduler builds this page in its own directory, where the report is staged
    # and the parts are not. Counting files there found nothing and every expedition
    # read "0 of 0" however much had been analysed.
    rows = _slices(3) + [dict(r, name="second.mp4") for r in _slices(3)]
    _collection(tmp_path, listed=10, report_rows=rows)
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert row.processed == 2


def test_falls_back_to_the_part_files_when_no_report_is_merged_yet(tmp_path):
    _collection(tmp_path, listed=10, parts=3)
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert row.processed == 3


def test_notices_an_expedition_only_present_on_disk(tmp_path):
    _collection(tmp_path, listed=3, parts=1, expedition="FKt240108")
    assert "FKt240108" in {r.id for r in read_progress(tmp_path)}


def test_writes_the_page_where_it_was_asked_to(tmp_path):
    _collection(tmp_path, listed=1, parts=1)
    assert write_catalogue_page(tmp_path) == tmp_path / "index.html"
    assert (tmp_path / "index.html").read_text().startswith("<!doctype html>")


def test_reports_slices_with_animals_before_a_second_pass_has_counted_them(tmp_path):
    # A slice the detector fired on is not an animal: one creature can occupy
    # twenty slices, so calling them animals would inflate every number here.
    _collection(tmp_path, listed=10, parts=2, report_rows=_slices(6))
    page = render(read_progress(tmp_path))
    assert "slices with an animal" in page
    assert "animals found" not in page


def test_prefers_counted_animals_once_a_second_pass_has_run(tmp_path):
    drifting = [("d.mp4", "fish", i * 0.25, (10 + i, 10, 30 + i, 30)) for i in range(9)]
    _collection(tmp_path, listed=10, parts=2, report_rows=_slices(6),
                sightings=_sightings(drifting))
    page = render(read_progress(tmp_path))
    # One animal, drifting across nine detections - not nine animals.
    assert "<b>1</b><span>animals</span>" in page
    assert "slices with an animal" not in page


def _report_with_animals(root, entries, expedition="EX2107"):
    """A report whose `detections` column carries per-animal crops, as the
    detector writes them: one entry per detection per frame it looked at."""
    import base64
    rows = []
    for slice_index, animals in enumerate(entries):
        rows.append({
            "obs_level": 1, "dim_t": slice_index * 50, "name": "dive.mp4",
            "child_id": "dive_one", "fps": 10.0,
            "detection_count": float(len(animals)),
            "detection_confidence": max((a["conf"] for a in animals), default=None),
            "detection_top_class": max(animals, key=lambda a: a["conf"])["class"] if animals else None,
            "detection_crop": JPEG, "slice_thumbnail": JPEG,
            # Each crop carries its own frame time, a tenth of a second apart, the
            # way a burst of consecutive frames comes out of the detector.
            "detections": json.dumps([
                {**a, "second": round(slice_index * 5.0 + i * 0.1, 3), "frame": i,
                 "crop": base64.b64encode(a.get("crop") or JPEG).decode()}
                for i, a in enumerate(animals)]) if animals else None,
        })
    (root / "parquet").mkdir(parents=True, exist_ok=True)
    table = pl.DataFrame(rows, infer_schema_length=None).to_arrow()
    table = table.replace_schema_metadata({b"pp_project_name": b"Windows to the Deep 2021"})
    pq.write_table(table, root / "parquet" / f"{expedition}.parquet")
    return root


def _animal(taxon, conf, box=(10, 10, 40, 40)):
    return {"class": taxon, "conf": conf, "box": list(box)}


def test_reads_animals_out_of_the_report_with_no_second_pass(tmp_path):
    # Three consecutive frames of one fish: one animal, seen three times.
    _report_with_animals(tmp_path, [[_animal("fish", 0.8)] * 3])
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert row.animals == 1
    assert row.sightings == 3


def test_the_same_fish_in_the_same_place_five_seconds_later_is_one_fish(tmp_path):
    # Five seconds is one sampling interval here, so it was never missing - nobody
    # looked. Counting it twice is what turned one fish into eight tiles.
    _report_with_animals(tmp_path, [[_animal("fish", 0.8)], [_animal("fish", 0.7)]])
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert row.animals == 1


def test_two_fish_on_opposite_sides_of_the_frame_are_two(tmp_path):
    _report_with_animals(tmp_path, [[_animal("fish", 0.8, (10, 10, 60, 50)),
                                     _animal("fish", 0.7, (560, 300, 610, 340))]])
    row = next(r for r in read_progress(tmp_path) if r.id == "EX2107")
    assert row.animals == 2


def test_the_wall_is_left_out_when_nothing_was_found(tmp_path):
    _report_with_animals(tmp_path, [[], []])
    page = render(read_progress(tmp_path))
    assert "<figure data-conf" not in page
    assert "species</h2>" not in page


def test_the_site_refreshes_its_bundled_viewer_rather_than_skipping_it(tmp_path, monkeypatch):
    """A site carries its own copy of every widget. Left in place from an earlier
    run it serves the widgets as they were then, silently - which is the worst way
    for a page to be wrong."""
    import pixel_patrol_base.api as api

    from pixel_patrol_deepsea import collect

    built = []
    monkeypatch.setattr(api, "build_viewer", lambda root: built.append(Path(root)))

    _collection(tmp_path, listed=1, parts=1)
    (tmp_path / "viewer").mkdir()
    (tmp_path / "viewer" / "index.html").write_text("a viewer from an earlier run")

    collect.build_site(tmp_path)
    assert built == [tmp_path], "an existing viewer must still be rebuilt"


def test_finds_the_source_urls_in_the_manifest_beside_the_report(tmp_path):
    """A recording staged to a scratch directory and deleted leaves no source_url
    on the report, but the manifest that named it is still sitting next to it."""
    from pixel_patrol_deepsea.catalogue_page import _sources_from_manifest
    from pixel_patrol_deepsea.reports import summarise

    _report_with_animals(tmp_path, [[_animal("fish", 0.8)]])
    (tmp_path / "manifests").mkdir(exist_ok=True)
    (tmp_path / "manifests" / "EX2107.json").write_text(json.dumps(
        {"videos": ["https://a/dive.mp4", "https://a/somebody_else.mp4"]}))
    got = _sources_from_manifest(tmp_path / "parquet" / "EX2107.parquet",
                                 summarise(tmp_path / "parquet" / "EX2107.parquet"))
    assert [s["label"] for s in got] == ["dive.mp4"]


def test_matches_a_report_that_names_a_transcoded_copy(tmp_path):
    # An older run named the recording after the thinned copy it analysed.
    from pixel_patrol_deepsea.catalogue_page import _sources_from_manifest

    class Summary:
        names = ["dive_10fps.mp4"]

    (tmp_path / "manifests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "manifests" / "EX2107.json").write_text(json.dumps(
        {"videos": ["https://a/dive.mp4"]}))
    (tmp_path / "parquet").mkdir(exist_ok=True)
    got = _sources_from_manifest(tmp_path / "parquet" / "EX2107.parquet", Summary())
    assert [s["label"] for s in got] == ["dive.mp4"]


def _clip(box, of, frames=3):
    from pixel_patrol_deepsea.catalogue_page import _Clip
    clip = _Clip(tuple(box))
    clip.frames = [(float(i), f"{of}-{i}".encode()) for i in range(frames)]
    return clip


def _track(recording, slice_t, box):
    from pixel_patrol_deepsea.refine import Sighting, Track
    best = Sighting(recording=recording, second=1.0, frame=0, slice_t=slice_t,
                    taxon="sea pen", confidence=0.8, box=tuple(box))
    return Track(recording=recording, taxon="sea pen", first_second=1.0,
                 last_second=1.0, sightings=[best])




# ── building the page without the collection ──────────────────────────────────

def test_the_expedition_table_survives_being_written_down(tmp_path):
    """A runner has the code and no reports. The table it needs is fifty kilobytes
    of counts; reading it back out of 4.7 GB of parquet is what it cannot do."""
    from pixel_patrol_deepsea.catalogue_page import (
        PROGRESS, Progress, load_progress, save_progress,
    )
    from pixel_patrol_deepsea.reports import Placed

    rows = [Progress(id="EX2107", title="Windows to the Deep", listed=120, processed=12,
                     seconds=3600.5, animals=91, taxa=["Actiniaria", "Beroe"],
                     report=tmp_path / "parquet" / "EX2107.parquet",
                     placed=Placed(latitude=31.9, longitude=-77.2, source="dive")),
            Progress(id="DSMOT", title="MBARI DeepSea-MOT")]
    where = save_progress(rows, tmp_path / PROGRESS)
    back = load_progress(where)
    assert [r.id for r in back] == ["EX2107", "DSMOT"]
    assert back[0].taxa == ["Actiniaria", "Beroe"] and back[0].animals == 91
    assert back[0].placed.latitude == 31.9 and back[1].placed is None
    assert back[0].report.name == "EX2107.parquet"


def test_a_collection_that_is_not_here_is_read_from_the_file(tmp_path, monkeypatch):
    """And nothing goes looking for the reports, which on a runner are not there."""
    from pixel_patrol_deepsea import catalogue_page
    from pixel_patrol_deepsea.catalogue_page import (
        PROGRESS, Progress, save_progress, write_catalogue_page,
    )

    save_progress([Progress(id="EX2107", title="Windows to the Deep", listed=9,
                            processed=9, seconds=60.0)], tmp_path / PROGRESS)
    monkeypatch.setattr(catalogue_page, "read_progress",
                        lambda root: (_ for _ in ()).throw(
                            AssertionError("went looking for the reports")))
    page = write_catalogue_page(tmp_path).read_text()
    assert "Windows to the Deep" in page
