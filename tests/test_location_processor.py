"""Stamping each slice with where and when it was filmed.

The processor is handed pixels and the dimensions they sit at, and has to turn
that into a position - so what these tests hold on to is the arithmetic between
the two: the slice's own frame number over the frame rate, added to the start time
the archive named. Get that wrong and every detection is in the wrong place, in a
way no amount of looking at the report would reveal.
"""

import numpy as np
import pytest

from pixel_patrol_base.core.record import record_from
from pixel_patrol_deepsea import locations
from pixel_patrol_deepsea.location_processor import SliceLocationProcessor

TRACK = "\n".join([
    "unixtime,latitude,longitude,depth_m,altitude_m,constant,footprint,source",
    # 12:00:00Z, 12:00:10Z and 12:00:20Z on 2021-10-27
    "1635336000.0,31.2100,-77.8500,800.0,1.5,0,\"{\"\"type\"\":\"\"LineString\"\"}\",a track",
    "1635336010.0,31.2110,-77.8510,810.0,1.4,0,,a track",
    "1635336020.0,31.2120,-77.8520,820.0,1.3,0,,a track",
])


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A track and a start time left in the environment, as `collect one` does."""
    path = tmp_path / "track.csv"
    path.write_text(TRACK)
    monkeypatch.setenv(locations.TRACK_FILE, str(path))
    monkeypatch.setenv(locations.STARTED_AT, "2021-10-27T12:00:00+00:00")
    # the processor reads the environment once per process
    from pixel_patrol_deepsea import location_processor
    location_processor._staged.cache_clear()
    yield
    location_processor._staged.cache_clear()


def _slice(dim_t: int, fps: float = 10.0):
    """One leaf of video, as the pipeline hands it over."""
    return record_from(np.zeros((4, 64, 64), np.uint8),
                       {"dim_order": "TYX", "fps": fps, "dim_t": dim_t},
                       kind="intensity")


def test_a_slice_knows_the_moment_it_was_filmed(staged):
    row = SliceLocationProcessor().run_chunk(_slice(dim_t=100))
    # 100 frames in at 10 fps is ten seconds after the start
    assert row["recorded_at"] == "2021-10-27T12:00:10+00:00"


def test_and_the_position_the_vehicle_was_at_then(staged):
    row = SliceLocationProcessor().run_chunk(_slice(dim_t=100))
    assert row["latitude"] == pytest.approx(31.2110)
    assert row["longitude"] == pytest.approx(-77.8510)
    assert row["depth_m"] == pytest.approx(810.0)
    assert row["altitude_m"] == pytest.approx(1.4)
    assert row["location_source"] == "a track"


def test_the_dive_outline_comes_along_for_the_map(staged):
    row = SliceLocationProcessor().run_chunk(_slice(dim_t=0))
    # the map widget wants latitude, longitude AND footprint or it shows nothing
    assert "LineString" in row["footprint"]


def test_a_slice_outside_the_track_keeps_its_time_and_loses_its_place(staged):
    # an hour in, where this track says nothing: a time and no position is the
    # honest answer, and the nearest fix an hour away is not
    row = SliceLocationProcessor().run_chunk(_slice(dim_t=36000))
    assert row["recorded_at"] == "2021-10-27T13:00:00+00:00"
    assert "latitude" not in row


def test_nothing_at_all_when_the_archive_named_no_time(tmp_path, monkeypatch):
    from pixel_patrol_deepsea import location_processor
    monkeypatch.delenv(locations.TRACK_FILE, raising=False)
    monkeypatch.delenv(locations.STARTED_AT, raising=False)
    location_processor._staged.cache_clear()
    try:
        assert SliceLocationProcessor().run_chunk(_slice(dim_t=0)) == {}
    finally:
        location_processor._staged.cache_clear()


def test_the_columns_are_the_ones_the_map_reads():
    schema = SliceLocationProcessor.OUTPUT_SCHEMA
    for column in ("latitude", "longitude", "footprint"):
        assert column in schema, f"{column} is what pixel-patrol-geospatial queries for"
    # every column says what it is, because a position with no provenance is a
    # claim nobody can check
    assert set(SliceLocationProcessor.OUTPUT_SCHEMA_DESCRIPTIONS) == set(schema)


def test_a_stretch_of_footage_is_described_by_when_it_started(staged):
    from pixel_patrol_deepsea.location_processor import RECORDED_AT

    processor = SliceLocationProcessor()
    rows = [processor.run_chunk(_slice(dim_t=at)) for at in (200, 0, 100)]
    aggregate = processor.get_aggregation(RECORDED_AT)(rows, {})
    assert aggregate == "2021-10-27T12:00:00+00:00"
