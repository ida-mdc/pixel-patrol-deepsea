"""Choosing which recordings of an expedition to analyse.

Choosing badly is expensive in a way that is invisible afterwards: the report
comes out looking complete and full of nothing. So what is protected here is that
the descent is left out, that the sample is spread across the dive rather than
taken from its first few minutes, and that a missing dive report costs footage
rather than silently dropping it.
"""

from pixel_patrol_deepsea.catalogue import Expedition
from pixel_patrol_deepsea.selection import (
    Dive, by_dive, choose, on_the_bottom, spread,
)

BASE = "https://x/Video/EX2503_DIVE02_20250412/Compressed/EX2503_VID_2025041"


def _url(stamp: str, dive: str = "02", day: str = "2") -> str:
    return (f"https://x/Video/EX2503_DIVE{dive}_2025041{day}/Compressed/"
            f"EX2503_VID_{stamp}_ROVHD_Low.mp4")


ON_BOTTOM = {"on_bottom_at": "2025-04-12T20:00:00", "off_bottom_at": "2025-04-13T00:00:00",
             "max_depth_m": 4859.0}


def test_groups_recordings_by_the_dive_they_belong_to():
    dives = by_dive([_url("20250412T190000Z", "02"), _url("20250412T200000Z", "02"),
                     _url("20250426T210000Z", "13", "3")])
    assert sorted(dives) == [2, 13]
    assert len(dives[2].videos) == 2


def test_leaves_out_the_descent_and_the_ascent():
    dive = Dive(number=2, summary=ON_BOTTOM, videos=[
        _url("20250412T183000Z"),        # still descending
        _url("20250412T210000Z"),        # working
        _url("20250412T230000Z"),        # working
        _url("20250413T013000Z"),        # already ascending
    ])
    kept = on_the_bottom(dive)
    assert len(kept) == 2
    assert all("T21" in u or "T23" in u for u in kept)


def test_keeps_everything_when_there_is_no_dive_report():
    # dropping footage that might hold the only animal in the dive is not
    # recoverable, so no answer beats a wrong one
    dive = Dive(number=2, videos=[_url("20250412T183000Z"), _url("20250412T210000Z")])
    assert len(on_the_bottom(dive)) == 2


def test_keeps_everything_when_nothing_falls_inside_the_window():
    dive = Dive(number=2, summary=ON_BOTTOM, videos=[_url("20250101T010000Z")])
    assert len(on_the_bottom(dive)) == 1


def test_spreads_the_sample_over_the_dive_rather_than_taking_the_first_few():
    items = [f"{i:03d}" for i in range(100)]
    picked = spread(items, 5)
    assert len(picked) == 5
    assert picked[0] == "000"
    assert picked[-1] != "004"          # not the first five
    assert int(picked[-1]) > 70         # reaches the end of the dive


def test_spreading_fewer_than_asked_for_returns_them_all():
    assert spread(["a", "b"], 5) == ["a", "b"]
    assert spread(["a", "b"], 0) == ["a", "b"]


def test_an_expedition_that_names_its_recordings_has_already_chosen():
    named = Expedition(id="OOI", videos=["https://x/CAMHDA301-20160815T030000Z.mp4",
                                         "https://x/CAMHDA301-20180815T031500.mp4"])
    assert len(choose(named, named.videos)) == 2
    assert len(choose(named, named.videos, most=1)) == 1


def test_deepest_dives_first():
    from pixel_patrol_deepsea import selection

    videos = [_url("20250412T210000Z", "02"), _url("20250426T210000Z", "13", "3"),
              _url("20250427T210000Z", "14", "4")]
    depths = {2: 4859.0, 13: 4599.0, 14: 2397.0}
    ordered = sorted(by_dive(videos).values(),
                     key=lambda d: -depths[d.number])
    assert [d.number for d in ordered] == [2, 13, 14]
    # and Dive reports the number the archive stated, not one recomputed here
    assert Dive(number=2, summary=ON_BOTTOM).max_depth_m == 4859.0
    assert Dive(number=9).max_depth_m == 0.0
