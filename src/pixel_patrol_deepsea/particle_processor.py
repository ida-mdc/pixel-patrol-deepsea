"""Counts small bright particles per slice.

In midwater footage those particles are largely the animals - measured against
MBARI's annotated DeepSea-MOT sequence at 0.85 precision, 0.39 recall. Close to a
lit seafloor the same number means marine snow instead, and inverts: on a NOAA
dive tape the highest-scoring frame was empty water and the lowest was a coral
colony filling the view. See particle_numpy_metrics for the measurements.

Do not read this as an animal detector. It is a particulate-load measure that
happens to coincide with animals in one kind of scene.
"""

from typing import Any, Dict, Tuple

import numpy as np

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec
from pixel_patrol_deepsea.particle_numpy_metrics import particles_per_frame
from pixel_patrol_base.plugins.processors.raster_processor import (
    RasterMetricSpec,
    _weighted_mean_agg,
)

PARTICLE_COUNT = "bright_particle_count"
PARTICLE_AREA  = "bright_particle_area_fraction"

FRAMES_SAMPLED = 4


class BrightParticleProcessor:
    """Counts small bright particles over the 2D spatial extent of each slice."""

    NAME        = "raster-particles"
    DESCRIPTION = "Counts small bright particles in each slice using a band-pass filter and a minimum area, and reports how much of the frame they cover. In midwater footage these are largely animals; near a lit seafloor they are marine snow."
    CHUNK_KIND  = ChunkKind.LEAF
    INPUT       = RecordSpec(axes={"X", "Y"}, kinds={"intensity"}, capabilities={"spatial-2d"})
    OUTPUT      = "features"

    METRICS: Tuple[RasterMetricSpec, ...] = (
        RasterMetricSpec(
            name=PARTICLE_COUNT, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description="Mean number of small bright particles per frame, above a size floor that excludes single-pixel noise. In midwater footage these are largely animals (0.85 precision, 0.39 recall against annotated MBARI footage); near a lit seafloor they are marine snow and the measure inverts, scoring empty water above a coral colony.",
        ),
        RasterMetricSpec(
            name=PARTICLE_AREA, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description="Share of the frame those particles cover. Separates a dense cloud of snow from a sparse scattering at a similar count.",
        ),
    )
    OUTPUT_SCHEMA = {m.name: m.data_type for m in METRICS}
    OUTPUT_SCHEMA_DESCRIPTIONS = {m.name: m.description for m in METRICS}

    def run_chunk(self, record: Record) -> Dict:
        chunk = record.data.compute() if hasattr(record.data, "compute") else np.asarray(record.data)
        dim_order = record.dim_order
        y_ax, x_ax = dim_order.index("Y"), dim_order.index("X")
        if y_ax != len(dim_order) - 2 or x_ax != len(dim_order) - 1:
            other = [i for i in range(chunk.ndim) if i not in (y_ax, x_ax)]
            chunk = chunk.transpose(other + [y_ax, x_ax])
        if chunk.shape[-1] < 8 or chunk.shape[-2] < 8:
            return {}
        count, area = particles_per_frame(chunk, FRAMES_SAMPLED)
        if not np.isfinite(count):
            return {}
        return {PARTICLE_COUNT: count, PARTICLE_AREA: area}

    def get_aggregation(self, name: str):
        spec = next((s for s in self.METRICS if s.name == name), None)
        if spec is None:
            return None
        return lambda rows, g_dims: spec.aggregate_rows(spec, rows)
