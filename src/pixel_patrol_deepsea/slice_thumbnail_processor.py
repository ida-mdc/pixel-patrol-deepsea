"""A small JPEG of every time slice, so a report can show footage without the footage.

The base thumbnail processor is MEMORY-kind and writes exactly one image per file,
which is no use for reading a recording along its length. Without something per
slice the only place frames can come from is the recording itself, and seeking a
remote archive is slow enough that a gallery of tiles takes a long visible moment
to fill - 43 MB of range requests for one screen.

One JPEG per slice removes that entirely: previews come out of the parquet, work
offline, and an event spanning several slices animates by cycling the stills it
already has, at no extra cost. The price is size, which is why these are small and
JPEG rather than the raw RGBA the base sprite uses: roughly 3 KB a slice, about
11 MB per hour of footage at one slice per second.
"""

import io
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec

logger = logging.getLogger(__name__)

SLICE_THUMBNAIL = "slice_thumbnail"
THUMBNAIL_WIDTH = 128
JPEG_QUALITY    = 72


def _to_small_plane(chunk: np.ndarray, dim_order: str) -> Optional[np.ndarray]:
    """The slice's middle frame, downscaled, as a small 2-D or HxWxC uint8 plane.

    Encoding is deferred to aggregation because the pipeline usually hands each
    colour channel to a separate leaf block. Encoding here would produce three grey
    JPEGs of the same moment and the report would lose all its colour - which is
    most of what makes footage readable at a glance.

    The middle frame rather than the first: a slice that starts on a cut or a
    motion-blurred frame still gets a representative still.
    """
    if "Y" not in dim_order or "X" not in dim_order:
        return None
    y_ax, x_ax = dim_order.index("Y"), dim_order.index("X")
    c_ax = dim_order.index("C") if "C" in dim_order else None
    lead = [i for i in range(chunk.ndim) if i not in (y_ax, x_ax, c_ax)]
    moved = chunk.transpose(lead + ([c_ax] if c_ax is not None else []) + [y_ax, x_ax])
    tail = 3 if c_ax is not None else 2
    flat = moved.reshape(-1, *moved.shape[-tail:])
    if flat.shape[0] == 0:
        return None
    frame = flat[flat.shape[0] // 2]
    if c_ax is not None:
        frame = np.moveaxis(frame, 0, -1)
    if frame.ndim == 3 and frame.shape[-1] == 1:
        # A single-channel leaf of colour footage still carries its channel axis.
        # Left in place it makes the plane 3-D, the three channels never stack, and
        # every still in the report comes out grey.
        frame = frame[..., 0]
    if min(frame.shape[:2]) < 8:
        return None
    return _downscale(_as_uint8(frame))


def _downscale(frame: np.ndarray, width: int = THUMBNAIL_WIDTH) -> np.ndarray:
    """Nearest-neighbour subsample; the result is a thumbnail, not a measurement."""
    height, source_width = frame.shape[:2]
    if source_width <= width:
        return frame
    new_height = max(1, round(height * width / source_width))
    rows = (np.arange(new_height) * height // new_height).clip(0, height - 1)
    cols = (np.arange(width) * source_width // width).clip(0, source_width - 1)
    return frame[rows][:, cols]


def _encode_rows(rows: List[Dict]) -> Optional[bytes]:
    """Stack the channel planes of one slice back into colour, then encode once.

    Aggregation is applied at each level in turn, so the rows arriving here are
    sometimes the raw planes this processor produced and sometimes the single-channel
    JPEGs a lower level already encoded from them. Both have to be handled, or the
    whole report comes out grey - which is exactly what happened first time round.
    """
    planes = []
    for row in rows:
        value = row.get(SLICE_THUMBNAIL)
        if value is None:
            continue
        plane = _as_plane(value)
        if plane is not None:
            planes.append((row.get("dim_c"), plane))
    if not planes:
        return None
    ordered = [p for _, p in sorted(planes, key=lambda kv: (kv[0] is None, kv[0]))]
    frame = (np.stack(ordered[:3], axis=-1)
             if len(ordered) >= 3 and all(p.ndim == 2 for p in ordered[:3])
             else ordered[0])
    try:
        return encode_thumbnail(_as_rgb8(frame))
    except Exception as exc:
        logger.warning("slice-thumbnail: %s", exc)
        return None


def _is_monochrome(array: np.ndarray) -> bool:
    sample = array[::4, ::4].astype(np.int16)
    return bool(np.abs(sample.max(axis=-1) - sample.min(axis=-1)).mean() < 2.0)


def _as_plane(value) -> Optional[np.ndarray]:
    """Accept either a raw plane or an already-encoded still."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        from PIL import Image
        try:
            array = np.asarray(Image.open(io.BytesIO(bytes(value))).convert("RGB"))
        except Exception:
            return None
        # These are written as RGB JPEGs even when they hold one channel, so a
        # single-channel still has to be flattened back to a plane before the three
        # of them can be stacked into colour.
        return array[..., 0] if _is_monochrome(array) else array
    array = np.asarray(value)
    return array if array.ndim in (2, 3) else None


def _as_uint8(frame: np.ndarray) -> np.ndarray:
    if frame.dtype == np.uint8:
        return frame
    top = float(np.nanmax(frame)) if np.isfinite(frame).any() else 0.0
    return np.clip(frame / (top or 1.0) * 255.0, 0, 255).astype(np.uint8)


def _as_rgb8(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        frame = np.repeat(frame[:, :, None], 3, axis=2)
    elif frame.shape[-1] == 1:
        frame = np.repeat(frame, 3, axis=2)
    elif frame.shape[-1] > 3:
        frame = frame[..., :3]
    if frame.dtype != np.uint8:
        top = float(np.nanmax(frame)) if np.isfinite(frame).any() else 0.0
        frame = np.clip(frame / (top or 1.0) * 255.0, 0, 255).astype(np.uint8)
    return frame


def encode_thumbnail(frame: np.ndarray, width: int = THUMBNAIL_WIDTH) -> bytes:
    """Encode an already-small RGB frame as JPEG, resizing only if still oversized."""
    from PIL import Image

    image = Image.fromarray(frame)
    if image.width > width:
        image = image.resize((width, max(1, round(image.height * width / image.width))), Image.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


class SliceThumbnailProcessor:
    """Writes one small JPEG per slice."""

    NAME        = "slice-thumbnail"
    DESCRIPTION = "Encodes a small JPEG of every slice, so a report can preview and animate footage without fetching the recording."
    CHUNK_KIND  = ChunkKind.LEAF
    INPUT       = RecordSpec(axes={"X", "Y"}, kinds={"intensity"})
    OUTPUT      = "features"
    OUTPUT_SCHEMA: Dict[str, Any] = {SLICE_THUMBNAIL: bytes}
    OUTPUT_SCHEMA_DESCRIPTIONS: Dict[str, str] = {
        SLICE_THUMBNAIL: f"JPEG of the middle frame of this slice, {THUMBNAIL_WIDTH} px wide, in colour where the source has colour. Lets the viewer show and animate footage with no access to the recording.",
    }

    def run_chunk(self, record: Record) -> Dict:
        chunk = record.data.compute() if hasattr(record.data, "compute") else np.asarray(record.data)
        plane = _to_small_plane(chunk, record.dim_order)
        if plane is None:
            return {}
        return {SLICE_THUMBNAIL: plane}

    def get_aggregation(self, name: str):
        if name != SLICE_THUMBNAIL:
            return None
        return lambda rows, _dims: _encode_rows(rows)
