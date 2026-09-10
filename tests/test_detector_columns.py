"""What the detector carries out of a slice, and how it rolls up.

The rollup rule is the subtle part: every row in the table is an aggregation over
leaf rows, the per-slice rows included, because a rollup groups leaves by the dims
it fixes rather than chaining level to level. An aggregation that returns None for
"nothing to say above the slice" therefore returns None everywhere, and an all-null
column is dropped from the parquet outright.
"""

import json

from pixel_patrol_deepsea.detector_processor import (
    DETECTION_CONFIDENCE, DETECTIONS, FathomNetDetectorProcessor, _detections_agg,
    _individuals,
)


def _spec():
    return next(s for s in FathomNetDetectorProcessor.METRICS if s.name == DETECTIONS)


def _leaf(animals, confidence):
    return {DETECTIONS: json.dumps(animals), DETECTION_CONFIDENCE: confidence}


def test_a_single_leaf_carries_its_animals_up_unchanged():
    # This is the per-slice row, and it is an aggregation of one leaf.
    animals = [{"class": "fish", "conf": 0.7}]
    got = _detections_agg(_spec(), [_leaf(animals, 0.7)])
    assert json.loads(got) == animals


def test_the_most_confident_leaf_wins():
    quiet = _leaf([{"class": "shrimp", "conf": 0.2}], 0.2)
    loud = _leaf([{"class": "fish", "conf": 0.9}], 0.9)
    got = json.loads(_detections_agg(_spec(), [quiet, loud]))
    assert got[0]["class"] == "fish"


def test_leaves_with_no_animals_do_not_win():
    empty = {DETECTION_CONFIDENCE: 0.99}
    found = _leaf([{"class": "fish", "conf": 0.3}], 0.3)
    got = json.loads(_detections_agg(_spec(), [empty, found]))
    assert got[0]["class"] == "fish"


def test_nothing_at_all_gives_nothing():
    assert _detections_agg(_spec(), [{DETECTION_CONFIDENCE: 0.5}]) is None
    assert _detections_agg(_spec(), []) is None


def test_the_column_is_declared_so_the_schema_catalog_can_see_it():
    assert DETECTIONS in FathomNetDetectorProcessor.OUTPUT_SCHEMA
    assert "per-animal" in FathomNetDetectorProcessor.OUTPUT_SCHEMA_DESCRIPTIONS[DETECTIONS]


def _at(box, conf):
    return {"class": "sea pen", "conf": conf, "box": box, "frame": 0}


def test_the_same_animal_seen_in_three_frames_is_one_individual():
    # A slice looks at several frames, so a stationary animal appears once per frame
    # with the box wobbling by a pixel or two.
    frames = [_at([100, 100, 200, 200], 0.8),
              _at([101, 102, 201, 201], 0.7),
              _at([99, 100, 199, 199], 0.6)]
    assert len(_individuals(frames)) == 1


def test_neighbours_on_a_crowded_seabed_stay_apart():
    # Twelve sea pens in one frame are twelve animals, not one detected twelve times.
    apart = [_at([100, 100, 200, 200], 0.8),
             _at([400, 100, 500, 200], 0.7),
             _at([100, 400, 200, 500], 0.6)]
    assert len(_individuals(apart)) == 3


def test_the_most_convincing_sighting_represents_its_animal():
    frames = [_at([100, 100, 200, 200], 0.4), _at([102, 101, 202, 201], 0.9)]
    assert _individuals(frames)[0]["conf"] == 0.9


def test_individuals_come_back_most_convincing_first():
    mixed = [_at([400, 100, 500, 200], 0.5), _at([100, 100, 200, 200], 0.9)]
    assert [a["conf"] for a in _individuals(mixed)] == [0.9, 0.5]
