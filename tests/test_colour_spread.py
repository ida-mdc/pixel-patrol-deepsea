"""The colour distribution of a slice.

What these protect is the part that makes the numbers comparable at all: the two
histograms are *shares* with named denominators, hue is weighted by saturation so
that grey pixels do not vote, and unlit water is left out rather than counted as
grey. A plot made from these columns across a decade of footage is only meaningful
if every one of those holds in every slice.
"""

import numpy as np
import pytest

from pixel_patrol_deepsea.colour_numpy_metrics import (
    HUE_BINS, SATURATION_BINS, colour_spread, colourfulness, hue_saturation_value,
)
from pixel_patrol_deepsea.colour_processor import (
    COLOURFULNESS, COLOURFUL_FRACTION, HUE, SATURATION, SATURATION_MEAN,
    SliceColourSpreadProcessor,
)


class Chunk:
    """The smallest thing that looks like a record to a processor."""

    def __init__(self, data, dim_order="TYXC"):
        self.data = data
        self.dim_order = dim_order
        self.meta = {}


def _filled(colour, frames=4, size=32):
    block = np.zeros((frames, size, size, 3), np.uint8)
    block[...] = colour
    return block


def test_a_grey_frame_has_no_hue_to_report():
    # Not "some hue with low weight" - none. At zero chroma the hue formula divides
    # by nothing and its answer swings across the circle, which would fill every
    # bin with the same fog and make a plot of hue look like noise everywhere.
    found = colour_spread(_filled((128, 128, 128)))
    assert sum(found[f"hue_{i:02d}"] for i in range(HUE_BINS)) == 0.0
    assert found[f"saturation_00"] == pytest.approx(1.0)
    assert found["colourfulness"] == pytest.approx(0.0, abs=0.5)


def test_hue_lands_in_the_bin_the_colour_belongs_to():
    # Pure primaries, whose hues are exactly 0, 120 and 240 degrees. A blue with
    # any red in it sits at 233 and belongs in bin 7, which is the bins working.
    for colour, expected in (((220, 0, 0), 0), ((0, 200, 0), 4), ((0, 0, 220), 8)):
        found = colour_spread(_filled(colour))
        heaviest = max(range(HUE_BINS), key=lambda i: found[f"hue_{i:02d}"])
        assert heaviest == expected, colour


def test_both_histograms_are_shares_that_sum_to_one():
    mixed = _filled((230, 120, 40))
    mixed[:, :, 16:] = (200, 200, 205)
    found = colour_spread(mixed)
    assert sum(found[f"hue_{i:02d}"] for i in range(HUE_BINS)) == pytest.approx(1.0)
    assert sum(found[f"saturation_{i:02d}"] for i in range(SATURATION_BINS)) == pytest.approx(1.0)


def test_unlit_water_is_left_out_rather_than_counted_as_grey():
    """Half the frame black, half orange: the saturation histogram is the orange half.

    Midwater footage is mostly water outside the lamp cone. Counting it as
    unsaturated would put nearly every slice of every midwater recording in the
    lowest bin and hide the animal that the lamps did reach.
    """
    frame = _filled((230, 120, 40))
    frame[:, :16] = 0
    found = colour_spread(frame)
    assert found[f"saturation_00"] == pytest.approx(0.0)
    assert found["colourful_fraction"] == pytest.approx(1.0)


def test_bleaching_moves_saturation_while_brightness_stays():
    """The measurement this exists for, as a directional check rather than a value."""
    pigmented = colour_spread(_filled((230, 120, 40)))
    bleached = colour_spread(_filled((235, 228, 225)))
    assert bleached["saturation_mean"] < pigmented["saturation_mean"]
    assert bleached["colourful_fraction"] < 0.05
    assert bleached[f"saturation_00"] > 0.9
    # and it is not simply a darker picture: value is what stays put
    assert hue_saturation_value(_filled((235, 228, 225))[0])[2].mean() > 0.85


def test_a_slice_with_no_colour_axis_reports_nothing():
    # Monochrome footage has no distribution to describe, and an absent column is
    # more honest than a zero that a reader will average.
    assert SliceColourSpreadProcessor().run_chunk(
        Chunk(np.zeros((4, 32, 32), np.uint8), dim_order="TYX")) == {}


def test_the_processor_writes_every_column_it_declares():
    row = SliceColourSpreadProcessor().run_chunk(Chunk(_filled((230, 120, 40))))
    assert set(row) == set(SliceColourSpreadProcessor.OUTPUT_SCHEMA)
    assert all(isinstance(value, float) for value in row.values())
    assert row[COLOURFULNESS] > 0
    assert row[SATURATION_MEAN] > 0.5
    assert row[COLOURFUL_FRACTION] == pytest.approx(1.0)


def test_every_column_says_what_its_denominator_is():
    # These columns travel into other people's plots; a share whose denominator is
    # not written down is a number nobody can compare with anything.
    for spec in SliceColourSpreadProcessor.METRICS:
        assert spec.description and len(spec.description) > 60


def test_reading_fewer_pixels_does_not_move_the_answer():
    """HD frames are read every second or third pixel; that has to be a detail."""
    large = np.zeros((2, 720, 1280, 3), np.uint8)
    large[..., :640, :] = (230, 120, 40)
    large[..., 640:, :] = (200, 200, 205)
    whole = colour_spread(large)
    strided = colour_spread(large[:, ::3, ::3, :])
    for index in range(SATURATION_BINS):
        assert strided[f"saturation_{index:02d}"] == pytest.approx(
            whole[f"saturation_{index:02d}"], abs=0.02)


def test_colourfulness_ranks_a_reef_above_sediment():
    reef = _filled((230, 120, 40))
    reef[:, :, 16:] = (40, 90, 200)
    assert colourfulness(reef[0]) > colourfulness(_filled((150, 140, 130))[0])
