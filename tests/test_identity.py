"""Which detections are the same animal, written into the report.

The thing being protected is that there is exactly one answer to that question and
it is in the file. Before this the page linked detections itself, the viewer
grouped slices its own way in the browser, and the parquet - the artifact the
cluster produces and the one anyone else opens - had no notion of an individual
at all. Three consumers, three answers, none of them written down.
"""

import json

import polars as pl
import pyarrow.parquet as pq
import pytest

from pixel_patrol_deepsea.identity import ANIMAL, animals_from, has_ids, identify
from pixel_patrol_deepsea.refine import Sighting, track_sightings


def _detection(second, box, taxon="fish", conf=0.9, **extra):
    return {"second": second, "frame": int(second * 10), "class": taxon,
            "conf": conf, "box": list(box), **extra}


def _report(tmp_path, rows, metadata=None):
    """A report shaped like the pipeline's: one row per slice, detections as JSON."""
    table = pl.DataFrame({
        "name": [name for name, _t, _d in rows],
        "dim_t": [t for _n, t, _d in rows],
        "detections": [json.dumps(d) if d is not None else None for _n, _t, d in rows],
    })
    path = tmp_path / "report.parquet"
    arrow = table.to_arrow()
    if metadata:
        arrow = arrow.replace_schema_metadata(metadata)
    pq.write_table(arrow, path)
    return path


def _stored(path):
    """Every non-clip detection's animal id, in file order."""
    out = []
    for blob in pl.read_parquet(path)["detections"].to_list():
        if blob:
            out += [a.get(ANIMAL) for a in json.loads(blob) if not a.get("clip")]
    return out


def test_one_fish_across_three_slices_is_one_animal(tmp_path):
    report = _report(tmp_path, [
        ("BD", 0, [_detection(0.0, (10, 10, 40, 40))]),
        ("BD", 1, [_detection(1.0, (12, 11, 42, 41))]),
        ("BD", 2, [_detection(2.0, (14, 12, 44, 42))]),
    ])
    assert identify(report) == 1
    assert _stored(report) == [0, 0, 0]


def test_two_fish_are_two_animals_and_keep_their_own_ids(tmp_path):
    report = _report(tmp_path, [
        ("BD", 0, [_detection(0.0, (10, 10, 40, 40)), _detection(0.0, (300, 300, 330, 330))]),
        ("BD", 1, [_detection(1.0, (12, 11, 42, 41)), _detection(1.0, (302, 301, 332, 331))]),
    ])
    assert identify(report) == 2
    first, second, third, fourth = _stored(report)
    assert first == third and second == fourth and first != second


def test_the_same_number_in_two_recordings_is_two_animals(tmp_path):
    """Identity is the pair, not the number: tracking cannot cross recordings."""
    report = _report(tmp_path, [
        ("BD", 0, [_detection(0.0, (10, 10, 40, 40))]),
        ("BS", 0, [_detection(0.0, (10, 10, 40, 40))]),
    ])
    assert identify(report) == 2
    assert _stored(report) == [0, 0]        # both are animal 0, of different tapes
    table = pl.read_parquet(report)
    assert set(table["name"].to_list()) == {"BD", "BS"}


def test_a_clip_carries_the_id_of_the_animal_it_is_of(tmp_path):
    # A clip is frames cut around one animal with one box; it says which by index.
    # Without this the film and the individual it shows are joined by nothing.
    report = _report(tmp_path, [
        ("BD", 0, [_detection(0.0, (10, 10, 40, 40)),
                   _detection(0.0, (300, 300, 330, 330)),
                   _detection(0.0, (300, 300, 330, 330), clip=True, of=1),
                   _detection(0.0, (10, 10, 40, 40), clip=True, of=0)]),
    ])
    identify(report)
    animals = json.loads(pl.read_parquet(report)["detections"].to_list()[0])
    real = [a for a in animals if not a.get("clip")]
    clips = [a for a in animals if a.get("clip")]
    assert clips[0][ANIMAL] == real[1][ANIMAL]
    assert clips[1][ANIMAL] == real[0][ANIMAL]


def test_the_report_keeps_the_metadata_that_names_it(tmp_path):
    # Rewritten through arrow rather than polars' writer for exactly this reason:
    # a report that loses pp_project_name opens as an untitled table.
    report = _report(tmp_path, [("BD", 0, [_detection(0.0, (10, 10, 40, 40))])],
                     metadata={b"pp_project_name": b"MBARI DeepSea-MOT",
                               b"pp_loader": b"video"})
    identify(report)
    kept = pq.read_schema(report).metadata
    assert kept[b"pp_project_name"] == b"MBARI DeepSea-MOT"
    assert kept[b"pp_loader"] == b"video"


def test_the_stored_ids_say_what_the_tracker_said(tmp_path):
    """Reading the ids and deriving them again have to agree, or one is a lie."""
    rows = [("BD", t, [_detection(t / 2, (10 + t, 10, 40 + t, 40)),
                       _detection(t / 2, (400, 300, 430, 330))]) for t in range(6)]
    report = _report(tmp_path, rows)
    identify(report)

    table = pl.read_parquet(report)
    sightings, ids = [], []
    for name, slice_t, blob in zip(table["name"], table["dim_t"], table["detections"]):
        for animal in json.loads(blob):
            if animal.get("clip"):
                continue
            sightings.append(Sighting(recording=name, second=animal["second"],
                                      frame=animal["frame"], slice_t=slice_t,
                                      taxon=animal["class"], confidence=animal["conf"],
                                      box=tuple(animal["box"]), crop=None))
            ids.append(animal[ANIMAL])

    stored = sorted(len(t.sightings) for t in animals_from(sightings, ids))
    derived = sorted(len(t.sightings) for t in track_sightings(sightings))
    assert stored == derived


def test_a_report_with_no_detections_is_left_alone(tmp_path):
    report = _report(tmp_path, [("BD", 0, None), ("BD", 1, [])])
    assert identify(report) == 0
    assert pl.read_parquet(report)["detections"].to_list() == [None, "[]"]


def test_reports_written_before_this_are_recognised_as_such(tmp_path):
    without = [json.dumps([_detection(0.0, (1, 1, 2, 2))])]
    assert has_ids(without) is False
    report = _report(tmp_path, [("BD", 0, [_detection(0.0, (1, 1, 2, 2))])])
    identify(report)
    assert has_ids(pl.read_parquet(report)["detections"].to_list()) is True


def test_the_recordings_own_summary_row_is_not_a_sighting(tmp_path):
    """A report carries one aggregate row per recording whose `detections` is a
    summary of the slices under it. Tagging that would count an animal twice - once
    where it was seen and once in the summary of having seen it - so it is left
    alone, and asking whether a report has ids has to look past it.
    """
    report = _report(tmp_path, [
        ("BD", None, [_detection(0.0, (10, 10, 40, 40))]),      # the aggregate row
        ("BD", 0, [_detection(0.0, (10, 10, 40, 40))]),
        ("BD", 1, [_detection(1.0, (12, 11, 42, 41))]),
    ])
    assert identify(report) == 1
    blobs = pl.read_parquet(report)["detections"].to_list()
    assert ANIMAL not in json.loads(blobs[0])[0]
    assert [json.loads(b)[0][ANIMAL] for b in blobs[1:]] == [0, 0]
    assert has_ids(blobs) is True


def test_identifying_twice_changes_nothing(tmp_path):
    report = _report(tmp_path, [
        ("BD", 0, [_detection(0.0, (10, 10, 40, 40))]),
        ("BD", 1, [_detection(1.0, (12, 11, 42, 41))]),
    ])
    assert identify(report) == 1
    once = pl.read_parquet(report)["detections"].to_list()
    assert identify(report) == 1
    assert pl.read_parquet(report)["detections"].to_list() == once
