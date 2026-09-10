"""Reports things moving in front of a camera that is holding still.

This is the only measure here that can find an animal nobody has a class for. A
detector answers "is this one of my 22 categories"; background subtraction answers
"is something here that is not usually here", which is true of a fish, of a squid,
and of a species nobody has described. It costs no model and no training data.

It works only while the camera is still, and measures that for itself rather than
being told - see motion_numpy_metrics. On a dive tape that means it reports during
the hovers, which is where anyone hunting animals would have looked anyway.
"""

from typing import Dict, Tuple

import numpy as np

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec
from pixel_patrol_base.plugins.processors.raster_processor import (
    RasterMetricSpec,
    _weighted_mean_agg,
)
from pixel_patrol_deepsea.motion_numpy_metrics import moving_objects

CAMERA_SPEED = "camera_speed"
MOVER_COUNT = "moving_object_count"
MOVER_AREA  = "moving_object_area"


class MovingObjectProcessor:
    """Counts what moves while the background does not, over each slice."""

    NAME        = "raster-motion"
    DESCRIPTION = "Finds objects moving independently of the camera, with no model and no class list, so it can flag an animal no detector was trained on. The camera's own motion is estimated by phase correlation and removed before anything is subtracted; where it is too fast to register, or the scene too three-dimensional for one shift to describe, that is reported rather than guessed at."
    CHUNK_KIND  = ChunkKind.LEAF
    INPUT       = RecordSpec(axes={"X", "Y", "T"}, kinds={"intensity"}, capabilities={"spatial-2d"})
    OUTPUT      = "features"

    METRICS: Tuple[RasterMetricSpec, ...] = (
        RasterMetricSpec(
            name=CAMERA_SPEED, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description="How fast the scene slid across the frame, in pixels per second, estimated by phase correlation. This is the camera's own motion rather than anything in front of it: near zero while it holds station on a subject, hundreds while it transits. On a NOAA dive tape the median is 43 px/s.",
        ),
        RasterMetricSpec(
            name=MOVER_COUNT, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description="How many separate things moved independently of the camera, after its own motion was registered away. Needs no trained model, so unlike a detector it is not restricted to species someone has already labelled - it is the only measure here that can flag an animal nobody has described. Zero where the alignment could not be trusted.",
        ),
        RasterMetricSpec(
            name=MOVER_AREA, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description="Share of the frame taken by the largest of those movers. Separates a fish crossing the view from a speck of marine snow at the same count.",
        ),
    )
    OUTPUT_SCHEMA = {m.name: m.data_type for m in METRICS}
    OUTPUT_SCHEMA_DESCRIPTIONS = {m.name: m.description for m in METRICS}

    def run_chunk(self, record: Record) -> Dict:
        frames = _frames_of(record)
        if frames is None:
            return {}
        speed, count, area, _box = moving_objects(frames, _seconds_of(record))
        if not np.isfinite(speed):
            return {}
        return {CAMERA_SPEED: speed, MOVER_COUNT: count, MOVER_AREA: area}

    def get_aggregation(self, name: str):
        spec = next((s for s in self.METRICS if s.name == name), None)
        if spec is None:
            return None
        return lambda rows, g_dims: spec.aggregate_rows(spec, rows)


def _seconds_of(record: Record) -> float:
    """How long this slice covers, so a speed can be reported per second."""
    frames = getattr(record, "shape", None)
    fps = float(getattr(record, "fps", 0) or 0)
    order = record.dim_order
    if fps > 0 and frames and "T" in order:
        return frames[order.index("T")] / fps
    return 1.0


def _frames_of(record: Record):
    """The slice as (T, Y, X), or None when there is no time axis to work with.

    A single-channel leaf of TYXC video arrives with a trailing axis of length one;
    keeping it would make every frame its own background and nothing would ever
    differ from anything.
    """
    chunk = record.data.compute() if hasattr(record.data, "compute") else np.asarray(record.data)
    order = record.dim_order
    if "T" not in order or "Y" not in order or "X" not in order:
        return None
    wanted = [order.index(axis) for axis in "TYX"]
    rest = [i for i in range(chunk.ndim) if i not in wanted]
    moved = chunk.transpose(wanted + rest)
    for _ in rest:
        moved = moved[..., 0] if moved.shape[-1] == 1 else moved.max(axis=-1)
    return moved if moved.ndim == 3 and moved.shape[0] >= 3 else None
