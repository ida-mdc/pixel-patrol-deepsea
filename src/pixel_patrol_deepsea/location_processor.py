"""Stamps every slice of footage with where and when it was recorded.

This is the processor that makes a map possible, and it is the one processor here
that looks at no pixels at all. What it needs is the recording's start time and
its vehicle's track, and a processor cannot get at either: it is handed a block of
pixels and the dimensions that block sits at, not the name of the file it came
from - and the position depends on the name, on the archive's dive log, and on two
HTTP requests. So `collect one`, which handles exactly one recording and does know
its URL, resolves all of that once and leaves it in the environment. See
`locations` for what the archives actually publish.

The column names are not free choices. `latitude`, `longitude` and `footprint`
are what pixel-patrol-geospatial's map widget queries for, so writing those names
means the map appears in the viewer with nothing further to do.
"""

import functools
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from pixel_patrol_base.core.contracts import ChunkKind
from pixel_patrol_base.core.record import Record
from pixel_patrol_base.core.specs import RecordSpec
from pixel_patrol_base.plugins.processors.raster_processor import (
    RasterMetricSpec,
    _weighted_mean_agg,
)
from pixel_patrol_deepsea import locations

logger = logging.getLogger(__name__)

LATITUDE        = "latitude"
LONGITUDE       = "longitude"
DEPTH           = "depth_m"
ALTITUDE        = "altitude_m"
RECORDED_AT     = "recorded_at"
FOOTPRINT       = "footprint"
LOCATION_SOURCE = "location_source"


def _earliest_agg(spec: RasterMetricSpec, rows: List[Dict]) -> Any:
    """The first moment in the group.

    A stretch of footage is described by when it starts, not by the average of its
    timestamps - and these are ISO strings in UTC, which sort as they read.
    """
    stamps = sorted(row[spec.name] for row in rows if row.get(spec.name))
    return stamps[0] if stamps else None


def _shared_agg(spec: RasterMetricSpec, rows: List[Dict]) -> Any:
    """One value that every row in the group shares anyway.

    The dive outline and the provenance of the fix belong to the recording, so
    every leaf carries the same string and any of them will do.
    """
    return next((row[spec.name] for row in rows if row.get(spec.name)), None)


@functools.lru_cache(maxsize=1)
def _staged() -> Tuple[Optional[locations.Track], Optional[Any]]:
    """The track and start time this process was given, read once."""
    track, start = locations.staged_track(), locations.staged_start()
    if track is not None:
        logger.info("slice-location: %d fixes, %s", len(track), track.source)
    return track, start


class SliceLocationProcessor:
    """Where on the seafloor, how deep, and at what moment each slice was filmed."""

    NAME        = "slice-location"
    DESCRIPTION = ("Positions each slice of footage in space and time, from the "
                   "recording's timestamp and its vehicle's published navigation. "
                   "Writes the column names pixel-patrol-geospatial's map reads.")
    CHUNK_KIND  = ChunkKind.LEAF
    INPUT       = RecordSpec(axes={"X", "Y"}, kinds={"intensity"}, capabilities={"spatial-2d"})
    OUTPUT      = "features"

    METRICS: Tuple[RasterMetricSpec, ...] = (
        RasterMetricSpec(
            name=LATITUDE, data_type=np.float64, aggregate_rows=_weighted_mean_agg,
            description="Latitude of the vehicle when this footage was recorded, in degrees north (WGS 84). Read from the archive's own navigation - see location_source for which of its records, because a per-second vehicle track and a single position for a whole dive are not the same claim.",
        ),
        RasterMetricSpec(
            name=LONGITUDE, data_type=np.float64, aggregate_rows=_weighted_mean_agg,
            description="Longitude of the vehicle when this footage was recorded, in degrees east (WGS 84).",
        ),
        RasterMetricSpec(
            name=DEPTH, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description="Depth of the vehicle below the surface in metres, positive downwards. Only a per-second navigation track carries this; a whole-dive position does not, and then it is null.",
        ),
        RasterMetricSpec(
            name=ALTITUDE, data_type=np.float32, aggregate_rows=_weighted_mean_agg,
            description="Height of the vehicle above the seafloor in metres, where the navigation reports it. Null on the descent, small once the vehicle is flying the bottom - which is a good proxy for whether the footage shows the seabed or open water.",
        ),
        RasterMetricSpec(
            name=RECORDED_AT, data_type=str, aggregate_rows=_earliest_agg,
            description="The UTC instant this footage was filmed, as ISO 8601. The recording's start comes from its filename, which is what the archive indexes and what its dive logs join against; the offset within the recording comes from the slice's own frame number.",
        ),
        RasterMetricSpec(
            name=FOOTPRINT, data_type=str, aggregate_rows=_shared_agg,
            description="The whole dive's path as a GeoJSON LineString, where the archive publishes one. Present so a map can draw the track the footage was collected along, and identical for every slice of the dive.",
        ),
        RasterMetricSpec(
            name=LOCATION_SOURCE, data_type=str, aggregate_rows=_shared_agg,
            description="Which published record the position came from. A vehicle track fixes the moment; a dive path fixes only the dive; a deployment register fixes a camera that does not move.",
        ),
    )
    OUTPUT_SCHEMA = {m.name: m.data_type for m in METRICS}
    OUTPUT_SCHEMA_DESCRIPTIONS = {m.name: m.description for m in METRICS}

    def run_chunk(self, record: Record) -> Dict:
        track, start = _staged()
        if start is None:
            return {}
        moment = start + timedelta(seconds=self._offset(record))
        row: Dict[str, Any] = {RECORDED_AT: moment.isoformat()}
        if track is None:
            return row
        fix = track.at(moment.timestamp())
        if fix is None:
            # The navigation was not running - the vehicle is on the surface, or the
            # track has a gap. A slice with a time and no position is the honest
            # answer; the nearest fix an hour away is not.
            return row
        row.update({LATITUDE: fix.latitude, LONGITUDE: fix.longitude,
                    LOCATION_SOURCE: fix.source})
        if fix.depth_m is not None:
            row[DEPTH] = fix.depth_m
        if fix.altitude_m is not None:
            row[ALTITUDE] = fix.altitude_m
        if track.footprint:
            row[FOOTPRINT] = track.footprint
        return row

    @staticmethod
    def _offset(record: Record) -> float:
        """How many seconds into the recording this slice starts.

        The slice's own frame number, not the middle of it: a slice is a couple of
        seconds long and the ROV moves metres in that time, so the start is the one
        unambiguous instant to attach a position to.
        """
        start = record.meta.get("dim_t")
        fps = record.meta.get("fps")
        if start is None or not fps:
            return 0.0
        return float(start) / float(fps)

    def get_aggregation(self, name: str):
        spec = next((s for s in self.METRICS if s.name == name), None)
        if spec is None:
            return None
        return lambda rows, g_dims: spec.aggregate_rows(spec, rows)
