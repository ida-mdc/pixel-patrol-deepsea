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


def _frames(taxon, box, count=3, of=0, crop=None):
    """The steady frames the detector cuts beside an animal, as it writes them:
    extra detections flagged `clip`, belonging to the animal numbered `of`.

    Cut with the animal's own box, which is how a clip says whose it is. `of` is
    the detector's own numbering of the animals it filmed in the slice and means
    nothing outside it.
    """
    return [{"class": taxon, "conf": 0.5, "box": list(box), "clip": 1, "of": of,
             **({"crop": crop} if crop else {})}
            for _ in range(count)]


def _film(tag):
    """A frame nobody could confuse with another animal's."""
    return JPEG[:-2] + bytes([tag]) + JPEG[-2:]


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


def test_an_animal_animates_with_its_own_film_and_not_its_neighbours(tmp_path):
    """Two sea pens in one slice, each filmed - each tile plays its own.

    The slice cuts a clip per animal and numbers them in its own order, so the
    number cannot be assumed: taking `of` 0 for every animal is what showed a
    bottom covered in sea pens as the same sea pen twelve times over, a tile
    holding one animal and moving as another.
    """
    near, far = (10, 10, 60, 50), (560, 300, 610, 340)
    store, _ = _store(tmp_path, [[
        _animal("sea pen", 0.9, near), _animal("sea pen", 0.7, far),
        *_frames("sea pen", near, of=0, crop=_film(1)),
        *_frames("sea pen", far, of=1, crop=_film(2))]])
    animals, _stills, clips = _page(store, "sea pen")
    assert [a["c"] for a in animals] == [0.9, 0.7], "most confident first"
    played, at = [], 0
    for animal in animals:
        frames = []
        for size in animal.get("f", []):
            frames.append(clips[at:at + size])
            at += size
        played.append(set(frames))
    assert played == [{_film(1)}, {_film(2)}]


def test_an_animal_the_slice_never_filmed_does_not_move(tmp_path):
    """Only the few most convincing animals in a slice get a clip.

    The rest have no film of their own, and the nearest one is a different animal
    in a different place. A still tile says so; a tile playing its neighbour says
    something false.
    """
    near, far = (10, 10, 60, 50), (560, 300, 610, 340)
    store, _ = _store(tmp_path, [[
        _animal("sea pen", 0.9, near), _animal("sea pen", 0.7, far),
        *_frames("sea pen", near, of=0, crop=_film(1))]])
    animals, _stills, _clips = _page(store, "sea pen")
    assert [bool(a["m"]) for a in animals] == [True, False]


def test_the_index_says_where_a_recording_can_be_played_from(tmp_path):
    """A crop of a 640-pixel frame proves very little; the seconds around it do.

    The page opens the archive's own file at the second the animal was found, which
    it can only do if the store carries the URL the recording was listed from and
    the frame the box was drawn in.
    """
    import json as _json

    (tmp_path / "manifests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "manifests" / "EX2107.json").write_text(_json.dumps({"videos": [
        "https://ncei/EX2107/Video/dive_one.mp4",
        "https://ncei/EX2107/Video/another.mp4"]}))
    store, index = _store(tmp_path, [[_animal("fish", 0.8, (10, 20, 60, 90))]])
    where = index["where"]["EX2107"]
    # The report names its recording `dive_one`, which is what the manifest listed.
    assert where["videos"] == {"dive_one": "https://ncei/EX2107/Video/dive_one.mp4"}
    assert where["frame"] == [640, 360]
    animals, _stills, _clips = _page(store, "fish")
    assert animals[0]["b"] == [10, 20, 60, 90]


def test_a_recording_the_manifest_never_listed_gets_no_url(tmp_path):
    """Silence rather than a guess: a link to a file that is not there is worse
    than no link, and the page shows the crop and says so instead."""
    store, index = _store(tmp_path, [[_animal("fish", 0.8)]])
    assert index["where"]["EX2107"]["videos"] == {}


def test_a_transcoded_copy_is_still_the_recording_that_was_listed(tmp_path):
    """An older run analysed `..._Low_10fps.mp4`; the archive holds `..._Low.mp4`."""
    import json as _json

    from pixel_patrol_deepsea.tiles import _where

    (tmp_path / "parquet").mkdir(parents=True, exist_ok=True)
    (tmp_path / "manifests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "manifests" / "EX2107.json").write_text(_json.dumps({"videos": [
        "https://ncei/EX2107_VID_Low.mp4", "https://ncei/EX2107_VID_Low_OTHER.mp4"]}))
    got = _where(tmp_path, "EX2107", ["EX2107_VID_Low_10fps.mp4"], {})
    assert got["videos"] == {
        "EX2107_VID_Low_10fps.mp4": "https://ncei/EX2107_VID_Low.mp4"}


def test_the_pictures_do_not_pile_up_in_memory(tmp_path, monkeypatch):
    """What killed a site build with seventeen expeditions in it.

    Every still and every clip frame used to be held from the moment it was read
    until the last page was written, because a taxon's page cannot be written until
    every animal of it has been seen and sorted. That is gigabytes that grow with
    the collection. They go to a spool beside the store instead, and what is held is
    two numbers an animal.
    """
    from pixel_patrol_deepsea import tiles as store_module

    kept = []
    real = store_module._Spool.keep

    def watch(self, picture):
        kept.append(len(picture or b""))
        return real(self, picture)

    monkeypatch.setattr(store_module._Spool, "keep", watch)
    store, index = _store(tmp_path, [
        [_animal("fish", 0.9, (10, 10, 60, 50)), *_frames("fish", (10, 10, 60, 50))],
        [_animal("crab", 0.8, (560, 300, 610, 340))]])
    assert sum(kept) > 0, "the pictures went through the spool"
    # ...and it takes itself away afterwards.
    assert not (tmp_path / ".tiles-spool").exists()
    # ...and the store is the same store.
    animals, stills, clips = _page(store, "fish")
    assert animals[0]["l"] == len(JPEG) and stills == JPEG
    assert len(clips) == sum(animals[0]["f"])


def test_an_unreadable_report_is_left_out_rather_than_fatal(tmp_path):
    _report_with_animals(tmp_path, [[_animal("fish", 0.8)]])
    (tmp_path / "parquet" / "BROKEN.parquet").write_bytes(b"not a parquet at all")
    index = tiles.build(tmp_path)
    assert index["taxa"]["fish"]["count"] == 1
