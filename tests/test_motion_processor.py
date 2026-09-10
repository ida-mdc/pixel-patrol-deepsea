"""Finding things that move independently of the camera."""

import numpy as np
import pytest

from pixel_patrol_deepsea.motion_numpy_metrics import camera_shift, moving_objects


@pytest.fixture
def seabed():
    """A textured scene, wider than the frame, so it can be panned across."""
    return np.random.default_rng(1).normal(90, 40, (140, 400)).astype(np.float32)


def _view(seabed, at=50):
    return seabed[:, at:at + 160].copy()


def _with_animal(frames, speed=6):
    for i, frame in enumerate(frames):
        frame[60:80, 20 + speed * i:44 + speed * i] = 235
    return frames


def test_measures_which_way_the_camera_went(seabed):
    a, b = _view(seabed, 50), _view(seabed, 57)
    dy, dx = camera_shift(a, b)
    assert (dy, dx) == (0, 7)
    assert np.allclose(np.roll(a, -dx, axis=1)[:, 10:150], b[:, 10:150])


def test_finds_nothing_in_an_empty_still_scene(seabed):
    rng = np.random.default_rng(2)
    frames = np.stack([_view(seabed) + rng.normal(0, 2, (140, 160)) for _ in range(20)])
    speed, count, area, box = moving_objects(frames.astype(np.float32))
    assert count == 0
    assert box is None


def test_finds_an_animal_in_front_of_a_still_camera(seabed):
    frames = _with_animal(np.stack([_view(seabed) for _ in range(20)]))
    speed, count, area, box = moving_objects(frames)
    assert count == 1
    assert speed < 1
    assert box[0] < 100 and box[2] > 20


def test_finds_an_animal_while_the_camera_pans(seabed):
    # The whole point of registering the camera away: on real dive footage it is
    # translating at a median of 43 px/s and almost never holding still.
    frames = _with_animal(np.stack([_view(seabed, 50 + 2 * i) for i in range(20)]))
    speed, count, area, box = moving_objects(frames)
    assert count == 1
    assert speed > 5


def test_reports_the_camera_speed_it_measured(seabed):
    frames = np.stack([_view(seabed, 50 + 3 * i) for i in range(20)])
    speed, _, _, _ = moving_objects(frames, seconds=2.0)
    # 3 px per frame, 19 gaps over 2 s -> about 28 px/s.
    assert speed == pytest.approx(28.5, rel=0.2)


def test_says_nothing_when_the_camera_outruns_the_alignment(seabed):
    # The reported speed saturates here rather than climbing: phase correlation
    # cannot see a shift of more than half a frame, it wraps. What matters is that
    # nothing is claimed - a scene sliding past this fast has no usable background.
    wide = np.random.default_rng(3).normal(90, 40, (140, 2000)).astype(np.float32)
    frames = np.stack([wide[:, 90 * i:90 * i + 160] for i in range(12)])
    speed, count, area, box = moving_objects(frames)
    assert count == 0
    assert box is None
    assert speed > 40


def test_ignores_a_slice_too_short_to_have_a_background(seabed):
    speed, count, area, box = moving_objects(np.stack([_view(seabed)] * 2))
    assert not np.isfinite(speed)
    assert count == 0
