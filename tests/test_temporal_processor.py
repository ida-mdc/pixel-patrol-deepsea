import numpy as np
import pytest

from pixel_patrol_base.core.record import record_from
from pixel_patrol_deepsea.temporal_processor import (
    FRAME_DIFFERENCE,
    FRAME_DIFFERENCE_MAX,
    FRAME_PAIR_COUNT,
    TemporalMetricsProcessor,
)


@pytest.fixture
def proc() -> TemporalMetricsProcessor:
    return TemporalMetricsProcessor()


def _run(proc, data: np.ndarray, dim_order: str) -> dict:
    return proc.run_chunk(record_from(data, {"dim_order": dim_order.upper()}))


def _aggregate(proc, rows: list[dict]) -> dict:
    return {
        name: value
        for name in proc.OUTPUT_SCHEMA
        if (fn := proc.get_aggregation(name)) and (value := fn(rows, ())) is not None
    }


def _frames(*levels, shape=(4, 5)) -> np.ndarray:
    """A T-stack of uniform frames, one per given intensity level."""
    return np.stack([np.full(shape, level, dtype=np.uint8) for level in levels])


def test_frozen_footage_scores_exactly_zero(proc):
    row = _run(proc, _frames(7, 7, 7, 7), "TYX")
    assert row[FRAME_DIFFERENCE] == 0.0
    assert row[FRAME_DIFFERENCE_MAX] == 0.0
    assert row[FRAME_PAIR_COUNT] == 3


def test_mean_and_peak_describe_different_things(proc):
    # One hard jump among otherwise still frames: the mean dilutes it, the peak keeps it.
    row = _run(proc, _frames(10, 10, 10, 90), "TYX")
    assert row[FRAME_DIFFERENCE] == pytest.approx(80 / 3)
    assert row[FRAME_DIFFERENCE_MAX] == pytest.approx(80.0)


def test_unsigned_frames_do_not_wrap_around(proc):
    # 0 - 255 on uint8 would wrap to 1 without the float promotion.
    row = _run(proc, _frames(255, 0), "TYX")
    assert row[FRAME_DIFFERENCE] == pytest.approx(255.0)


def test_t_axis_is_found_wherever_it_sits(proc):
    moved = np.moveaxis(_frames(0, 10, 30), 0, 1)
    assert _run(proc, moved, "YTX")[FRAME_DIFFERENCE] == pytest.approx(15.0)


def test_a_single_frame_yields_no_row(proc):
    assert _run(proc, _frames(5), "TYX") == {}


def test_a_record_without_time_yields_no_row(proc):
    assert _run(proc, np.zeros((4, 5), dtype=np.uint8), "YX") == {}


def test_aggregation_weights_by_frame_pairs(proc):
    # 3 pairs at movement 10 and 1 pair at movement 50 average to 20, not 30.
    rows = [
        {FRAME_DIFFERENCE: 10.0, FRAME_DIFFERENCE_MAX: 10.0, FRAME_PAIR_COUNT: 3},
        {FRAME_DIFFERENCE: 50.0, FRAME_DIFFERENCE_MAX: 50.0, FRAME_PAIR_COUNT: 1},
    ]
    aggregated = _aggregate(proc, rows)
    assert aggregated[FRAME_DIFFERENCE] == pytest.approx(20.0)
    assert aggregated[FRAME_DIFFERENCE_MAX] == pytest.approx(50.0)
    assert aggregated[FRAME_PAIR_COUNT] == 4


def test_colour_frames_reduce_over_every_axis_but_time(proc):
    frames = np.zeros((2, 4, 5, 3), dtype=np.uint8)
    frames[1, ..., 0] = 30  # only the red channel moves
    assert _run(proc, frames, "TYXC")[FRAME_DIFFERENCE] == pytest.approx(10.0)
