import numpy as np
import pytest

from pixel_patrol_base.core.record import record_from
from pixel_patrol_deepsea.particle_numpy_metrics import (
    count_bright_particles,
    scale_for,
)
from pixel_patrol_deepsea.particle_processor import (
    PARTICLE_AREA,
    PARTICLE_COUNT,
    BrightParticleProcessor,
)


@pytest.fixture
def proc() -> BrightParticleProcessor:
    return BrightParticleProcessor()


def _scene(*centres, shape=(1080, 1920), radius=8.0, seed=0) -> np.ndarray:
    """Dim noisy background with bright round particles planted in it."""
    rng = np.random.default_rng(seed)
    frame = rng.normal(40, 3, shape).astype(np.float32)
    y, x = np.ogrid[:shape[0], :shape[1]]
    for cy, cx in centres:
        frame += 60 * np.exp(-(((y - cy) ** 2 + (x - cx) ** 2) / (2 * radius ** 2)))
    return frame


def test_finds_each_planted_particle(proc):
    frame = _scene((200, 300), (600, 900), (800, 1500))
    assert count_bright_particles(frame, scale_for(*frame.shape))[0] == 3


def test_empty_water_yields_nothing(proc):
    assert count_bright_particles(_scene(), scale_for(1080, 1920))[0] == 0


def test_specks_the_size_of_marine_snow_are_ignored(proc):
    # The size threshold is the whole reason this beats a plain band-pass: without
    # it, a band-pass found ~3600 particles per frame where 48 animals were annotated.
    speckled = _scene(*[(100 + 40 * i, 100 + 40 * i) for i in range(12)], radius=0.6)
    assert count_bright_particles(speckled, scale_for(1080, 1920))[0] == 0


def test_area_fraction_grows_with_particle_size(proc):
    small = count_bright_particles(_scene((500, 900), radius=6.0), scale_for(1080, 1920))[1]
    large = count_bright_particles(_scene((500, 900), radius=20.0), scale_for(1080, 1920))[1]
    assert large > small > 0


def test_reports_both_metrics_for_a_frame(proc):
    row = proc.run_chunk(record_from(_scene((300, 400), (700, 1200)), {"dim_order": "YX"}))
    assert row[PARTICLE_COUNT] == pytest.approx(2.0)
    assert 0 < row[PARTICLE_AREA] < 1


def test_finds_the_spatial_axes_wherever_they_sit(proc):
    stack = np.stack([_scene((300, 400)), _scene((300, 400), (700, 1200))])
    moved = np.moveaxis(stack, 0, 1)          # Y, T, X
    assert proc.run_chunk(record_from(moved, {"dim_order": "YTX"})) != {}


def test_a_frame_too_small_to_filter_yields_no_row(proc):
    assert proc.run_chunk(record_from(np.zeros((4, 4), np.float32), {"dim_order": "YX"})) == {}
