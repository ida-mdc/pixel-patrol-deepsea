"""Scoring a report against per-frame ground truth.

The point of the module, and of these tests, is that a coarse first pass must not
produce wrong answers - only fewer of them. So the matching is done in time, which
survives re-encoding, rather than in frame numbers, which do not.
"""

import pytest

from pixel_patrol_deepsea.groundtruth import (
    first_annotated_frame, read_mot, score_recording,
)

# frame 1 at 0.0 s, frame 121 at 4.0 s when the original runs at 30 fps
TRUTH = read_mot("\n".join([
    "1,1,10,10,20,20,1,1,1",
    "121,1,50,50,20,20,1,1,1",
    "121,2,300,300,20,20,1,1,1",
]))


def _found(second, box, conf=0.8):
    return {"second": second, "box": box, "conf": conf}


def test_reads_boxes_per_frame_from_a_mot_file():
    assert sorted(TRUTH) == [1, 121]
    # x1 y1 x2 y2 and the identity the benchmark tracks the animal under, without
    # which an animal in view for 300 frames counts as 300 animals
    assert TRUTH[1] == [(10.0, 10.0, 30.0, 30.0, 1)]
    assert len(TRUTH[121]) == 2


def test_ignores_lines_it_cannot_read():
    assert read_mot("nonsense\n\n1,1,0,0,5,5,1,1,1") == {1: [(0.0, 0.0, 5.0, 5.0, 1)]}


def test_knows_whether_the_file_counts_from_zero_or_one():
    assert first_annotated_frame(TRUTH) == 1
    assert first_annotated_frame({0: [(0, 0, 1, 1)]}) == 0


def test_a_detection_from_a_thinned_copy_still_matches_the_right_frame():
    """The whole reason this works in seconds. A coarse pass may analyse a ten-frame
    copy of a thirty-frame recording, where frame 40 of the copy is frame 121 of the
    original. Its second is 4.0 either way."""
    score = score_recording("MWD", [_found(4.0, (51, 51, 70, 70))], TRUTH,
                            fps=30.0, first_frame=1)
    assert score.matched == 1
    assert score.precision == 1.0


def test_a_detection_nowhere_near_a_real_animal_counts_against_precision():
    score = score_recording("MWD", [_found(4.0, (600, 400, 620, 420))], TRUTH,
                            fps=30.0, first_frame=1)
    assert (score.detections, score.matched) == (1, 0)
    assert score.precision == 0.0


def test_recall_is_measured_only_on_the_frames_that_were_looked_at():
    """Judging the model on frames nobody sampled would report a recall near zero
    and say nothing about the model."""
    score = score_recording("MWD", [_found(4.0, (51, 51, 70, 70))], TRUTH,
                            fps=30.0, first_frame=1)
    assert score.frames_judged == 1          # frame 121 only, not frame 1
    assert score.truth_boxes == 2            # the two boxes in that frame
    assert score.recall == pytest.approx(0.5)


def test_two_detections_cannot_both_claim_one_animal():
    score = score_recording("MWD", [_found(4.0, (51, 51, 70, 70), 0.9),
                                    _found(4.0, (52, 52, 71, 71), 0.4)], TRUTH,
                            fps=30.0, first_frame=1)
    assert (score.detections, score.matched) == (2, 1)


def test_a_detection_at_a_moment_with_no_annotation_is_left_out():
    # Nothing to be right or wrong against.
    score = score_recording("MWD", [_found(2.0, (10, 10, 30, 30))], TRUTH,
                            fps=30.0, first_frame=1)
    assert score.frames_judged == 0
    assert score.precision is None


def test_a_recording_with_no_frame_rate_scores_nothing_rather_than_guessing():
    score = score_recording("MWD", [_found(4.0, (51, 51, 70, 70))], TRUTH,
                            fps=0.0, first_frame=1)
    assert score.frames_judged == 0


# ── counting creatures rather than boxes ──────────────────────────────────────
#
# The benchmark boxes the same animal in every frame it is visible, so 94 animals
# in BD are 28,708 boxes. Recall over boxes therefore mostly measures how often
# each animal was re-found; recall over identities measures whether it was found.

TWO_ANIMALS = read_mot("\n".join([
    "1,7,10,10,20,20,1,1,1",       # animal 7, frames 1 and 121
    "121,7,10,10,20,20,1,1,1",
    "121,8,300,300,20,20,1,1,1",   # animal 8, frame 121 only
]))


def test_one_sighting_is_enough_to_have_found_an_animal():
    # looked at frame 1 only, and found animal 7 there
    score = score_recording("d.mov", [_found(0.0, (10, 10, 30, 30))],
                            TWO_ANIMALS, fps=30.0)
    assert score.matched == 1
    assert score.recall == pytest.approx(1.0)          # of the boxes it looked at
    assert score.recall_overall == pytest.approx(1 / 3)  # of every box
    assert score.recall_animals == pytest.approx(0.5)   # of animal 7 and animal 8


def test_finding_the_same_animal_twice_is_still_one_animal():
    score = score_recording("d.mov", [_found(0.0, (10, 10, 30, 30)),
                                      _found(4.0, (10, 10, 30, 30))],
                            TWO_ANIMALS, fps=30.0)
    assert score.matched == 2                           # two boxes matched
    assert score.animals_found == {7}                   # one creature
    assert score.recall_animals == pytest.approx(0.5)


def test_the_fraction_of_frames_read_is_reported():
    score = score_recording("d.mov", [_found(0.0, (10, 10, 30, 30))],
                            TWO_ANIMALS, fps=30.0)
    assert score.frames_annotated == 2
    assert score.frames_judged == 1
    assert score.looked_at == pytest.approx(0.5)
