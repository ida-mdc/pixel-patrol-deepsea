"""Reading a report: what is in it, and what it found."""

import json

import polars as pl
import pyarrow.parquet as pq
import pytest

from pixel_patrol_deepsea.reports import collect, summarise

# A one-pixel JPEG, so the fixtures carry real bytes rather than a placeholder.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffc2000b08000100"
    "0101011100ffc40014000100000000000000000000000000000009"
    "ffda0008010100000001d2cf20ffd9")


def _report(path, rows, name="A dive"):
    """Write a parquet that looks enough like a pixel-patrol report to summarise."""
    table = pl.DataFrame(rows).to_arrow()
    metadata = {b"pp_project_name": name.encode(),
                b"pp_description": b"Test footage.",
                b"pp_created_at": b"2026-01-02T03:04:05+00:00"}
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata})
    pq.write_table(table, path)
    return path


def _slices(classes, crops=True):
    return [{"obs_level": 1, "dim_t": index * 30, "dim_c": None, "name": "dive.mp4",
             "fps": 30.0, "detection_count": 1 if taxon else 0,
             "detection_top_class": taxon,
             "detection_confidence": 0.5 + index / 100,
             "detection_crop": JPEG if (taxon and crops) else None,
             "slice_thumbnail": JPEG}
            for index, taxon in enumerate(classes)]


def test_reads_the_project_name_out_of_the_parquet_footer(tmp_path):
    path = _report(tmp_path / "r.parquet", _slices(["beroe", None]), name="Midwater 2021")
    assert summarise(path).title == "Midwater 2021"


def test_counts_only_the_slices_that_had_an_animal(tmp_path):
    path = _report(tmp_path / "r.parquet", _slices(["beroe", None, "shrimp", None]))
    summary = summarise(path)
    assert summary.slices == 4
    assert summary.with_animals == 2


def test_keeps_the_most_confident_look_at_each_species(tmp_path):
    # Same species twice; the chip should carry the better of the two scores.
    path = _report(tmp_path / "r.parquet", _slices(["beroe", "beroe", "shrimp"]))
    taxa = summarise(path, pictures=True).taxa
    assert set(taxa) == {"beroe", "shrimp"}
    assert taxa["beroe"].best == pytest.approx(0.51)
    assert taxa["beroe"].thumbnail.startswith("data:image/jpeg;base64,")


def test_a_summary_reads_no_pictures_unless_it_is_asked_to(tmp_path, monkeypatch):
    """Counting the slices in a report used to read every clip frame in it.

    Seventeen expeditions is 36 GB of reports and about 200 MB of columns a summary
    touches, and the page over them calls this once each - which is how writing the
    page came to take longer than analysing a recording.
    """
    import polars

    asked = []
    real = polars.read_parquet

    def watch(source, **kwargs):
        asked.append(kwargs.get("columns"))
        return real(source, **kwargs)

    path = _report(tmp_path / "r.parquet", _slices(["beroe", "shrimp"]))
    monkeypatch.setattr(polars, "read_parquet", watch)
    summary = summarise(path)
    read = set(asked[0] or [])
    assert "detections" not in read and "slice_thumbnail" not in read
    assert "detection_crop" not in read
    assert "detection_top_class" in read and "obs_level" in read
    # ...and the numbers are the same ones.
    assert summary.slices == 2 and summary.with_animals == 2
    assert set(summary.taxa) == {"beroe", "shrimp"}
    assert summary.taxa["beroe"].crop is None


def test_how_many_slices_have_a_thumbnail_comes_out_of_the_footer(tmp_path):
    """A parquet's row groups say how many nulls each column holds, so the count
    needs no picture read - which is the only reason it can still be reported."""
    path = _report(tmp_path / "r.parquet", _slices(["beroe", None, "shrimp"]))
    assert summarise(path).stills == summarise(path, pictures=True).stills == 3


def test_ignores_files_that_are_not_reports(tmp_path):
    # sightings.parquet lives beside the report and has no observation level.
    pl.DataFrame({"taxon": ["beroe"], "second": [1.0]}).write_parquet(tmp_path / "sightings.parquet")
    _report(tmp_path / "r.parquet", _slices(["beroe"]))
    assert [s.path.name for s in collect(tmp_path)] == ["r.parquet"]


def test_counts_footage_at_each_recording_own_frame_rate(tmp_path):
    # A 59.94 fps sequence beside a 30 fps one: 2 slices of each is 1.0 s + 2.0 s.
    rows = ([{"obs_level": 1, "dim_t": i * 30, "dim_c": None, "name": "fast.mp4",
              "fps": 59.94, "detection_count": 0, "detection_top_class": None,
              "detection_confidence": None, "detection_crop": None, "slice_thumbnail": None}
             for i in range(2)]
            + [{"obs_level": 1, "dim_t": i * 30, "dim_c": None, "name": "slow.mp4",
                "fps": 30.0, "detection_count": 0, "detection_top_class": None,
                "detection_confidence": None, "detection_crop": None, "slice_thumbnail": None}
               for i in range(2)])
    path = _report(tmp_path / "r.parquet", rows)
    assert summarise(path).seconds == pytest.approx(1.0 + 2.0, abs=0.01)


def test_still_counts_footage_when_no_frame_rate_was_recorded(tmp_path):
    rows = [{"obs_level": 1, "dim_t": i * 30, "dim_c": None, "name": "dive.mp4"} for i in range(4)]
    path = _report(tmp_path / "r.parquet", rows)
    assert summarise(path).seconds == pytest.approx(4.0)


def _sighting_rows(entries):
    """entries: (name, taxon, second, confidence, box)"""
    return [{"name": n, "dim_t": int(sec) * 30, "frame": int(sec * 30), "second": sec,
             "timecode": "00:00", "taxon": t, "confidence": c,
             "x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "crop": JPEG}
            for n, t, sec, c, b in entries]


def test_reads_the_slice_size_off_the_time_axis(tmp_path):
    # A dive sliced at 50 frames beside a collection sliced at 30: assuming either
    # made an eight-hour dive read as four hours and fifty minutes.
    rows = [{"obs_level": 1, "dim_t": i * 50, "dim_c": None, "name": "dive.mp4",
             "fps": 10.0} for i in range(6)]
    path = _report(tmp_path / "r.parquet", rows)
    assert summarise(path).seconds == pytest.approx(6 * 50 / 10.0)


def test_falls_back_to_thirty_frames_for_a_single_slice(tmp_path):
    rows = [{"obs_level": 1, "dim_t": 0, "dim_c": None, "name": "dive.mp4", "fps": 30.0}]
    path = _report(tmp_path / "r.parquet", rows)
    assert summarise(path).seconds == pytest.approx(1.0)


# ── where the footage was taken ───────────────────────────────────────────────
#
# A cruise that crossed the dateline is the case that breaks every naive answer
# here, and EX2503 did: it worked at 166.67W, at 179.87E and at 178.50W.

from pixel_patrol_deepsea.reports import (  # noqa: E402
    Placed, _longitude_bounds, _mean_longitude,
)

ACROSS_THE_DATELINE = [-166.67, 179.87, -178.50]


def test_the_average_of_longitudes_is_not_the_average_of_their_numbers():
    # arithmetically these average to about -55, a quarter of the way round the
    # world from anywhere the ship was
    assert _mean_longitude(ACROSS_THE_DATELINE) == pytest.approx(-175.107, abs=0.01)
    assert _mean_longitude([179.9, -179.9]) == pytest.approx(180.0, abs=0.01)


def test_the_extent_is_bounded_by_the_gap_not_by_the_numbers():
    west, east, width = _longitude_bounds(ACROSS_THE_DATELINE)
    # the smallest and largest numbers are -178.50 and 179.87, which would suggest
    # 1.6 degrees; the footage actually spans 13.5, eastward past the dateline
    assert (west, east) == pytest.approx((179.87, -166.67))
    assert width == pytest.approx(13.46, abs=0.01)


def test_one_dive_is_a_point_and_a_cruise_is_an_extent():
    one_dive = Placed(latitude=45.9336, longitude=-130.0136,
                      north=45.9337, south=45.9336, west=-130.0137, east=-130.0136,
                      longitude_span=0.0001)
    assert one_dive.position == "45.934°N 130.014°W"

    a_cruise = Placed(latitude=27.48, longitude=-175.1,
                      north=27.581, south=27.398, west=179.87, east=-166.664,
                      longitude_span=13.46)
    assert a_cruise.position == ("27.398°N to 27.581°N, "
                                 "179.870°E to 166.664°W")


def test_no_longitudes_at_all_is_not_an_exception():
    assert _longitude_bounds([]) == (0.0, 0.0, 0.0)
    assert _mean_longitude([]) == 0.0


def test_ffmpeg_is_looked_for_rather_than_assumed(monkeypatch, tmp_path):
    """A missing ffmpeg used to surface as FileNotFoundError from inside subprocess,
    once per task, after the recording had already been fetched over the network."""
    from pixel_patrol_deepsea import collect

    stated = tmp_path / "my-ffmpeg"
    stated.write_text("")
    monkeypatch.setenv("PIXEL_PATROL_FFMPEG", str(stated))
    collect.ffmpeg.cache_clear()
    assert collect.ffmpeg() == str(stated)

    monkeypatch.delenv("PIXEL_PATROL_FFMPEG")
    monkeypatch.setattr(collect.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    collect.ffmpeg.cache_clear()
    assert collect.ffmpeg() == "/usr/bin/ffmpeg"


def test_no_ffmpeg_anywhere_says_how_to_get_one(monkeypatch):
    import builtins

    from pixel_patrol_deepsea import collect

    monkeypatch.delenv("PIXEL_PATROL_FFMPEG", raising=False)
    monkeypatch.setattr(collect.shutil, "which", lambda _name: None)
    real_import = builtins.__import__

    def without_imageio(name, *args, **kwargs):
        if name.startswith("imageio_ffmpeg"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_imageio)
    collect.ffmpeg.cache_clear()
    with pytest.raises(RuntimeError, match="conda-forge ffmpeg"):
        collect.ffmpeg()
    collect.ffmpeg.cache_clear()


def test_a_rebuilt_plugin_gets_a_url_the_browser_treats_as_new(tmp_path):
    """The failure this prevents looks exactly like a build that did not work.

    The viewer fetches each plugin from a path that never changes, so a browser
    that has seen one keeps serving its copy: the page rebuilds, index.html is new
    because it is written inline, and the widgets are yesterday's.
    """
    from pixel_patrol_deepsea.collect import _stamp_plugin_urls

    extension = tmp_path / "extensions" / "00-viewer"
    extension.mkdir(parents=True)
    (extension / "plugin_deepsea.js").write_text("export const one = 1;\n")
    (extension / "extension.json").write_text(
        json.dumps({"name": "Deep-Sea Extension", "plugins": ["./plugin_deepsea.js"]}))

    _stamp_plugin_urls(tmp_path)
    first = json.loads((extension / "extension.json").read_text())["plugins"][0]
    assert first.startswith("./plugin_deepsea.js?v=")

    # Rebuilding an unchanged plugin keeps the URL, so the cache is still used...
    _stamp_plugin_urls(tmp_path)
    assert json.loads((extension / "extension.json").read_text())["plugins"][0] == first

    # ...and changing it changes the URL, so the browser fetches the new one.
    (extension / "plugin_deepsea.js").write_text("export const one = 2;\n")
    _stamp_plugin_urls(tmp_path)
    assert json.loads((extension / "extension.json").read_text())["plugins"][0] != first


def test_a_manifest_naming_a_plugin_that_is_not_there_is_left_alone(tmp_path):
    from pixel_patrol_deepsea.collect import _stamp_plugin_urls

    extension = tmp_path / "extensions" / "00-viewer"
    extension.mkdir(parents=True)
    (extension / "extension.json").write_text(
        json.dumps({"plugins": ["./absent.js"]}))
    _stamp_plugin_urls(tmp_path)
    assert json.loads((extension / "extension.json").read_text())["plugins"] == ["./absent.js"]
