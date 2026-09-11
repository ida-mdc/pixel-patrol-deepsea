"""The store the landing page browses.

The pictures are raw JPEG laid end to end and the JSON only says how long each one
is, so nothing in the store says where a picture starts - the page adds the lengths
up. That is cheap and it is also the one thing that can go quietly wrong: an
off-by-one in the lengths shows up as tiles of static rather than as an error. So
these tests do what the page does - add the lengths up and cut - and insist on
getting whole JPEGs back.
"""

import json

from pixel_patrol_deepsea import tiles
from tests.test_catalogue_page import JPEG, _animal, _report_with_animals


def _frames(taxon, box, count=3):
    """The steady frames the detector cuts beside an animal, as it writes them:
    extra detections flagged `clip`, belonging to the animal numbered `of`."""
    return [{"class": taxon, "conf": 0.5, "box": list(box), "clip": 1, "of": 0}
            for _ in range(count)]


def _store(tmp_path, entries):
    _report_with_animals(tmp_path, entries)
    index = tiles.build(tmp_path)
    return tmp_path / "tiles", index


def _page(store, taxon, page=0):
    where = store / tiles.slug(taxon)
    return (json.loads((where / f"p{page}.json").read_text()),
            (where / f"p{page}.jpgs").read_bytes(),
            (where / f"p{page}.clips").read_bytes()
            if (where / f"p{page}.clips").exists() else b"")


def test_cutting_the_stills_by_their_lengths_gives_back_whole_pictures(tmp_path):
    store, _ = _store(tmp_path, [[_animal("fish", 0.8)]])
    animals, stills, _clips = _page(store, "fish")
    at = 0
    for animal in animals:
        crop = stills[at:at + animal["l"]]
        at += animal["l"]
        assert crop == JPEG
    # Nothing left over: the blob is exactly the tiles it claims to hold.
    assert at == len(stills)


def test_cutting_the_clips_by_their_lengths_gives_back_whole_frames(tmp_path):
    # Two animals, each with the steady frames the detector cuts beside it, so the
    # second one's frames only line up if the page counts past the first one's.
    store, _ = _store(tmp_path, [
        [_animal("fish", 0.9, (10, 10, 60, 50)), *_frames("fish", (10, 10, 60, 50))],
        [_animal("fish", 0.8, (560, 300, 610, 340)),
         *_frames("fish", (560, 300, 610, 340))]])
    animals, _stills, clips = _page(store, "fish")
    assert sum(1 for a in animals if a.get("f")) == 2, "both animals should animate"
    at = 0
    for animal in animals:
        for size in animal.get("f", []):
            assert clips[at:at + size] == JPEG
            at += size
    assert at == len(clips)


def test_an_animal_with_no_animation_is_not_given_a_play_badge(tmp_path):
    store, _ = _store(tmp_path, [[_animal("fish", 0.8)]])
    animals, _stills, _clips = _page(store, "fish")
    for animal in animals:
        assert bool(animal["m"]) == bool(animal.get("f"))


def test_the_index_counts_what_the_pages_hold(tmp_path):
    store, index = _store(tmp_path, [[_animal("fish", 0.8, (10, 10, 60, 50)),
                                      _animal("crab", 0.7, (560, 300, 610, 340))]])
    for taxon, about in index["taxa"].items():
        held = 0
        for page in range(about["pages"]):
            held += len(_page(store, taxon, page)[0])
        assert held == about["count"], taxon


def test_no_picture_is_carried_in_the_json(tmp_path):
    # Base64 in the metadata was a third of the store. The page reads bytes now, so
    # a crop appearing in the JSON again is a regression worth failing on.
    store, _ = _store(tmp_path, [[_animal("fish", 0.8)]])
    text = (store / tiles.slug("fish") / "p0.json").read_text()
    assert "/9j/" not in text and "ffd8" not in text.lower()
    assert len(text) < 400


def test_rebuilding_leaves_nothing_of_the_last_build_behind(tmp_path):
    store, _ = _store(tmp_path, [[_animal("fish", 0.8)]])
    stale = store / "gone" / "p0.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("[]")
    tiles.build(tmp_path)
    assert not stale.exists()
    assert (store / "index.json").exists()


def test_pages_hold_no_more_than_a_pageful(tmp_path):
    many = [[_animal("fish", 0.9, (10 + i, 10, 60 + i, 50))] for i in range(3)]
    store, index = _store(tmp_path, many)
    for taxon, about in index["taxa"].items():
        for page in range(about["pages"]):
            assert len(_page(store, taxon, page)[0]) <= tiles.PER_PAGE
