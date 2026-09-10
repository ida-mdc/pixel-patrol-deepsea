import numpy as np
import pytest

from pixel_patrol_base.core.record import record_from
from pixel_patrol_deepsea.slice_colour_processor import (
    CHANNELS,
    SLICE_BLUE,
    SLICE_GREEN,
    SLICE_RED,
    SliceColourProcessor,
)


@pytest.fixture
def proc() -> SliceColourProcessor:
    return SliceColourProcessor()


def _tinted(red, green, blue, count=4, height=8, width=12) -> np.ndarray:
    """Footage of one flat colour, so the means are known exactly."""
    frames = np.zeros((count, height, width, 3), dtype=np.uint8)
    frames[..., 0], frames[..., 1], frames[..., 2] = red, green, blue
    return frames


def _row(proc, data, dim_order, channel=None):
    row = proc.run_chunk(record_from(data, {"dim_order": dim_order}))
    row["dim_c"] = channel
    return row


def _aggregate(proc, rows):
    return {name: proc.get_aggregation(name)(rows, ()) for name in CHANNELS}


def test_reads_each_channel_of_a_whole_colour_block(proc):
    row = _row(proc, _tinted(200, 100, 20), "TYXC")
    assert (row[SLICE_RED], row[SLICE_GREEN], row[SLICE_BLUE]) == (200.0, 100.0, 20.0)


def test_puts_the_channels_back_together_from_separate_leaves(proc):
    """The case the whole design exists for: one leaf per channel, none of which
    knows which channel it is, reassembled by `dim_c` at aggregation."""
    leaves = [_row(proc, _tinted(v, v, v)[..., :1], "TYXC", channel=c)
              for c, v in enumerate((200, 100, 20))]
    assert _aggregate(proc, leaves) == {SLICE_RED: 200.0, SLICE_GREEN: 100.0,
                                        SLICE_BLUE: 20.0}


def test_a_single_leaf_of_all_three_channels_is_already_the_answer(proc):
    assert _aggregate(proc, [_row(proc, _tinted(200, 100, 20), "TYXC")]) == {
        SLICE_RED: 200.0, SLICE_GREEN: 100.0, SLICE_BLUE: 20.0}


def test_monochrome_footage_comes_out_grey_rather_than_red(proc):
    """A greyscale recording has one channel mean and three channels to paint. The
    same number in all three is grey; leaving two null would draw the strip in
    whichever channel happened to be first."""
    row = _row(proc, np.full((3, 8, 12), 90, dtype=np.uint8), "TYX")
    assert _aggregate(proc, [row]) == {name: 90.0 for name in CHANNELS}


def test_says_nothing_about_an_empty_block(proc):
    assert proc.run_chunk(record_from(np.zeros((0, 8, 12), dtype=np.uint8),
                                      {"dim_order": "TYX"})) == {}


def test_averages_rather_than_guessing_when_channels_are_unlabelled(proc):
    """More leaves than channels, or leaves with no `dim_c`: the mean is wrong in
    hue and right in brightness, which beats a hole in the strip."""
    leaves = [_row(proc, np.full((2, 8, 12), v, dtype=np.uint8), "TYX")
              for v in (40, 80)]
    assert _aggregate(proc, leaves) == {name: 60.0 for name in CHANNELS}


def test_skips_slices_nothing_was_measured_for(proc):
    assert _aggregate(proc, [{"dim_c": 0}]) == {name: None for name in CHANNELS}
