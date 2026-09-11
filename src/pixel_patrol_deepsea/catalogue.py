"""The expeditions we collect from, and how to find their videos.

Analysing one dive is a demo; analysing an archive is the point. NOAA Ocean
Exploration alone publishes on the order of ten thousand hours across a hundred
and nineteen cruises, and nobody has watched most of it. That only becomes
tractable if the list of what exists is data rather than a shell script, so it
lives in `expeditions.yaml` and this module reads it.

Discovery is deliberately separate from analysis. Listing an expedition costs a
handful of HTTP requests and tells you what is there; analysing it costs hours.
Keeping them apart is what lets a scheduler notice that a cruise has three new
dives and process only those.
"""

import fnmatch
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from pixel_patrol_deepsea.remote_file import open_with_retry

TIMEOUT = 120


@dataclass
class Expedition:
    """One cruise, and the recipe for listing its recordings.

    `depth` is how many directory levels sit between the listing URL and the
    videos: NOAA nests them as <cruise>/Video/<dive>/Compressed/<file>, so two.
    Written out rather than crawled to any depth, because an archive with a
    mistake in it should fail to list rather than walk the whole server.
    """
    id: str
    listing: str = ""
    pattern: str = "*.mp4"
    depth: int = 0
    # Named outright, for a set that is small, fixed and not behind a directory
    # index - a benchmark, say, or seven records with DOIs.
    videos: List[str] = field(default_factory=list)
    # A sibling file holding per-frame ground truth for each recording, if there is
    # one. `gt.txt` beside `MWD.mov` makes the expedition scoreable.
    truth: str = ""
    # How to find out where this expedition's footage was filmed. A recipe rather
    # than coordinates, because the answer differs per dive and per deployment and
    # is published by the archive - see `locations`.
    location: Dict = field(default_factory=dict)
    name: str = ""
    # Where the expedition describes itself, for a reader who wants to know what the
    # ship was doing rather than what this package measured. Only where one exists
    # and answers: NOAA names its expedition sites per cruise with no pattern, and
    # half of them have moved to an archive domain, so most entries have none.
    link: str = ""
    archive: str = ""
    vessel: str = ""
    date: str = ""
    notes: str = ""

    @property
    def title(self) -> str:
        return self.name or self.id


@dataclass
class Manifest:
    """What one expedition turned out to hold."""
    expedition: str
    videos: List[str] = field(default_factory=list)
    listed_at: str = ""

    def to_json(self) -> str:
        return json.dumps({"expedition": self.expedition, "listed_at": self.listed_at,
                           "videos": self.videos}, indent=1)


def load_catalogue(path: Path) -> List[Expedition]:
    """Read the expedition list, keeping the file's order."""
    import yaml

    entries = yaml.safe_load(Path(path).read_text()) or []
    known = {f.name for f in Expedition.__dataclass_fields__.values()}
    return [Expedition(**{k: v for k, v in entry.items() if k in known}) for entry in entries]


def find_expedition(catalogue: Iterable[Expedition], wanted: str) -> Expedition:
    for expedition in catalogue:
        if expedition.id == wanted:
            return expedition
    raise LookupError(f"no expedition {wanted!r} in the catalogue")


def discover(expedition: Expedition, listing: Optional[callable] = None) -> Manifest:
    """Every video URL in one expedition: named outright, or found by walking."""
    from datetime import datetime, timezone

    if expedition.videos:
        urls = list(expedition.videos)
    else:
        read = listing or _entries
        urls = _walk(expedition.listing, expedition.depth, expedition.pattern, read)
    return Manifest(expedition=expedition.id, videos=sorted(urls),
                    listed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def _walk(url: str, depth: int, pattern: str, read) -> List[str]:
    names = read(url)
    if depth == 0:
        return [urllib.parse.urljoin(url, name) for name in names
                if fnmatch.fnmatch(name, pattern)]
    found: List[str] = []
    for name in names:
        if name.endswith("/"):
            found.extend(_walk(urllib.parse.urljoin(url, name), depth - 1, pattern, read))
    return found


def _entries(url: str) -> List[str]:
    """Names in one directory listing, as an Apache-style index reports them."""
    request = urllib.request.Request(url)
    with open_with_retry(request, TIMEOUT) as response:
        page = response.read().decode(errors="replace")
    return [href for href in re.findall(r'href="([^"?][^"]*)"', page)
            if not href.startswith(("/", "http", "..", "?"))]


def truth_urls(expedition: Expedition, video: str) -> List[str]:
    """Where the ground truth for one recording may live, likeliest first.

    A MOT dataset puts it in one of two places, and DeepSea-MOT uses both: some
    sequences keep `gt.txt` beside the recording and the rest keep it in the `gt/`
    directory the format specifies. Which of the two a sequence chose is not
    something a catalogue entry should have to state per recording, so both are
    offered and the caller takes the one that answers.
    """
    if not expedition.truth:
        return []
    folder = video.rsplit("/", 1)[0]
    candidates = [f"{folder}/{expedition.truth}"]
    if "/" not in expedition.truth:
        candidates.append(f"{folder}/gt/{expedition.truth}")
    return candidates


def truth_url(expedition: Expedition, video: str) -> Optional[str]:
    """The first place to look for one recording's ground truth."""
    candidates = truth_urls(expedition, video)
    return candidates[0] if candidates else None


def video_size(url: str) -> int:
    """Bytes, from a HEAD request - the one cheap fact about a remote recording."""
    request = urllib.request.Request(url, method="HEAD")
    with open_with_retry(request, TIMEOUT) as response:
        return int(response.headers.get("Content-Length") or 0)


def catalogue_path() -> Path:
    """The expedition list that ships with the package."""
    return Path(__file__).parent / "expeditions.yaml"
