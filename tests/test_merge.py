"""One parquet per expedition, from one parquet per recording.

The merge is where a collection is most likely to be lost: it runs after every
recording has been analysed, and the obvious way to write it - read every part,
concatenate, write the pile - needs as much memory as the expedition is big. On the
cluster that killed EX1702 at an 8 GB limit with 2.9 GB of parts, and it aborted the
whole run. So what is checked here is that the rows and the columns come through
whatever the parts look like, and that nothing ever reads a part whole.
"""

import polars as pl
import pyarrow.parquet as pq
import pytest

from pixel_patrol_deepsea.collect import merge


def _part(root, name, rows, extra=None):
    """One recording's parquet, with whatever columns that recording turned up."""
    frame = {"name": [name] * rows, "dim_t": list(range(rows)),
             "slice_thumbnail": [b"\\xff\\xd8" + bytes([i % 251]) * 900 for i in range(rows)]}
    frame.update(extra or {})
    root.mkdir(parents=True, exist_ok=True)
    where = root / f"{name}.parquet"
    pl.DataFrame(frame).write_parquet(where)
    return where


def test_every_row_of_every_part_comes_through(tmp_path):
    parts = [_part(tmp_path / "parts", f"dive{i}", 200) for i in range(5)]
    assert merge("EX2107", parts, tmp_path / "EX2107.parquet") == 0
    out = pl.read_parquet(tmp_path / "EX2107.parquet")
    assert len(out) == 1000
    assert sorted(out["name"].unique().to_list()) == [f"dive{i}" for i in range(5)]


def test_a_column_only_some_recordings_have_is_null_in_the_rest(tmp_path):
    """A recording the detector found nothing in has no detection columns, and a
    part from last week has fewer of them than one from today."""
    parts = [_part(tmp_path / "parts", "quiet", 3),
             _part(tmp_path / "parts", "busy", 3,
                   {"detection_count": [1.0, 2.0, 3.0], "detections": ["a", "b", "c"]})]
    merge("EX2107", parts, tmp_path / "out.parquet")
    out = pl.read_parquet(tmp_path / "out.parquet").sort("name", "dim_t")
    assert set(out.columns) >= {"name", "dim_t", "detection_count", "detections"}
    assert out.filter(pl.col("name") == "quiet")["detections"].to_list() == [None] * 3
    assert out.filter(pl.col("name") == "busy")["detection_count"].to_list() == [1.0, 2.0, 3.0]


def test_a_column_typed_differently_in_two_parts_takes_the_wider_type(tmp_path):
    parts = [_part(tmp_path / "parts", "early", 2, {"depth_m": [10, 20]}),
             _part(tmp_path / "parts", "later", 2, {"depth_m": [10.5, 20.5]})]
    merge("EX2107", parts, tmp_path / "out.parquet")
    out = pl.read_parquet(tmp_path / "out.parquet")
    assert out["depth_m"].dtype == pl.Float64
    assert sorted(out["depth_m"].to_list()) == [10.0, 10.5, 20.0, 20.5]


def test_the_report_says_which_expedition_it_is(tmp_path):
    """The viewer reads the title and the description out of the parquet's own
    metadata; a merged report with none of it opens as `out.parquet`."""
    parts = [_part(tmp_path / "parts", "dive", 2)]
    merge("EX2107", parts, tmp_path / "out.parquet")
    metadata = pq.read_schema(tmp_path / "out.parquet").metadata or {}
    assert b"Windows to the Deep" in metadata[b"pp_project_name"]
    assert metadata[b"pp_loader"] == b"video"
    assert b"1 recordings" in metadata[b"pp_description"]


def test_a_part_that_is_empty_or_missing_is_left_out(tmp_path):
    parts = [_part(tmp_path / "parts", "dive", 2)]
    (tmp_path / "parts" / "nothing.parquet").write_bytes(b"")
    parts.append(tmp_path / "parts" / "nothing.parquet")
    parts.append(tmp_path / "parts" / "never-written.parquet")
    assert merge("EX2107", parts, tmp_path / "out.parquet") == 0
    assert len(pl.read_parquet(tmp_path / "out.parquet")) == 2


def test_nothing_to_merge_is_said_rather_than_written(tmp_path):
    assert merge("EX2107", [tmp_path / "gone.parquet"], tmp_path / "out.parquet") == 1
    assert not (tmp_path / "out.parquet").exists()


def test_no_part_is_ever_read_whole(tmp_path, monkeypatch):
    """The regression that cost a nineteen-hour run.

    Reading a part to concatenate it is the one thing this must not do: an
    expedition is its parts, and GOA2004's are 17 GB. The merge reads row groups
    and writes row groups, so `read_table` - and polars' own reader, which is the
    other way to say it - are never reached at all.
    """
    import polars
    import pyarrow.parquet

    def refuse(*args, **kwargs):
        raise AssertionError("a whole part was read into memory")

    from pixel_patrol_deepsea import collect

    monkeypatch.setattr(pyarrow.parquet, "read_table", refuse)
    monkeypatch.setattr(polars, "read_parquet", refuse)
    # A row group of 50 kB rather than 48 MB, so a test's worth of data is cut the
    # way an expedition's is.
    monkeypatch.setattr(collect, "MERGE_BYTES", 50_000)
    parts = [_part(tmp_path / "parts", f"dive{i}", 150) for i in range(4)]
    assert merge("EX2107", parts, tmp_path / "out.parquet") == 0
    # ...and the writing is not one row group of everything either, or the reader of
    # this file has the same problem the merge had.
    assert pq.ParquetFile(tmp_path / "out.parquet").num_row_groups > 4


def test_row_groups_are_cut_by_weight_and_not_by_row_count(tmp_path):
    """Slim rows and fat rows both want a row group of about the same size: the
    viewer reads these over HTTP ranges, and a hundred-kilobyte row group is a
    request that buys nothing."""
    parts = [_part(tmp_path / "parts", "dive", 400)]
    merge("EX2107", parts, tmp_path / "out.parquet")
    written = pq.ParquetFile(tmp_path / "out.parquet")
    assert written.num_row_groups == 1, "400 slim rows is not 48 MB of anything"
    assert written.metadata.num_rows == 400
