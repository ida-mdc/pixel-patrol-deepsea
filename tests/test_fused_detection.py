"""Fusing several reads of one frame, and following an animal through a clip.

Neither of these needs the model, which is why they can be tested at all: fusion
is arithmetic over boxes, and the tracker is correlation over pixels. Both were
measured against annotated footage before being written - the numbers are in
`detector.FUSED_SIZES` and in the README - and what is protected here is the
behaviour those numbers depend on.
"""

import numpy as np
import pytest

from pixel_patrol_deepsea.detector import Detection, _group_across_passes, same_animal
from pixel_patrol_deepsea.detector_processor import _follow, _grey, _nudge


def _greys(stack):
    """What the processor hands the tracker: the slice, already greyscale."""
    return np.stack([_grey(frame) for frame in stack])


def _at(confidence, box, klass=0):
    return Detection(confidence=confidence, class_index=klass, box=box)


# ── one animal or two ─────────────────────────────────────────────────────────

def test_overlapping_boxes_are_one_animal():
    assert same_animal((10, 10, 50, 50), (12, 12, 52, 52))


def test_boxes_that_do_not_touch_are_two_animals():
    assert not same_animal((10, 10, 50, 50), (200, 200, 240, 240))


def test_a_box_almost_inside_a_bigger_one_is_the_same_animal():
    # a jelly's bell against its bell plus tentacles: the overlap is small
    # relative to the union, so plain IoU calls them two
    bell, whole = (100, 100, 120, 120), (100, 100, 180, 180)
    intersection_over_union = (20 * 20) / (80 * 80)
    assert intersection_over_union < 0.45
    assert same_animal(bell, whole)


# ── fusing the passes ─────────────────────────────────────────────────────────

def test_agreement_between_passes_beats_a_single_loud_opinion():
    seen_by_all = [[_at(0.30, (10, 10, 50, 50))]] * 3
    seen_once = [[_at(0.80, (300, 300, 340, 340))], [], []]
    passes = [a + b for a, b in zip(seen_by_all, seen_once)]

    scored = {best.box: total / 3 for best, total in _group_across_passes(passes)}
    quiet_but_agreed = scored[(10, 10, 50, 50)]
    loud_but_alone = scored[(300, 300, 340, 340)]
    assert quiet_but_agreed == pytest.approx(0.30)
    assert loud_but_alone == pytest.approx(0.80 / 3)
    assert quiet_but_agreed > loud_but_alone


def test_the_union_of_the_passes_is_kept_not_the_intersection():
    passes = [[_at(0.5, (0, 0, 20, 20))],
              [_at(0.5, (100, 100, 120, 120))],
              [_at(0.5, (200, 200, 220, 220))]]
    assert len(_group_across_passes(passes)) == 3


def test_one_pass_gets_one_vote_however_many_boxes_it_puts_on_the_animal():
    # A pass can propose a jelly's bell and its bell plus tentacles: suppression
    # within the pass keeps both, and grouping joins them. Counting both made a
    # "mean over three sizes" come out above 1.0.
    passes = [[_at(0.9, (100, 100, 120, 120)), _at(0.8, (100, 100, 180, 180))],
              [_at(0.9, (100, 100, 122, 122))],
              [_at(0.9, (100, 100, 121, 121))]]
    (_best, total), = _group_across_passes(passes)
    assert total == pytest.approx(2.7)
    assert total / 3 <= 1.0


def test_the_best_localised_box_represents_its_group():
    passes = [[_at(0.9, (10, 10, 50, 50), klass=7)],
              [_at(0.2, (11, 11, 53, 53), klass=3)]]
    (best, total), = _group_across_passes(passes)
    assert best.box == (10, 10, 50, 50)
    assert best.class_index == 7          # the label of the pass that saw it clearly
    assert total == pytest.approx(1.1)


# ── following the animal through the clip ─────────────────────────────────────

def _drifting(step=3, frames=8, size=20, at=(40, 90)):
    """A textured patch moving `step` pixels right per frame over noise."""
    rng = np.random.default_rng(1)
    texture = (rng.random((size, size)) * 120 + 120).astype(np.uint8)
    stack = []
    for i in range(frames):
        frame = (rng.random((200, 300, 3)) * 30).astype(np.uint8)
        x, y = at[0] + step * i, at[1]
        frame[y:y + size, x:x + size] = texture[..., None]
        stack.append(frame)
    return np.stack(stack)


def test_the_crop_follows_the_animal():
    stack = _drifting()
    boxes = _follow(_greys(stack), (40, 90, 60, 110), list(range(8)), detected_at=0)
    assert boxes[0] == (40, 90, 60, 110)
    assert boxes[7] == (61, 90, 81, 110)          # 3 px a frame, seven frames on
    # the box keeps its size, so the animal does not change scale between frames
    assert all((b[2] - b[0], b[3] - b[1]) == (20, 20) for b in boxes.values())


def test_tracking_runs_both_ways_from_the_frame_the_model_fired_on():
    stack = _drifting()
    boxes = _follow(_greys(stack), (52, 90, 72, 110), list(range(8)), detected_at=4)
    assert boxes[4] == (52, 90, 72, 110)
    assert boxes[0][0] < boxes[4][0] < boxes[7][0]


def test_a_flat_patch_does_not_move_rather_than_jumping():
    # correlation of a patch with no variation in it is undefined and comes back
    # as NaN, which compares false against any threshold - a flat stretch of open
    # water is exactly this, so it has to be caught rather than trusted
    rng = np.random.default_rng(2)
    flat = np.stack([(rng.random((200, 300, 3)) * 4 + 10).astype(np.uint8) for _ in range(4)])
    boxes = _follow(_greys(flat), (40, 90, 60, 110), list(range(4)), detected_at=0)
    assert set(boxes.values()) == {(40, 90, 60, 110)}


def test_the_box_stays_put_when_the_animal_has_left_the_frame():
    stack = _drifting(frames=3)
    stack[2] = 0            # nothing in reach looks like what was there
    greys = _greys(stack)
    box = _nudge(greys[1], greys[2], (43, 90, 63, 110))
    assert box == (43, 90, 63, 110)
