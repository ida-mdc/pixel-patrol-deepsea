"""Where and when a recording was made, from what its archive actually publishes.

Footage without a position is footage you cannot compare with anything. Two dives
in the same canyon ten years apart are the interesting question in this whole
collection, and the only thing that can join them is a coordinate and a clock.

None of it is in the video. The recordings carry no GPS track, no telemetry
channel and - for NOAA's ROV footage - not even a burnt-in overlay to read. What
the archives do publish, beside the video, is enough:

    the clock       the filename. `EX2107_VID_20211027T124027Z_ROVHD_Low.mp4` and
                    `CAMHDA301-20160815T000000Z.mov` both state the UTC second the
                    recording started, so a slice `n` seconds in has a real time.

    the position    one of three things, in descending order of what it can tell:

                    a 1 Hz vehicle track (`RovTrack1Hz.csv`, inside the dive's
                    ancillary-data zip) - latitude, longitude and depth every
                    second, which joined on the clock above gives every slice of
                    footage its own position on the seafloor;

                    a dive path (`*_Path.kml`) - the shape of the dive but no
                    times, so it can say where the dive was and draw its outline,
                    and nothing per moment;

                    a deployment record - for a cabled camera bolted to the
                    seafloor, one exact position and depth valid between two dates,
                    from the observatory's own asset register.

Every fix says which of those it came from, because a per-second track and a
whole-dive centroid are not the same claim and a map should not show them as if
they were.
"""

import csv
import io
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import zipfile
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from pixel_patrol_deepsea.remote_file import open_with_retry

logger = logging.getLogger(__name__)

TIMEOUT = 300
# A slice is matched to the nearest second of the vehicle track. Further than this
# and there is no fix rather than a guess: gaps in these tracks are the vehicle on
# the surface or the navigation dropping out, and interpolating across one puts a
# detection somewhere the ROV never was.
NEAREST_SECONDS = 20.0


@dataclass(frozen=True)
class Fix:
    """One position, and how well it is known."""
    latitude: float
    longitude: float
    depth_m: Optional[float] = None
    altitude_m: Optional[float] = None
    source: str = ""

    def as_row(self) -> dict:
        return {"latitude": self.latitude, "longitude": self.longitude,
                "depth_m": self.depth_m, "altitude_m": self.altitude_m,
                "location_source": self.source}


# ── the clock ─────────────────────────────────────────────────────────────────

# Both archives stamp the start of the recording into its name, and that is the
# only clock either of them offers. NOAA separates it with underscores and always
# marks it Z; OOI's newer files dropped the Z but are still UTC.
STAMP = re.compile(r"(?<![0-9])(\d{8})T(\d{6})(?:\.\d+)?Z?(?![0-9])")


def recorded_at(name: str) -> Optional[datetime]:
    """The UTC instant a recording starts, read from its name.

    Preferred over the container's `creation_time` even where that exists: the
    filename is what the archive indexes, what its dive logs join against, and
    what survives a transcode.
    """
    match = STAMP.search(Path(urllib.parse.urlparse(name).path).name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def dive_number(url: str) -> Optional[int]:
    """Which dive a recording belongs to, from the folder its archive files it under."""
    found = re.search(r"DIVE0*(\d+)", url, re.IGNORECASE)
    return int(found.group(1)) if found else None


# ── a track that can be asked about a moment ──────────────────────────────────

class Track:
    """Positions over time, answerable at any instant the source covers.

    Two quite different things are both a Track, and the difference is `constant`.
    A vehicle track has a fix per second and can only answer for the seconds it
    covers - asked about a gap it answers nothing, because interpolating across
    one puts a detection somewhere the ROV never was. A fixed camera, or a dive
    whose archive publishes only where the dive was, has a single fix that holds
    for the whole recording, and answers every moment with it.

    Carrying the distinction in the data rather than in two classes matters
    because a track is written to disk and read back in another process: what
    survives is the file, so the file has to say which kind it is.
    """

    def __init__(self, fixes: Sequence[Tuple[float, Fix]], source: str,
                 constant: bool = False, footprint: str = ""):
        ordered = sorted(fixes, key=lambda pair: pair[0])
        self.times = [when for when, _ in ordered]
        self.fixes = [fix for _, fix in ordered]
        self.source = source
        self.constant = constant
        self.footprint = footprint

    def __len__(self) -> int:
        return len(self.times)

    def at(self, when: float) -> Optional[Fix]:
        """The fix nearest `when`, or nothing if nothing was known then."""
        if not self.fixes:
            return None
        if self.constant:
            return self.fixes[0]
        index = min(bisect_left(self.times, when), len(self.times) - 1)
        best = min((max(0, index - 1), index), key=lambda i: abs(self.times[i] - when))
        return self.fixes[best] if abs(self.times[best] - when) <= NEAREST_SECONDS else None

    def to_csv(self) -> str:
        rows = io.StringIO()
        writer = csv.writer(rows)
        writer.writerow(("unixtime", "latitude", "longitude", "depth_m", "altitude_m",
                         "constant", "footprint", "source"))
        for index, (when, fix) in enumerate(zip(self.times, self.fixes)):
            writer.writerow((f"{when:.3f}", fix.latitude, fix.longitude,
                             "" if fix.depth_m is None else fix.depth_m,
                             "" if fix.altitude_m is None else fix.altitude_m,
                             1 if self.constant else 0,
                             self.footprint if index == 0 else "", fix.source))
        return rows.getvalue()

    @classmethod
    def from_csv(cls, text: str) -> "Track":
        fixes, source, constant, footprint = [], "", False, ""
        for row in csv.DictReader(io.StringIO(text)):
            source = row.get("source") or source
            constant = constant or (row.get("constant") or "").strip() == "1"
            footprint = footprint or (row.get("footprint") or "")
            fixes.append((float(row["unixtime"]), Fix(
                latitude=float(row["latitude"]), longitude=float(row["longitude"]),
                depth_m=_number(row.get("depth_m")), altitude_m=_number(row.get("altitude_m")),
                source=row.get("source") or "")))
        return cls(fixes, source, constant=constant, footprint=footprint)


def one_place(fix: Fix, source: str, when: float = 0.0, footprint: str = "") -> Track:
    """A single fix that holds for a whole recording.

    It gets a footprint even when there is no path to draw, because the map widget
    in pixel-patrol-geospatial asks for latitude, longitude *and* footprint and
    shows nothing unless all three are there. A camera bolted to the seafloor
    genuinely is a point, and a `Point` is the one geometry that reader handles
    even worse than a line - it tries to iterate a number - so the point is
    written the same way a path is, as a ring standing still. It encloses nothing
    and draws nothing; what puts the camera on the map is its latitude and
    longitude, which is the honest answer for a thing that does not move.
    """
    return Track([(when, fix)], source, constant=True,
                 footprint=footprint or _path_polygon([(fix.longitude, fix.latitude)]))


def _number(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── NOAA: the 1 Hz vehicle track ──────────────────────────────────────────────

def ancillary_member(data_url: str, dive: int, pattern: str) -> Optional[Tuple[str, str]]:
    """One file out of a dive's ancillary-data zip, without fetching the zip.

    The archive is ~16 MB and holds two copies of the same navigation - a 1 Hz one
    and a 53 MB full-rate one - plus a dive report. Everything wanted here is a few
    kilobytes to a few megabytes of that, and the zip is served over ranges, so it
    is opened where it lies and only the wanted member is read. A dive summary
    costs about five seconds this way instead of the whole download.
    """
    name = _listing_match(data_url, rf"dive0*{dive}-ancillary-data\.zip$")
    if not name:
        return None
    archive = urllib.parse.urljoin(data_url, name)
    try:
        from pixel_patrol_deepsea.remote_file import RemoteFile
        with zipfile.ZipFile(io.BufferedReader(RemoteFile(archive), 1 << 20)) as zipped:
            member = next((m for m in zipped.namelist()
                           if re.search(pattern, m, re.IGNORECASE)), None)
            if member is None:
                return None
            return Path(member).name, zipped.read(member).decode(errors="replace")
    except Exception as exc:
        logger.warning("cannot read %s out of %s: %s", pattern, archive, exc)
        return None


def noaa_rov_track(data_url: str, dive: int) -> Optional[Track]:
    """Latitude, longitude and depth every second of one ROV dive."""
    found = ancillary_member(data_url, dive, r"RovTrack1Hz\.csv$")
    if found is None:
        return None
    name, text = found
    return _read_rov_track(text, f"NOAA 1 Hz ROV track ({name})")


# What the dive's own report states about itself. Read rather than recomputed: the
# vehicle's operators decided when it was on the bottom, and that is not something
# to infer from a depth trace.
# Two generations of this report say the same things in different words. Cruises
# from 2021 on write "Max Vehicle Depth" and decimal degrees; 2016 to 2019 write
# "Max. depth" and degrees-and-minutes. Both are read, because the older cruises
# are a third of this catalogue and a summary that does not parse is not a
# cruise without depths - it is a cruise whose transit footage gets analysed.
SUMMARY_FIELDS = {
    "max_depth_m": re.compile(r"Max(?:\.|imum)?\s*(?:Vehicle\s*)?depth:\s*([0-9.]+)",
                              re.IGNORECASE),
    "seafloor_depth_m": re.compile(r"Min Seafloor Depth:\s*([0-9.]+)", re.IGNORECASE),
    "distance_m": re.compile(r"Distance Travelled:\s*([0-9.]+)", re.IGNORECASE),
}
# Each of the dive's four moments, with the position it happened at. The pair that
# matters is on-bottom and off-bottom: everything before the first and after the
# second is the vehicle in transit through empty water, which is most of a deep
# dive's footage and none of its interest.
#
# The time and the position are matched separately and the position is optional.
# They arrive on two lines and the second one is the fragile half - it is
# degrees-and-minutes on the older cruises and literally "N/A ; N/A" where the
# navigation dropped out - and losing a whole dive's on-bottom *window* because
# its coordinates are written in another notation would be the expensive kind of
# strictness.
EVENT = r"{label}:[ \t]*(\S+)[ \t]*\r?\n[ \t]*([^\r\n]*)"
EVENTS = {name: re.compile(EVENT.format(label=label)) for name, label in (
    ("in_water", "In Water"), ("on_bottom", "On Bottom"),
    ("off_bottom", "Off Bottom"), ("out_water", "Out Water"))}

DECIMAL_POSITION = re.compile(r"^\s*(-?[0-9.]+)\s*;\s*(-?[0-9.]+)\s*$")
# 28°, 15.064' N ; 79°, 36.072' W - degrees, decimal minutes, hemisphere.
DMS_POSITION = re.compile(
    r"(\d+)\s*°?,?\s*([0-9.]+)'\s*([NSEW])\s*;\s*(\d+)\s*°?,?\s*([0-9.]+)'\s*([NSEW])")


def _position(line: str) -> Optional[Tuple[float, float]]:
    """Latitude and longitude from the line under one of the dive's moments.

    Both notations the archive uses, and None for the rows that say "N/A" - a
    dive whose in-water fix was never recorded still has an on-bottom one.
    """
    decimal = DECIMAL_POSITION.match(line)
    if decimal:
        return float(decimal.group(1)), float(decimal.group(2))
    minutes = DMS_POSITION.search(line)
    if not minutes:
        return None
    first, second = [], []
    for degrees, minute, hemisphere in ((minutes.group(1), minutes.group(2), minutes.group(3)),
                                        (minutes.group(4), minutes.group(5), minutes.group(6))):
        value = float(degrees) + float(minute) / 60.0
        if hemisphere in ("S", "W"):
            value = -value
        (first if hemisphere in ("N", "S") else second).append(value)
    if len(first) != 1 or len(second) != 1:
        return None
    return first[0], second[0]


def noaa_dive_summary(data_url: str, dive: int) -> Optional[dict]:
    """What one dive says about itself: where it worked, how deep, how long.

    This is how a dive can be chosen before it is analysed. "As deep as possible"
    is a question about a number nobody publishes in the video listing, and it
    costs one ranged read of a text file per dive to answer.
    """
    found = ancillary_member(data_url, dive, r"DIVE0*%d\.txt$" % dive)
    if found is None:
        return None
    name, text = found
    return read_dive_summary(text, dive, name)


def read_dive_summary(text: str, dive: int = 0, name: str = "") -> dict:
    """One dive report, read in whichever notation the cruise wrote it in.

    Separate from fetching it so the two generations of the format can be tested
    against the real thing without a network - which is the only way to know that
    the older one parses, since what it looks like is the whole question.
    """
    out = {"dive": dive, "summary": name}
    for field_name, pattern in SUMMARY_FIELDS.items():
        match = pattern.search(text)
        if match:
            out[field_name] = float(match.group(1))
    for field_name, pattern in EVENTS.items():
        match = pattern.search(text)
        if not match:
            continue
        out[f"{field_name}_at"] = match.group(1)
        where = _position(match.group(2))
        if where is not None:
            out[f"{field_name}_latitude"], out[f"{field_name}_longitude"] = where
    # The dive's position, plainly: where the vehicle was when it reached the
    # bottom and started working.
    if "on_bottom_latitude" in out:
        out["latitude"] = out["on_bottom_latitude"]
        out["longitude"] = out["on_bottom_longitude"]
    return out


def working_window(summary: dict) -> Optional[Tuple[datetime, datetime]]:
    """When the vehicle was on the bottom, so the transit can be left out.

    A deep dive spends hours descending and ascending through open water. Those
    hours are recorded and published like the rest, and analysing them is the
    single easiest way to waste a night of compute on footage of nothing.
    """
    start, stop = summary.get("on_bottom_at"), summary.get("off_bottom_at")
    if not start or not stop:
        return None
    try:
        return (datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
                datetime.fromisoformat(stop).replace(tzinfo=timezone.utc))
    except ValueError:
        return None


# What each column of a 1 Hz track is, whatever the cruise called it. The newer
# files head it DATE,TIME,UNIXTIME,DEPTH,ALT,LAT_DD,LON_DD; the 2016 ones write
# "time (unix sec)", "lat (dec. deg.)" and put depth after longitude instead of
# before it. Reading the header rather than the column order is what makes both
# the same file to everything downstream.
TRACK_COLUMNS = (
    ("when", lambda name: "unix" in name),
    ("latitude", lambda name: name.startswith("lat")),
    ("longitude", lambda name: name.startswith("lon")),
    ("depth", lambda name: name.startswith("depth")),
    ("altitude", lambda name: name.startswith("alt")),
)


def _track_columns(fieldnames: Optional[Sequence[str]]) -> Dict[str, str]:
    """Which raw header holds each thing a fix needs."""
    found: Dict[str, str] = {}
    for raw in fieldnames or []:
        name = (raw or "").strip().lower()
        for role, matches in TRACK_COLUMNS:
            if role not in found and matches(name):
                found[role] = raw
                break
    return found


def _read_rov_track(text: str, source: str) -> Optional[Track]:
    """A unix time, a position and a depth per second, skipping the rows with no fix.

    The surface and descent rows have empty coordinates, and DEPTH is published as
    a negative number on the newer cruises - metres below the surface, signed as an
    elevation - and a positive one on the older cruises. It is turned positive here
    because everything downstream, and every reader, means depth as a positive
    number of metres down.
    """
    rows = csv.DictReader(io.StringIO(text))
    columns = _track_columns(rows.fieldnames)
    if not {"when", "latitude", "longitude"} <= set(columns):
        logger.warning("%s: no time and position columns in %s", source, rows.fieldnames)
        return None

    def value(row: dict, role: str) -> Optional[float]:
        return _number(row.get(columns[role])) if role in columns else None

    fixes: List[Tuple[float, Fix]] = []
    for row in rows:
        when = value(row, "when")
        latitude, longitude = value(row, "latitude"), value(row, "longitude")
        if when is None or latitude is None or longitude is None:
            continue
        depth = value(row, "depth")
        fixes.append((when, Fix(latitude=latitude, longitude=longitude,
                                depth_m=None if depth is None else abs(depth),
                                altitude_m=value(row, "altitude"), source=source)))
    if not fixes:
        return None
    logger.info("%s: %d fixes", source, len(fixes))
    # The dive's outline, drawn from the navigation itself rather than from the
    # separate KML the archive also publishes. Same dive, but this one is the
    # positions that the footage is actually joined to, so the line on the map and
    # the points on it cannot disagree.
    return Track(fixes, source,
                 footprint=_path_polygon([(fix.longitude, fix.latitude) for _, fix in fixes]))


# ── NOAA: the dive path, when there is no track ────────────────────────────────

def noaa_dive_path(data_url: str, dive: int) -> Optional[Track]:
    """Where one dive happened, from the line the archive draws for it.

    The path is a bare list of coordinates with no times on it, so it places the
    dive but not the moment: every slice of every recording in the dive gets the
    same fix. That is worth having anyway - it is what lets this dive sit on a map
    next to one from ten years earlier - as long as the fix says which it is.
    """
    name = _listing_match(data_url, rf"dive0*{dive}_path\.kml$")
    if not name:
        return None
    url = urllib.parse.urljoin(data_url, name)
    try:
        points = _kml_points(_fetch(url).decode(errors="replace"))
    except Exception as exc:
        logger.warning("no dive path from %s: %s", url, exc)
        return None
    if not points:
        return None
    source = f"NOAA dive path ({Path(name).name}), whole-dive position"
    middle = points[len(points) // 2]
    return one_place(Fix(latitude=middle[1], longitude=middle[0], source=source),
                     source, footprint=_path_polygon(points))


def _kml_points(text: str) -> List[Tuple[float, float]]:
    """Longitude/latitude pairs out of a KML LineString."""
    points = []
    for block in re.findall(r"<coordinates>(.*?)</coordinates>", text, re.S):
        for triple in block.split():
            parts = triple.split(",")
            if len(parts) >= 2:
                longitude, latitude = _number(parts[0]), _number(parts[1])
                if longitude is not None and latitude is not None:
                    points.append((longitude, latitude))
    return points


def _path_polygon(points: Sequence[Tuple[float, float]], most: int = 400) -> str:
    """A path as GeoJSON the map widget can read: a polygon, drawn there and back.

    A dive is tens of thousands of navigation fixes and drawing all of them in a
    browser is slower than it is informative, so the path is subsampled. The
    endpoints are kept whatever the step lands on.

    It is a `Polygon` and not the `LineString` it describes because the map widget
    in pixel-patrol-geospatial reads every footprint as one - it takes
    `coordinates[0]` as the outer ring - and a LineString handed to that yields one
    position whose two numbers get destructured as if they were pairs. The throw
    happens inside the map's load handler and takes the whole map with it.

    Tracing the ring out along the path and back gives that reader the pairs it
    expects, encloses no area, and comes out of the line layer looking like the
    path it is. The turn-around point repeats, which nothing minds, and a ring of
    no area is not a shape GeoJSON really means you to write - the alternative is
    a report with no map on it.
    """
    step = max(1, len(points) // most)
    thinned = list(points[::step])
    if thinned[-1] != points[-1]:
        thinned.append(points[-1])
    ring = thinned + thinned[::-1]
    while len(ring) < 4:                     # a ring wants four positions, and one
        ring = ring + ring                   # fix on its own only has two
    return json.dumps({"type": "Polygon",
                       "coordinates": [[[round(x, 6), round(y, 6)] for x, y in ring]]})


# ── a cabled camera that does not move ────────────────────────────────────────

ASSETS = ("https://raw.githubusercontent.com/oceanobservatories/asset-management/"
          "master/deployment/{site}_Deploy.csv")


def ooi_deployment(reference: str, when: datetime) -> Optional[Track]:
    """Where an Ocean Observatories instrument was on a given date.

    Position comes from the observatory's own deployment register rather than from
    anything written down here, because an instrument is recovered and reinstalled
    and each deployment has its own coordinates, depth and dates. Reading the
    register means a recording from 2016 and one from 2024 each get the position
    the camera actually had at the time.
    """
    site = reference.split("-")[0]
    try:
        text = _fetch(ASSETS.format(site=site)).decode(errors="replace")
    except Exception as exc:
        logger.warning("no deployment register for %s: %s", site, exc)
        return None
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("Reference Designator") or "").strip() != reference:
            continue
        start, stop = _instant(row.get("startDateTime")), _instant(row.get("stopDateTime"))
        if start is None or when.timestamp() < start:
            continue
        if stop is not None and when.timestamp() > stop:
            continue
        latitude, longitude = _number(row.get("lat")), _number(row.get("lon"))
        if latitude is None or longitude is None:
            continue
        source = (f"OOI deployment {row.get('deploymentNumber', '?')} of {reference}"
                  f" (asset-management register)")
        return one_place(Fix(latitude=latitude, longitude=longitude,
                             depth_m=_number(row.get("deployment_depth")), source=source),
                         source, when=start)
    logger.warning("%s has no deployment covering %s", reference, when.isoformat())
    return None


def _instant(text) -> Optional[float]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).strip()).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


# ── choosing among them ───────────────────────────────────────────────────────

def track_for(recipe: Dict, url: str, when: Optional[datetime]) -> Optional[Track]:
    """The best position source an expedition's recipe can produce for one recording.

    Tried in order of what each can say about a moment, and the first that answers
    wins: a per-second track beats a whole-dive centroid, which beats nothing.
    """
    kind = (recipe or {}).get("kind") or ""
    if kind == "ooi-deployment" and when is not None:
        return ooi_deployment(recipe["reference"], when)
    if kind == "fixed":
        source = recipe.get("source") or "stated in the expedition catalogue"
        return one_place(Fix(latitude=float(recipe["latitude"]),
                             longitude=float(recipe["longitude"]),
                             depth_m=_number(recipe.get("depth_m")), source=source), source)
    if kind == "noaa-dive":
        dive = dive_number(url)
        if dive is None:
            return None
        return noaa_rov_track(recipe["data"], dive) or noaa_dive_path(recipe["data"], dive)
    return None


# ── talking to the archive ────────────────────────────────────────────────────

_LISTINGS: Dict[str, List[str]] = {}


def _listing_match(url: str, pattern: str) -> Optional[str]:
    """A file in a published directory, found by pattern rather than by construction.

    These filenames are nearly regular and not quite: the same cruise writes
    `EX1605L1_DIVE01_Path.kml` and `EX1605l1_Dive01_Summary...pdf`. Building the
    name from the cruise id gets the case wrong on some cruises and the separator
    wrong on others, so the directory is listed once and matched case-insensitively.
    """
    if url not in _LISTINGS:
        try:
            page = _fetch(url).decode(errors="replace")
        except Exception as exc:
            logger.warning("cannot list %s: %s", url, exc)
            _LISTINGS[url] = []
        else:
            _LISTINGS[url] = re.findall(r'href="([^"?][^"]*)"', page)
    for name in _LISTINGS[url]:
        if re.search(pattern, name, re.IGNORECASE):
            return name
    return None


def _fetch(url: str) -> bytes:
    with open_with_retry(urllib.request.Request(url), TIMEOUT) as response:
        return response.read()


# ── handing it to the pipeline ────────────────────────────────────────────────

TRACK_FILE = "PIXEL_PATROL_TRACK"
STARTED_AT = "PIXEL_PATROL_RECORDED_AT"


def stage(recipe: Dict, url: str, scratch: Path) -> Dict[str, str]:
    """Resolve one recording's clock and track, and say how to pass them on.

    The processors cannot look this up themselves. A processor is handed a block of
    pixels and the dimensions it sits at, not the name of the file it came from -
    and the position depends on the name, on the archive's dive log, and on two
    HTTP requests. `collect one` handles exactly one recording and does know its
    URL, so it resolves the position once, writes the track beside the video and
    names it in the environment.
    """
    when = recorded_at(url)
    settings: Dict[str, str] = {}
    if when is not None:
        settings[STARTED_AT] = when.isoformat()
    track = track_for(recipe, url, when)
    if track is None or not len(track):
        return settings
    path = scratch / "track.csv"
    path.write_text(track.to_csv())
    settings[TRACK_FILE] = str(path)
    logger.info("%s: %s", Path(url).name, track.source)
    return settings


def staged_track() -> Optional[Track]:
    """The track `collect one` left for this process, if any."""
    path = os.environ.get(TRACK_FILE)
    if not path or not Path(path).is_file():
        return None
    try:
        return Track.from_csv(Path(path).read_text())
    except Exception as exc:
        logger.warning("cannot read the staged track %s: %s", path, exc)
        return None


def staged_start() -> Optional[datetime]:
    stamp = os.environ.get(STARTED_AT)
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None
