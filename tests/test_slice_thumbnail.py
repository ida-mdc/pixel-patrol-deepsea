import io

import numpy as np
import pytest
from PIL import Image

from pixel_patrol_base.core.record import record_from
from pixel_patrol_deepsea.slice_thumbnail_processor import (
    SLICE_THUMBNAIL,
    THUMBNAIL_WIDTH,
    SliceThumbnailProcessor,
)


@pytest.fixture
def proc() -> SliceThumbnailProcessor:
    return SliceThumbnailProcessor()


def _frames(count=5, height=64, width=96, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (count, height, width, 3), dtype=np.uint8)


def _encode(proc, rows):
    return proc.get_aggregation(SLICE_THUMBNAIL)(rows, ())


def _decode(blob: bytes) -> Image.Image:
    return Image.open(io.BytesIO(blob))


def _row(proc, data, dim_order, channel=None):
    row = proc.run_chunk(record_from(data, {"dim_order": dim_order}))
    row["dim_c"] = channel
    return row


def test_writes_a_jpeg_at_the_target_width(proc):
    image = _decode(_encode(proc, [_row(proc, _frames(width=360, height=240), "TYXC")]))
    assert image.format == "JPEG"
    assert image.width == THUMBNAIL_WIDTH


def test_never_upscales_footage_smaller_than_the_target(proc):
    image = _decode(_encode(proc, [_row(proc, _frames(width=96, height=64), "TYXC")]))
    assert image.width == 96


def test_keeps_the_aspect_ratio(proc):
    image = _decode(_encode(proc, [_row(proc, _frames(height=240, width=720), "TYXC")]))
    assert abs(image.height - round(THUMBNAIL_WIDTH * 240 / 720)) <= 1


def test_channel_split_slices_recombine_into_colour(proc):
    """The pipeline hands each channel to its own leaf block; encoding per leaf
    would give three grey stills of the same moment instead of one colour one."""
    rgb = _frames(4, 64, 96)
    red = np.zeros_like(rgb[:, :, :, 0]); red[:] = 200
    blue = np.zeros_like(red)
    rows = [_row(proc, red, "TYX", 0), _row(proc, blue, "TYX", 1), _row(proc, blue, "TYX", 2)]
    pixel = _decode(_encode(proc, rows)).convert("RGB").getpixel((10, 10))
    assert pixel[0] > 150 and pixel[1] < 80 and pixel[2] < 80


def test_channel_order_follows_the_channel_index(proc):
    dark, bright = np.zeros((2, 32, 48), np.uint8), np.full((2, 32, 48), 240, np.uint8)
    rows = [_row(proc, dark, "TYX", 0), _row(proc, bright, "TYX", 1), _row(proc, dark, "TYX", 2)]
    pixel = _decode(_encode(proc, list(reversed(rows)))).convert("RGB").getpixel((10, 10))
    assert pixel[1] > pixel[0] and pixel[1] > pixel[2]


def test_stays_small_enough_to_store_per_slice(proc):
    # Random noise is the worst case for JPEG; real footage compresses far better.
    blob = _encode(proc, [_row(proc, _frames(1, 240, 360), "TYXC")])
    assert len(blob) < 12_000


def test_finds_the_spatial_axes_wherever_they_sit(proc):
    moved = np.moveaxis(_frames(), 0, 2)          # Y, X, T, C
    assert _encode(proc, [_row(proc, moved, "YXTC")])


def test_handles_a_single_grey_frame(proc):
    grey = np.full((48, 64), 120, np.uint8)
    assert _decode(_encode(proc, [_row(proc, grey, "YX")])).mode == "RGB"


def test_a_slice_too_small_to_show_yields_no_row(proc):
    assert proc.run_chunk(record_from(np.zeros((4, 4), np.uint8), {"dim_order": "YX"})) == {}


def test_nothing_to_encode_yields_nothing(proc):
    assert _encode(proc, [{"dim_c": 0}]) is None


def test_colour_survives_a_second_aggregation_pass(proc):
    """Rollup aggregates level by level, so the second pass is handed the
    single-channel stills the first pass already encoded, not raw planes. Missing
    this turned every still in a six-expedition report grey."""
    import io as _io
    from pixel_patrol_deepsea.slice_thumbnail_processor import _as_rgb8, encode_thumbnail

    encoded = [
        {"dim_c": channel, SLICE_THUMBNAIL: encode_thumbnail(_as_rgb8(np.full((72, 128), value, np.uint8)))}
        for channel, value in enumerate((30, 200, 60))
    ]
    pixel = _decode(_encode(proc, encoded)).convert("RGB").getpixel((10, 10))
    assert pixel[1] > 150 and pixel[0] < 80


def test_an_already_colour_still_is_not_flattened(proc):
    from pixel_patrol_deepsea.slice_thumbnail_processor import encode_thumbnail

    colour = np.dstack([np.full((72, 128), v, np.uint8) for v in (200, 30, 30)])
    rows = [{"dim_c": None, SLICE_THUMBNAIL: encode_thumbnail(colour)}]
    pixel = _decode(_encode(proc, rows)).convert("RGB").getpixel((10, 10))
    assert pixel[0] > 150 and pixel[1] < 80


def test_single_channel_leaves_of_colour_footage_stack(proc):
    """The pipeline hands one channel of a TYXC video as (Y, X, 1), not (Y, X).
    Keeping that axis is what made a whole report grey."""
    frames = _frames(4, 64, 96)
    rows = []
    for channel, level in enumerate((30, 200, 60)):
        plane = np.full_like(frames[:, :, :, :1], level)
        rows.append(_row(proc, plane, "TYXC", channel))
    assert all(np.asarray(r[SLICE_THUMBNAIL]).ndim == 2 for r in rows)
    pixel = _decode(_encode(proc, rows)).convert("RGB").getpixel((10, 10))
    assert pixel[1] > 150 and pixel[0] < 80
