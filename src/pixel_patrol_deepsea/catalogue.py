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
    # What this archive allows and asks for, where it differs from what its
    # organisation allows and asks for generally. Almost always empty: the terms
    # belong to the archive, not to the cruise, so they are looked up by `archive`
    # in RIGHTS below and only written here when one expedition is an exception.
    rights: str = ""
    credit: str = ""

    @property
    def title(self) -> str:
        return self.name or self.id

    @property
    def terms(self) -> "Terms":
        """What may be done with this expedition's footage, and who to credit.

        Checked at the source rather than assumed, because the three archives this
        collection reads from do not agree: NOAA's video is in the public domain,
        MBARI's benchmark is share-alike, and the observatory's is open with a
        required acknowledgement and no licence named at all. A page that shows
        crops of all three has to say so per expedition, and a CSV that leaves with
        somebody has to carry it.
        """
        known = RIGHTS.get(self.archive, RIGHTS[""])
        return Terms(licence=self.rights or known.licence,
                     credit=self.credit or known.credit,
                     cite=known.cite, url=known.url)


@dataclass(frozen=True)
class Terms:
    """The licence of an archive's footage, and the credit it asks for."""
    licence: str
    credit: str
    cite: str = ""
    url: str = ""

    @property
    def share_alike(self) -> bool:
        return "BY-SA" in self.licence.upper()


# Read off each archive's own terms, in September 2026, with the page they are
# stated on. Quoted rather than paraphrased where the wording is the obligation.
RIGHTS: Dict[str, Terms] = {
    "NOAA Ocean Exploration": Terms(
        licence="public domain",
        credit="NOAA Ocean Exploration",
        cite="Video published by NOAA Ocean Exploration. \"All video on the portal "
             "is in the public domain and should be credited to NOAA Ocean "
             "Exploration.\" A caption carrying the word \"copyright\" is the "
             "exception and needs permission.",
        url="https://oceanexplorer.noaa.gov/news/media-kit.html"),
    "MBARI, via Hugging Face": Terms(
        licence="CC BY-SA 4.0",
        credit="MBARI (DeepSea-MOT)",
        cite="Barnard, K., Liu, E., Walz, K., Schlining, B., Jacobsen Stout, N. & "
             "Lundsten, L. (2025). DeepSea MOT: A benchmark dataset for "
             "multi-object tracking on deep-sea video. arXiv:2509.03499. "
             "Share-alike: anything derived from these frames carries the same "
             "licence.",
        url="https://huggingface.co/datasets/MBARI-org/DeepSea-MOT"),
    "Ocean Observatories Initiative Regional Cabled Array": Terms(
        licence="open, with acknowledgement (no licence named)",
        credit="NSF Ocean Observatories Initiative, WHOI OOI Program Office, and "
               "the Regional Cabled Array (University of Washington)",
        cite="OOI User Terms and Conditions: users \"are required to specifically "
             "acknowledge the National Science Foundation and the WHOI OOI Program "
             "Office when core data/infrastructure is used and individual "
             "researchers, groups, or organizations when project specific data is "
             "used\", and \"must include an appropriate citation crediting the "
             "source\". The data may be redistributed at no cost, and are for "
             "research and education rather than operational use.",
        url="https://oceanobservatories.org/wp-content/uploads/2024/01/"
            "1102-00020_Data_User_Terms_Conditions_OOI_2019-01-02_Ver_2-00.pdf"),
    "": Terms(licence="unknown - check before redistributing",
              credit="the archive that published it"),
}


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
