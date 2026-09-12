"""The collection's own colours, as a picture.

One stripe per recording, oldest first, in the colours the processors measured.
What is checked here is that it is the data and not a decoration: the order is the
order it was filmed in, a recording's whole length is in its stripe, and nothing is
stretched to fill the palette.
"""

import numpy as np
import polars as pl
import pyarrow.parquet as pq
import pytest

from pixel_patrol_deepsea import banner

Image = pytest.importorskip("PIL.Image")


def _report(root, expedition, recordings):
    """A report with colour on every slice, the way the colour processor writes it.

    `recordings` is {name: (when, [(r, g, b), ...])}.
    """
    rows = []
    for name, (when, colours) in recordings.items():
        # The aggregate row for the whole recording carries no `dim_t` and an
        # average of averages; the banner has to skip it.
        rows.append({"name": name, "dim_t": None, "recorded_at": when,
                     "slice_red": 1.0, "slice_green": 1.0, "slice_blue": 1.0})
        for at, (red, green, blue) in enumerate(colours):
            rows.append({"name": name, "dim_t": at * 10, "recorded_at": when,
                         "slice_red": float(red), "slice_green": float(green),
                         "slice_blue": float(blue)})
    (root / "parquet").mkdir(parents=True, exist_ok=True)
    table = pl.DataFrame(rows, infer_schema_length=None).to_arrow()
    pq.write_table(table, root / "parquet" / f"{expedition}.parquet")
    return root


def _band(tmp_path):
    where = banner.build(tmp_path)
    return np.asarray(Image.open(where))


def test_one_stripe_per_recording(tmp_path):
    _report(tmp_path, "EX2107", {
        "a.mp4": ("2021-10-01T00:00:00+00:00", [(10, 20, 30)] * 4),
        "b.mp4": ("2021-10-02T00:00:00+00:00", [(40, 50, 60)] * 4)})
    band = _band(tmp_path)
    assert band.shape == (banner.HEIGHT, 2 * banner.STRIPE, 3)
    assert list(band[0, 0]) == [10, 20, 30]
    assert list(band[0, banner.STRIPE]) == [40, 50, 60]


def test_the_band_runs_in_the_order_it_was_filmed(tmp_path):
    """Two decades side by side is the point: 2004 at one end, last year at the
    other, whatever order the expeditions happen to sit in on disk."""
    _report(tmp_path, "LATER", {"z.mp4": ("2025-04-01T00:00:00+00:00", [(9, 9, 9)])})
    _report(tmp_path, "EARLIER", {"a.mp4": ("2004-07-01T00:00:00+00:00", [(3, 3, 3)])})
    band = _band(tmp_path)
    assert list(band[0, 0]) == [3, 3, 3]
    assert list(band[0, banner.STRIPE]) == [9, 9, 9]


def test_a_stripe_holds_the_whole_recording(tmp_path):
    """Top to bottom is start to end. A dive that goes from a lit seabed into the
    dark has to show that, which it does not if the stripe is one moment sampled."""
    colours = [(200, 200, 200)] * 50 + [(0, 0, 0)] * 50
    _report(tmp_path, "EX2107", {"a.mp4": ("2021-10-01T00:00:00+00:00", colours)})
    band = _band(tmp_path)
    assert band[0, 0, 0] > 190, "it starts light"
    assert band[-1, 0, 0] < 10, "and ends dark"


def test_nothing_is_stretched_to_fill_the_palette(tmp_path):
    """The whole collection is nearly cyan, and the picture should say so rather
    than normalise its way to a rainbow."""
    _report(tmp_path, "EX2107", {"a.mp4": ("2021-10-01T00:00:00+00:00",
                                           [(20, 60, 70)] * 8)})
    band = _band(tmp_path)
    assert set(map(tuple, band.reshape(-1, 3))) == {(20, 60, 70)}


def test_a_collection_with_no_colours_draws_nothing(tmp_path):
    (tmp_path / "parquet").mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"name": ["a.mp4"]}).write_parquet(tmp_path / "parquet" / "EX.parquet")
    assert banner.build(tmp_path) is None
