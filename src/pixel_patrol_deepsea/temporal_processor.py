"""Temporal raster metric processor: movement between consecutive frames along T.

Runs on leaf blocks, so it measures movement *within* one T slice. With
``--slice-size T=30`` on 30 fps video that is one movement value per second of
footage. The transition across a slice boundary is not measured (29 of every 30
pairs are, at that setting); a block of a single frame yields no value at all.
"""

from typing import Any, Dict, List, Tuple

import numpy as np

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec
from pixel_patrol_deepsea.temporal_numpy_metrics import (
    consecutive_frame_differences,
    frames_first,
    max_frame_difference,
    mean_frame_difference,
)
from pixel_patrol_base.plugins.processors.raster_processor import (
    RasterMetricSpec,
    _integer_sum_agg,
    _scalar_rows_agg,
)

FRAME_DIFFERENCE     = "frame_difference"
FRAME_DIFFERENCE_MAX = "frame_difference_max"
FRAME_PAIR_COUNT     = "frame_pair_count"


def _pair_weighted_mean_agg(spec: RasterMetricSpec, rows: List[Dict]) -> Any:
    """Mean weighted by frame-pair count, so short trailing blocks do not count double.

    Mirrors the pixel-count weighting the spatial metrics use, but the natural
    weight for a between-frames metric is the number of pairs it averaged over.
    """
    total = weight_sum = 0.0
    for row in rows:
        if spec.name not in row:
            continue
        pairs = float(row.get(FRAME_PAIR_COUNT, 0) or 0)
        value = float(row[spec.name])
        if pairs > 0 and np.isfinite(value):
            total += value * pairs
            weight_sum += pairs
    return total / weight_sum if weight_sum > 0 else None


class TemporalMetricsProcessor:
    """Measures how much the image changes from frame to frame along T."""

    NAME        = "raster-temporal"
    DESCRIPTION = "Computes frame-to-frame movement along the T axis (mean and peak absolute difference between consecutive frames) for each time slice."
    CHUNK_KIND  = ChunkKind.LEAF
    INPUT       = RecordSpec(axes={"T"}, kinds={"intensity"})
    OUTPUT      = "features"

    METRICS: Tuple[RasterMetricSpec, ...] = (
        RasterMetricSpec(
            name=FRAME_DIFFERENCE, data_type=np.float32, aggregate_rows=_pair_weighted_mean_agg,
            description="Mean absolute difference between consecutive frames, in intensity units. Exactly 0 marks a frozen or duplicated stretch. Low values mean a static camera holding on a subject; high values mean the camera or the scene is moving.",
        ),
        RasterMetricSpec(
            name=FRAME_DIFFERENCE_MAX, data_type=np.float32, aggregate_rows=_scalar_rows_agg(np.nanmax),
            description="Largest absolute difference between any consecutive frame pair in the slice. Isolates hard cuts, lighting switches, and subjects entering the frame, which the mean over a whole slice dilutes.",
        ),
        RasterMetricSpec(
            name=FRAME_PAIR_COUNT, data_type=np.uint64, aggregate_rows=_integer_sum_agg,
            description="Number of consecutive frame pairs the movement values average over. One less than the frame count per sub-slice, summed over the sub-slices merged into this row - so a 30-frame slice of 3-channel video reports 87, not 29, because each channel is a separate leaf block by default.",
        ),
    )
    OUTPUT_SCHEMA = {m.name: m.data_type for m in METRICS}
    OUTPUT_SCHEMA_DESCRIPTIONS = {m.name: m.description for m in METRICS}

    def run_chunk(self, record: Record) -> Dict:
        if "T" not in record.dim_order:
            return {}
        chunk = record.data.compute() if hasattr(record.data, "compute") else np.asarray(record.data)
        differences = consecutive_frame_differences(frames_first(chunk, record.dim_order))
        if not differences.size:
            return {}
        return {
            FRAME_DIFFERENCE:     mean_frame_difference(differences),
            FRAME_DIFFERENCE_MAX: max_frame_difference(differences),
            FRAME_PAIR_COUNT:     int(differences.size),
        }

    def get_aggregation(self, name: str):
        spec = next((s for s in self.METRICS if s.name == name), None)
        if spec is None:
            return None
        return lambda rows, g_dims: spec.aggregate_rows(spec, rows)
