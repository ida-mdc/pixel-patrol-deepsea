"""The point of refinement is to spend inference only where it can pay off, so the
tests are about which frames get visited and which do not."""
import pytest

from pixel_patrol_deepsea.refine import (
    Window,
    _frames_to_visit,
    windows_with_detections,
)


def rows(pairs, fps=30.0):
    return [{"dim_t": second * fps, "detection_count": count} for second, count in pairs]


def test_finds_the_seconds_that_saw_something():
    found = windows_with_detections(rows([(0, 2), (1, 0), (30, 1)]), fps=30.0)
    assert [w.start for w in found] == [0.0, 30.0]


def test_merges_hits_that_are_almost_certainly_one_animal():
    # A coarse pass samples every few seconds; consecutive hits are the same subject,
    # and refining each separately would run the same footage twice.
    found = windows_with_detections(rows([(10, 1), (12, 2), (14, 1)]), fps=30.0, merge_gap=4.0)
    assert found == [Window(10.0, 14.0)]


def test_keeps_separate_encounters_apart():
    found = windows_with_detections(rows([(10, 1), (40, 1)]), fps=30.0, merge_gap=4.0)
    assert len(found) == 2


def test_a_recording_with_nothing_in_it_is_skipped_entirely():
    assert windows_with_detections(rows([(0, 0), (5, 0)]), fps=30.0) == []
    assert windows_with_detections([], fps=30.0) == []


def test_ignores_slices_the_coarse_pass_never_scored():
    unscored = [{"dim_t": 0, "detection_count": None}, {"dim_t": 30, "detection_count": 1}]
    assert windows_with_detections(unscored, fps=30.0) == [Window(1.0, 1.0)]


def test_visits_only_the_padded_windows():
    visit = _frames_to_visit([Window(10.0, 10.0)], fps=30.0, duration=100.0, pad=2.0, step=5)
    assert min(visit) >= 8 * 30 - 5 and max(visit) <= 12 * 30 + 5
    # A whole 100 s recording at this step would be 600 frames; this is a small slice of it.
    assert len(visit) < 40


def test_padding_cannot_run_past_the_recording():
    visit = _frames_to_visit([Window(0.0, 1.0)], fps=30.0, duration=2.0, pad=5.0, step=10)
    assert min(visit) >= 0
    assert max(visit) <= 2 * 30


def test_the_step_follows_the_refined_rate():
    dense = _frames_to_visit([Window(0.0, 10.0)], fps=30.0, duration=20.0, pad=0.0, step=2)
    sparse = _frames_to_visit([Window(0.0, 10.0)], fps=30.0, duration=20.0, pad=0.0, step=15)
    assert len(dense) > 5 * len(sparse)


def test_windows_are_clamped_to_the_recording():
    assert Window(5.0, 9.0).padded(pad=10.0, limit=8.0) == Window(0.0, 8.0)


def _sighting(taxon="beroe", confidence=0.9, second=12.5, recording="dive.mp4", crop=b"\xff\xd8\xff"):
    from pixel_patrol_deepsea.refine import Sighting
    return Sighting(recording=recording, second=second, frame=int(second * 30),
                    slice_t=int(second) * 30, taxon=taxon, confidence=confidence,
                    box=(1, 2, 3, 4), crop=crop)


def _at(second, taxon="beroe", box=(10, 10, 30, 30), confidence=0.5, recording="dive.mp4"):
    from pixel_patrol_deepsea.refine import Sighting
    return Sighting(recording=recording, second=second, frame=int(second * 30),
                    slice_t=int(second) * 30, taxon=taxon, confidence=confidence, box=box)


def test_one_animal_seen_many_times_is_one_track():
    from pixel_patrol_deepsea.refine import track_sightings

    drifting = [_at(i * 0.25, box=(10 + i, 10, 30 + i, 30)) for i in range(8)]
    tracks = track_sightings(drifting)
    assert len(tracks) == 1
    assert tracks[0].frames == 8
    assert tracks[0].seconds == pytest.approx(1.75)


def test_two_animals_in_the_same_frame_are_two_tracks():
    from pixel_patrol_deepsea.refine import track_sightings

    pair = [_at(0.0, box=(10, 10, 30, 30)), _at(0.0, box=(200, 200, 220, 220)),
            _at(0.25, box=(11, 10, 31, 30)), _at(0.25, box=(201, 200, 221, 220))]
    tracks = track_sightings(pair)
    assert len(tracks) == 2
    assert all(t.frames == 2 for t in tracks)


def test_the_same_place_at_a_different_species_is_a_different_track():
    from pixel_patrol_deepsea.refine import track_sightings

    swapped = [_at(0.0, taxon="beroe"), _at(0.25, taxon="shrimp")]
    assert len(track_sightings(swapped)) == 2


def test_an_animal_that_leaves_and_comes_back_later_is_counted_twice():
    # Without appearance matching there is no honest way to say it is the same one,
    # and splitting one animal in two is the safer error than inventing one.
    from pixel_patrol_deepsea.refine import track_sightings

    apart = [_at(0.0), _at(0.25), _at(9.0), _at(9.25)]
    assert len(track_sightings(apart)) == 2


def test_tracks_do_not_cross_between_recordings():
    from pixel_patrol_deepsea.refine import track_sightings

    two = [_at(0.0, recording="a.mp4"), _at(0.25, recording="b.mp4")]
    assert len(track_sightings(two)) == 2


def test_a_track_reports_its_most_confident_look():
    from pixel_patrol_deepsea.refine import track_sightings

    run = [_at(0.0, confidence=0.3), _at(0.25, confidence=0.91), _at(0.5, confidence=0.4)]
    [track] = track_sightings(run)
    assert track.best.confidence == pytest.approx(0.91)


def test_two_equally_bad_candidates_do_not_crash_the_tracker():
    # max() over (score, track) pairs falls through to comparing the tracks when the
    # scores tie, which they do constantly on real data: two detections that overlap
    # an open track by exactly nothing.
    from pixel_patrol_deepsea.refine import track_sightings

    far_apart = [_at(0.0, box=(0, 0, 10, 10)), _at(0.0, box=(500, 500, 510, 510)),
                 _at(0.25, box=(900, 900, 910, 910))]
    assert len(track_sightings(far_apart)) == 3


def _burst(second, x, taxon="fish", width=60, frames=3, gap=0.02):
    """One look: a burst of consecutive frames, the way the detector reads them."""
    from pixel_patrol_deepsea.refine import Sighting
    return [Sighting("d.mp4", second + i * gap, 0, 0, taxon, 0.7,
                     (x + i, 100, x + width + i, 140)) for i in range(frames)]


def test_the_gap_follows_how_often_the_detector_looked():
    """The interval between looks, not between frames. A burst is a fiftieth of a
    second apart, and taking the median of every step gave 1.5 s when the number
    that mattered was five."""
    from pixel_patrol_deepsea.refine import _gap_for

    looked_every_5s = [s for n in range(6) for s in _burst(100 + 5 * n, 200)]
    assert _gap_for(looked_every_5s) == pytest.approx(12.5)

    looked_every_frame = _burst(100, 200, frames=20, gap=0.1)
    assert _gap_for(looked_every_frame) == pytest.approx(1.5)


def test_one_fish_looked_at_every_five_seconds_is_one_fish():
    # The failure this fixes: a fish followed for half a minute came out as eight
    # animals, because it was judged missing between looks nobody took.
    from pixel_patrol_deepsea.refine import track_sightings

    drifting = [s for n in range(7) for s in _burst(272 + 5 * n, 200 + 12 * n)]
    tracks = track_sightings(drifting)
    assert len(tracks) == 1
    assert tracks[0].frames == 21


def test_two_fish_in_different_places_stay_two():
    from pixel_patrol_deepsea.refine import track_sightings

    here = [s for n in range(4) for s in _burst(100 + 5 * n, 100)]
    there = [s for n in range(4) for s in _burst(100 + 5 * n, 500)]
    assert len(track_sightings(here + there)) == 2


def test_an_animal_unseen_for_long_enough_is_a_new_animal():
    # However sparse the sampling, half a minute is the ceiling.
    from pixel_patrol_deepsea.refine import track_sightings

    apart = _burst(0.0, 200) + _burst(90.0, 200)
    assert len(track_sightings(apart)) == 2


def test_a_box_that_moved_about_its_own_size_is_still_the_same_animal():
    from pixel_patrol_deepsea.refine import TRACK_IOU, _agreement

    same = _agreement((100, 100, 160, 140), (150, 100, 210, 140), TRACK_IOU)
    assert same > 0

    elsewhere = _agreement((100, 100, 160, 140), (500, 300, 560, 340), TRACK_IOU)
    assert elsewhere == 0


def test_overlap_still_beats_proximity_when_boxes_do_touch():
    from pixel_patrol_deepsea.refine import TRACK_IOU, _agreement

    overlapping = _agreement((100, 100, 160, 140), (105, 100, 165, 140), TRACK_IOU)
    nearby = _agreement((100, 100, 160, 140), (170, 100, 230, 140), TRACK_IOU)
    assert overlapping > nearby


def _at_edge(second, frames=4, taxon="fish"):
    """A box wedged into the bottom-right corner, the way the arm sits."""
    from pixel_patrol_deepsea.refine import Sighting
    return [Sighting("d.mp4", second + i * 2.0, 0, 0, taxon, 0.6,
                     (560, 150, 640, 360)) for i in range(frames)]


def test_a_detection_wedged_against_the_frame_edge_for_ages_is_the_vehicle():
    """A claw was detected as a fish for two minutes straight and was the single
    largest animal on the page."""
    from pixel_patrol_deepsea.refine import looks_like_vehicle, track_sightings

    [track] = track_sightings(_at_edge(0.0, frames=8))
    assert track.seconds >= 10
    assert looks_like_vehicle(track, 640, 360)


def test_an_animal_crossing_the_view_is_not_the_vehicle():
    # It touches an edge going in and coming out, not for half a minute.
    from pixel_patrol_deepsea.refine import looks_like_vehicle, track_sightings

    # Moving 40 px a look with a 60 px box: within the drift the tracker allows,
    # so it stays one animal, and it is only against an edge at the start.
    crossing = [s for n in range(8) for s in _burst(n * 5.0, 2 + 40 * n)]
    [track] = track_sightings(crossing)
    assert track.seconds >= 10
    assert not looks_like_vehicle(track, 640, 360)


def test_a_brief_look_at_the_edge_is_left_alone():
    # Ten seconds is the floor: a glimpse at the frame boundary proves nothing.
    from pixel_patrol_deepsea.refine import looks_like_vehicle, track_sightings

    [track] = track_sightings(_at_edge(0.0, frames=2))
    assert track.seconds < 10
    assert not looks_like_vehicle(track, 640, 360)


def test_it_says_nothing_about_a_sponge_in_the_middle_of_the_frame():
    """Geometry cannot reach a misnaming. A sessile animal called a fish sits in
    open frame and looks exactly like a fish would to this rule."""
    from pixel_patrol_deepsea.refine import looks_like_vehicle, track_sightings

    sessile = [s for n in range(8) for s in _burst(n * 5.0, 300, width=50)]
    [track] = track_sightings(sessile)
    assert track.seconds >= 10
    assert not looks_like_vehicle(track, 640, 360)


def test_a_label_that_names_a_rock_or_a_sampler_is_not_an_animal():
    """The 499-class model has words for the things a fish detector called fish.
    Where it says so, believe it - that beats any geometric rule guessing."""
    from pixel_patrol_deepsea.refine import is_an_animal

    for label in ("equipment", "Equipment", "geologic", "marine snow", "sand",
                  "Suction Sampler", "trash", "bone", "molt", "eggcase"):
        assert not is_an_animal(label), label


def test_a_sponge_is_an_animal_even_though_it_does_not_move():
    from pixel_patrol_deepsea.refine import is_an_animal

    for label in ("Porifera", "Demospongiae", "Actinopterygii", "Crinoidea",
                  "Ceriantharia", "Anguilliformes", "fish"):
        assert is_an_animal(label), label
