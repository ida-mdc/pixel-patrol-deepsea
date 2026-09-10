"""Reading a pixel-patrol report: what is in it, and what it found.

Only the reading. One page presents a collection - see `catalogue_page` - and this
is what it asks each report for: how much footage, how many recordings, which
species and the best look at each. Keeping them apart is what stopped there being
three pages that each half-answered the same question.
"""

import argparse
import base64
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

@dataclass
class Taxon:
    """One species the detector named, and the best look the report has at it."""
    best: float
    crop: Optional[bytes] = None

    @property
    def thumbnail(self) -> str:
        """Inline, so the page is one file you can mail to someone."""
        if not self.crop:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(self.crop).decode()

@dataclass
class ReportSummary:
    """What one parquet contains, read without opening the viewer."""
    path: Path
    title: str
    description: str
    recordings: int
    seconds: float
    slices: int
    stills: int
    scored: int
    with_animals: int
    created: str = ""
    names: List[str] = field(default_factory=list)
    taxa: Dict[str, "Taxon"] = field(default_factory=dict)
    # Where and when, when the archive published enough to say. This is what makes
    # two reports comparable: the same canyon ten years apart is a question you can
    # only ask of footage that knows where it was.
    placed: Optional["Placed"] = None

    @property
    def size(self) -> str:
        megabytes = self.path.stat().st_size / 1e6
        return f"{megabytes:.0f} MB" if megabytes >= 10 else f"{megabytes:.1f} MB"

    @property
    def modified(self) -> str:
        return datetime.fromtimestamp(self.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _degrees(value: float, hemispheres: str) -> str:
    """A coordinate as degrees and a hemisphere, which is how one is read aloud."""
    return f"{abs(value):.3f}\u00b0{hemispheres[0] if value >= 0 else hemispheres[1]}"


def _longitude_bounds(values: Sequence[float]) -> Tuple[float, float, float]:
    """West edge, east edge and width of a set of longitudes, on the circle.

    The smallest and largest number are not the edges once the dateline is
    involved. EX2503 worked at 166.67W, 179.87E and 178.50W; the numbers run from
    -178.50 to 179.87, suggesting an extent of 1.6 degrees, and the footage
    actually spans 13.5 - eastward from 179.87E, past the dateline, to 166.67W.

    What is unambiguous is the *gap*: the widest stretch of longitude with no
    footage in it. Everything else is the extent, and its edges are the values
    either side of that gap.
    """
    if not values:
        return 0.0, 0.0, 0.0
    ordered = sorted(values)
    gaps = [(ordered[i + 1] - ordered[i], i) for i in range(len(ordered) - 1)]
    gaps.append(((ordered[0] + 360.0) - ordered[-1], len(ordered) - 1))
    widest, at = max(gaps)
    west = ordered[(at + 1) % len(ordered)]
    east = ordered[at]
    return west, east, 360.0 - widest


def _mean_longitude(values: Sequence[float]) -> float:
    """The average of longitudes, which is not the average of their numbers.

    A cruise that worked at 179.9E and at 166.7W averages arithmetically to about
    45W - the middle of the Atlantic, a quarter of the way round the world from
    anywhere it was. Averaging the directions instead and reading the angle back
    gives a longitude that is actually between them.
    """
    import math

    if not values:
        return 0.0
    east = sum(math.sin(math.radians(v)) for v in values)
    north = sum(math.cos(math.radians(v)) for v in values)
    return math.degrees(math.atan2(east, north))


@dataclass
class Placed:
    """Where and when a report's footage was taken."""
    latitude: float
    longitude: float
    source: str = ""
    shallowest_m: Optional[float] = None
    deepest_m: Optional[float] = None
    first_at: str = ""
    last_at: str = ""
    north: float = 0.0
    south: float = 0.0
    east: float = 0.0
    west: float = 0.0

    @property
    def depth(self) -> str:
        if self.deepest_m is None:
            return ""
        if self.shallowest_m is not None and self.deepest_m - self.shallowest_m > 50:
            return f"{self.shallowest_m:,.0f}-{self.deepest_m:,.0f} m"
        return f"{self.deepest_m:,.0f} m"

    # Beyond this much spread, a cruise has no single position and saying one would
    # be inventing a place it never was. EX2503 worked three sites more than a
    # thousand kilometres apart; its centroid is open ocean.
    ONE_PLACE_DEGREES = 0.25

    @property
    def position(self) -> str:
        """Where the footage was taken, as a point or as an extent.

        A point when the footage came from one place, and the extent when it did
        not - a cruise that worked three sites a thousand kilometres apart has a
        centroid in open water that it never visited.
        """
        if max(self.latitude_span, self.longitude_span) <= self.ONE_PLACE_DEGREES:
            return f"{_degrees(self.latitude, 'NS')} {_degrees(self.longitude, 'EW')}"
        return (f"{_degrees(self.south, 'NS')} to {_degrees(self.north, 'NS')}, "
                f"{_degrees(self.west, 'EW')} to {_degrees(self.east, 'EW')}")

    @property
    def latitude_span(self) -> float:
        return abs(self.north - self.south)

    # East-west extent on the circle, computed where the longitudes are known and
    # carried here because the edges alone cannot say it - see _longitude_bounds.
    longitude_span: float = 0.0

    @property
    def when(self) -> str:
        first, last = self.first_at[:10], self.last_at[:10]
        return first if first == last else f"{first} to {last}"


def _placed(slices) -> Optional["Placed"]:
    """The report's position, depth range and time span, from its own rows.

    The mean position rather than a bounding box: one dive is a few hundred metres
    of seafloor and a map wants a point for it. The depth *range* though, because
    a dive that went from 200 m to 4859 m did two different things.
    """
    import polars as pl

    if "latitude" not in slices.columns or not len(slices):
        return None
    placed = slices.filter(pl.col("latitude").is_not_null())
    if not len(placed):
        return None

    def span(column):
        if column not in placed.columns:
            return None, None
        values = placed[column].drop_nulls()
        return (float(values.min()), float(values.max())) if len(values) else (None, None)

    shallowest, deepest = span("depth_m")
    stamps = (sorted(placed["recorded_at"].drop_nulls().to_list())
              if "recorded_at" in placed.columns else [])
    south, north = span("latitude")
    longitudes = placed["longitude"].drop_nulls().to_list()
    west, east, width = _longitude_bounds(longitudes)
    return Placed(
        latitude=float(placed["latitude"].mean()),
        longitude=_mean_longitude(longitudes),
        source=(placed["location_source"].drop_nulls().first()
                if "location_source" in placed.columns else "") or "",
        shallowest_m=shallowest, deepest_m=deepest,
        first_at=stamps[0] if stamps else "", last_at=stamps[-1] if stamps else "",
        north=north or 0.0, south=south or 0.0,
        east=east, west=west, longitude_span=width)


def slice_rows(table):
    """The per-slice rows of a report, whichever axes it was sliced on.

    `dim_c IS NULL` picks the row that spans the channels - but only where the
    channel axis was split in the first place. Ask for the colour axis whole and
    there is no dim_c column, and naming it raises rather than returning nothing.
    """
    import polars as pl

    if "dim_t" not in table.columns:
        return table.head(0)
    wanted = (pl.col("obs_level") == 1) & pl.col("dim_t").is_not_null()
    if "dim_c" in table.columns:
        wanted = wanted & pl.col("dim_c").is_null()
    return table.filter(wanted)


def summarise(path: Path) -> Optional[ReportSummary]:
    import polars as pl

    try:
        table = pl.read_parquet(path)
    except Exception:
        return None
    if "obs_level" not in table.columns:
        return None
    slices = slice_rows(table)
    metadata = _parquet_metadata(path)
    summary = ReportSummary(
        path=path,
        title=metadata.get("name") or path.stem,
        description=metadata.get("description") or "",
        created=metadata.get("created", ""),
        recordings=table["name"].n_unique() if "name" in table.columns else 0,
        names=sorted(table["name"].unique().to_list()) if "name" in table.columns else [],
        seconds=_footage_seconds(slices),
        slices=len(slices),
        stills=int(slices["slice_thumbnail"].is_not_null().sum()) if "slice_thumbnail" in slices.columns else 0,
        scored=int(slices["detection_count"].is_not_null().sum()) if "detection_count" in slices.columns else 0,
        with_animals=0,
    )
    summary.placed = _placed(slices)
    if "detection_count" in slices.columns:
        seen = slices.filter(pl.col("detection_count") > 0)
        summary.with_animals = len(seen)
        if len(seen) and "detection_top_class" in seen.columns:
            summary.taxa = _best_look_per_taxon(seen)
    return summary


def _best_look_per_taxon(seen) -> Dict[str, Taxon]:
    """The most confident slice of each species, with its crop if one was stored.

    A list of names tells you what is in a report; the picture tells you whether to
    open it. The crops are a few hundred bytes each, so carrying them costs nothing.
    """
    import polars as pl

    has_crop = "detection_crop" in seen.columns
    ordered = seen.sort("detection_confidence", descending=True, nulls_last=True)
    out: Dict[str, Taxon] = {}
    for row in ordered.iter_rows(named=True):
        name = row.get("detection_top_class")
        if not name or name in out:
            continue
        out[name] = Taxon(best=float(row.get("detection_confidence") or 0),
                          crop=row.get("detection_crop") if has_crop else None)
    return out


DEFAULT_SLICE_FRAMES = 30.0     # only when the report holds a single slice


def _footage_seconds(slices) -> float:
    """Total footage, from each recording's own slice size and frame rate.

    Both have to come from the data. A report can hold a 59.94 fps sequence beside
    a 30 fps one, and it can hold a collection sliced at 30 frames beside a dive
    sliced at 50 - assuming either made an eight-hour dive read as four hours and
    fifty minutes, which is the wrong number in the one place the page exists to
    state it. The slice size is the gap between consecutive dim_t values.
    """
    import polars as pl

    if not len(slices) or "fps" not in slices.columns or "name" not in slices.columns:
        return len(slices) * DEFAULT_SLICE_FRAMES / 30.0
    total = 0.0
    for (name,), rows in slices.group_by("name"):
        fps = float(rows["fps"][0] or 30.0)
        total += len(rows) * _slice_frames(rows) / fps
    return total


def _slice_frames(rows) -> float:
    """Frames per slice, read off the spacing of the time axis."""
    import polars as pl

    if "dim_t" not in rows.columns or len(rows) < 2:
        return DEFAULT_SLICE_FRAMES
    steps = rows["dim_t"].sort().diff().drop_nulls()
    step = float(steps.median()) if len(steps) else 0.0
    return step if step > 0 else DEFAULT_SLICE_FRAMES


def _parquet_metadata(path: Path) -> Dict[str, str]:
    """What pixel-patrol writes into the parquet footer, under `pp_` keys."""
    try:
        import pyarrow.parquet as pq
        raw = pq.read_schema(path).metadata or {}
    except Exception:
        return {}
    out = {}
    for key, value in raw.items():
        try:
            out[key.decode()] = value.decode()
        except Exception:
            continue
    return {
        "name": out.get("pp_project_name", ""),
        "description": out.get("pp_description", ""),
        "created": out.get("pp_created_at", "")[:16].replace("T", " "),
        "loader": out.get("pp_loader", ""),
    }


def animals_in_sightings(rows):
    """Detections linked into one entry per animal.

    The distinction is the whole difference between "we found 106 jellyfish" and
    "we watched one jellyfish for fifteen seconds", and the number people read off
    a summary page is the first one unless it is made to be the second.
    """
    from pixel_patrol_deepsea.refine import Sighting, track_sightings

    sightings = [Sighting(recording=r["name"], second=r["second"], frame=r["frame"],
                          slice_t=r["dim_t"], taxon=r["taxon"], confidence=r["confidence"],
                          box=(r["x1"], r["y1"], r["x2"], r["y2"]), crop=r.get("crop"))
                 for r in rows.iter_rows(named=True)]
    return track_sightings(sightings)


def read_sources(directory: Path) -> Dict[str, dict]:
    """Where each clip came from, as written by whatever fetched it.

    A tile in the gallery is worth nothing to someone who cannot say which archive
    it came out of, and the parquet has no room for a citation.
    """
    path = directory / "sources.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def collect(directory: Path) -> List[ReportSummary]:
    summaries = []
    for path in sorted(directory.rglob("*.parquet")):
        summary = summarise(path)
        if summary:
            summaries.append(summary)
    return summaries


