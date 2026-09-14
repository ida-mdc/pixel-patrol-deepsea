"""Cutting a report down to the pictures the page reads.

A report is 98% pictures, and three of them earn nothing: a crop column that
duplicates what the detections JSON already holds, a whole frame nobody looks at
while an animal is named, and the interpolation around every crop that was
stored at 192 px whatever size the animal really was. What is checked here is
that every animal still has a picture afterwards, that the numbers are untouched,
and that a report is never read whole to do it - GOA2004 is 3.38 GB.
"""

import base64
import io
import json

import polars as pl
import pyarrow.parquet as pq
import pytest

from pixel_patrol_deepsea.slim import slim


def _jpeg(width, height):
    from PIL import Image

    held = io.BytesIO()
    Image.new("RGB", (width, height), (20, 60, 90)).save(held, "JPEG", quality=75)
    return held.getvalue()


def _animals(*boxes):
    """Detections as the report carries them: a box, and a crop blown up to 192 px."""
    return json.dumps([
        {"class": "beroe", "conf": 0.7, "box": list(box),
         "crop": base64.b64encode(_jpeg(192, 108)).decode()}
        for box in boxes])


def _report(path, rows=6):
    frame = {
        "name": ["dive.mp4"] * rows,
        "dim_t": list(range(rows)),
        "obs_level": [1] * rows,
        "detection_count": [2.0] * rows,
        "detections": [_animals((10, 10, 74, 46), (100, 100, 148, 136))] * rows,
        "detection_crop": [_jpeg(192, 108)] * rows,
        "slice_thumbnail": [_jpeg(128, 72)] * rows,
        "frame_difference": [0.5] * rows,
    }
    table = pl.DataFrame(frame).to_arrow()
    table = table.replace_schema_metadata({b"pp_project_name": b"Windows to the Deep"})
    pq.write_table(table, path)
    return path


def test_the_spare_picture_columns_go(tmp_path):
    report = _report(tmp_path / "EX2107.parquet")
    slim(report)
    names = pq.read_schema(report).names
    assert "slice_thumbnail" not in names and "detection_crop" not in names
    assert "detections" in names and "frame_difference" in names


def test_every_animal_still_has_its_own_crop(tmp_path):
    """The whole point. A gallery that shows one animal per frame is not a gallery
    of what was in the frame."""
    report = _report(tmp_path / "EX2107.parquet")
    slim(report)
    out = pl.read_parquet(report)
    for raw in out["detections"].to_list():
        animals = json.loads(raw)
        assert len(animals) == 2
        assert all(a["crop"] for a in animals)


def test_a_crop_keeps_every_pixel_it_was_cut_with(tmp_path):
    """`crop_of` cuts the box plus a 60% margin, because a tight box round a
    two-centimetre animal is a picture of nothing, and the report stores exactly
    what was cut - measured over 800 crops, the stored width is the cut width.
    So there is no interpolation to reclaim, and shrinking towards the box would
    throw away the context that makes the animal identifiable."""
    from PIL import Image

    report = _report(tmp_path / "EX2107.parquet")
    assert slim(report) == 12          # two animals on each of six rows
    animals = json.loads(pl.read_parquet(report)["detections"][0])
    sizes = [Image.open(io.BytesIO(base64.b64decode(a["crop"]))).size for a in animals]
    assert sizes == [(192, 108), (192, 108)]
    assert all(base64.b64decode(a["crop"])[:4] == b"RIFF" for a in animals), "webp"


def test_running_it_twice_does_not_compress_the_crops_twice(tmp_path):
    report = _report(tmp_path / "EX2107.parquet")
    slim(report)
    once = pl.read_parquet(report)["detections"][0]
    assert slim(report) == 0, "nothing left to re-encode"
    assert pl.read_parquet(report)["detections"][0] == once


def test_the_boxes_and_the_names_come_through_untouched(tmp_path):
    report = _report(tmp_path / "EX2107.parquet")
    before = [ {k: v for k, v in a.items() if k != "crop"}
               for a in json.loads(pl.read_parquet(report)["detections"][0]) ]
    slim(report)
    after = [ {k: v for k, v in a.items() if k != "crop"}
              for a in json.loads(pl.read_parquet(report)["detections"][0]) ]
    assert before == after


def test_what_the_viewer_reads_off_the_file_survives(tmp_path):
    report = _report(tmp_path / "EX2107.parquet")
    slim(report)
    assert (pq.read_schema(report).metadata or {})[b"pp_project_name"] \
        == b"Windows to the Deep"
    assert pq.ParquetFile(report).metadata.num_rows == 6


def test_a_row_with_no_detections_survives(tmp_path):
    path = tmp_path / "EX2107.parquet"
    pl.DataFrame({"name": ["dive.mp4"] * 2, "dim_t": [0, 1], "obs_level": [0, 1],
                  "detections": [None, "not json"],
                  "slice_thumbnail": [_jpeg(128, 72), None]}).write_parquet(path)
    assert slim(path) == 0
    out = pl.read_parquet(path)
    assert out["detections"].to_list() == [None, "not json"]
    assert "slice_thumbnail" not in out.columns


def test_a_report_is_never_read_whole_to_slim_it(tmp_path, monkeypatch):
    """GOA2004 is 3.38 GB of pictures, which is the reason this exists at all."""
    import polars
    import pyarrow.parquet

    from pixel_patrol_deepsea import slim as module

    def refuse(*args, **kwargs):
        raise AssertionError("the whole report was read into memory")

    monkeypatch.setattr(pyarrow.parquet, "read_table", refuse)
    monkeypatch.setattr(polars, "read_parquet", refuse)
    monkeypatch.setattr(module, "READ_ROWS", 4)
    # A flat-colour crop compresses to almost nothing, so the row group has to
    # be cut small enough that a test's worth of data still fills more than one.
    monkeypatch.setattr(module, "ROW_GROUP_BYTES", 2_000)
    report = _report(tmp_path / "EX2107.parquet", rows=40)
    assert module.slim(report) == 80
    assert pyarrow.parquet.ParquetFile(report).num_row_groups > 1


def test_the_file_is_left_alone_when_the_rewrite_fails(tmp_path, monkeypatch):
    from pixel_patrol_deepsea import slim as module

    report = _report(tmp_path / "EX2107.parquet")
    was = report.read_bytes()
    monkeypatch.setattr(module, "_smaller_crops",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        module.slim(report)
    assert report.read_bytes() == was
    assert not (tmp_path / "EX2107.parquet.slimming").exists()
