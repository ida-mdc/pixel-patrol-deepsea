"""The expedition list, and finding the recordings in one."""

import pytest

from pixel_patrol_deepsea.catalogue import (
    Expedition, catalogue_path, discover, find_expedition, load_catalogue,
)

# A fake archive laid out the way NOAA's is: cruise / Video / dive / Compressed / files
ARCHIVE = {
    "http://a/Video/": ["DIVE01/", "DIVE02/", "readme.txt"],
    "http://a/Video/DIVE01/": ["Compressed/"],
    "http://a/Video/DIVE01/Compressed/": ["one_ROVHD_Low.mp4", "one_CPHD_Low.mp4"],
    "http://a/Video/DIVE02/": ["Compressed/"],
    "http://a/Video/DIVE02/Compressed/": ["two_ROVHD_Low.mp4", "notes.xml"],
}


def listing(url):
    return ARCHIVE[url]


@pytest.fixture
def cruise():
    return Expedition(id="A", listing="http://a/Video/", depth=2, pattern="*ROVHD_Low.mp4")


def test_finds_every_recording_matching_the_pattern(cruise):
    found = discover(cruise, listing=listing).videos
    assert found == ["http://a/Video/DIVE01/Compressed/one_ROVHD_Low.mp4",
                     "http://a/Video/DIVE02/Compressed/two_ROVHD_Low.mp4"]


def test_leaves_out_the_other_camera_and_the_paperwork(cruise):
    found = discover(cruise, listing=listing).videos
    assert not any("CPHD" in url or url.endswith(".xml") for url in found)


def test_stops_at_the_declared_depth(cruise):
    # Depth is written down rather than crawled, so an archive with a mistake in it
    # fails to list instead of walking the whole server.
    shallow = Expedition(id="A", listing="http://a/Video/", depth=0, pattern="*.mp4")
    assert discover(shallow, listing=listing).videos == []


def test_says_when_it_looked(cruise):
    assert discover(cruise, listing=listing).listed_at.startswith("20")


def test_a_manifest_round_trips_as_json(cruise):
    import json
    manifest = discover(cruise, listing=listing)
    assert json.loads(manifest.to_json())["videos"] == manifest.videos


def test_the_shipped_catalogue_reads(tmp_path):
    catalogue = load_catalogue(catalogue_path())
    assert len(catalogue) >= 3
    assert all(e.id for e in catalogue)
    assert all(e.depth >= 0 for e in catalogue)
    # Each one either says where to look or says what to fetch.
    for entry in catalogue:
        assert entry.listing.startswith("http") or entry.videos, entry.id
        assert all(url.startswith("http") for url in entry.videos), entry.id


def test_ignores_catalogue_keys_it_does_not_know(tmp_path):
    # So a note added for a human does not break the loader.
    path = tmp_path / "e.yaml"
    path.write_text("- id: X\n  listing: http://x/\n  who_added_it: someone\n")
    assert load_catalogue(path)[0].id == "X"


def test_looks_an_expedition_up_by_id():
    catalogue = load_catalogue(catalogue_path())
    assert find_expedition(catalogue, "EX2107").id == "EX2107"
    with pytest.raises(LookupError):
        find_expedition(catalogue, "nope")


def test_an_expedition_can_name_its_recordings_outright():
    """For a set that is small, fixed and not behind a directory index - a
    benchmark, say - listing costs no HTTP at all."""
    named = Expedition(id="B", videos=["http://b/one.mov", "http://b/two.mov"])
    manifest = discover(named, listing=lambda url: (_ for _ in ()).throw(
        AssertionError("should not have gone to the network")))
    assert manifest.videos == ["http://b/one.mov", "http://b/two.mov"]


def test_ground_truth_sits_beside_each_recording():
    from pixel_patrol_deepsea.catalogue import truth_url

    scored = Expedition(id="B", videos=["http://b/MWD/MWD.mov"], truth="gt.txt")
    assert truth_url(scored, scored.videos[0]) == "http://b/MWD/gt.txt"


def test_an_expedition_without_ground_truth_has_none_to_point_at():
    from pixel_patrol_deepsea.catalogue import truth_url

    assert truth_url(Expedition(id="B", videos=["http://b/x.mov"]), "http://b/x.mov") is None


def test_the_shipped_catalogue_has_one_scoreable_expedition():
    # Every other number this package produces is a claim; on this one it is checkable.
    catalogue = load_catalogue(catalogue_path())
    scoreable = [e for e in catalogue if e.truth]
    assert scoreable, "no expedition ships per-frame ground truth"
    assert all(e.videos for e in scoreable)
