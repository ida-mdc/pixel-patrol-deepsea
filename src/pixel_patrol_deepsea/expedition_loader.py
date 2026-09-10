"""A loader whose input file is a list of video links, not a video.

An expedition is thousands of recordings on somebody else's web server. Handing
those to pixel-patrol used to mean a wrapper around it: stage a recording, run the
pipeline on it, delete it, repeat, then merge the parquets. That is a lot of
machinery for something the loader contract already covers.

pixel-patrol calls a file with more than one image inside it a container, and asks
a loader for `n_images` and then for records by range. A manifest of video URLs is
exactly that shape: one small local file that ordinary file discovery finds, and
`n_images` recordings behind it, each streamed when its turn comes. So one
expedition is one input and one report, no staging and no merge step, and the
recordings stay where they were published.

    python -m pixel_patrol_deepsea.collect list EX2107 -o EX2107.expedition
    pixel-patrol process . -o EX2107.parquet --loader expedition --slice-size T=50

The file is the JSON that `collect list` writes:

    {"expedition": "EX2107", "videos": ["https://.../one.mp4", ...]}
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set, Tuple

import numpy as np

from pixel_patrol_base.core.contracts import FileInfo
from pixel_patrol_base.core.record import Record

logger = logging.getLogger(__name__)

EXTENSION = "expedition"


def read_links(path: Path) -> List[str]:
    """The video URLs in a manifest, in the order it lists them."""
    found = json.loads(Path(path).read_text())
    if isinstance(found, list):
        return [str(entry) for entry in found]
    return [str(entry) for entry in found.get("videos", [])]


def recording_name(url: str) -> str:
    """`.../EX2107_VID_20211027T164000Z_ROVHD_Low.mp4` -> the stem.

    This becomes the child id, which is what every widget shows as the recording
    and what a sighting is keyed by, so it has to be stable and readable.
    """
    return Path(url.split("?")[0]).stem or url


class ExpeditionLoader:
    """Reads a manifest of remote recordings as one container of many images."""

    NAME = "expedition"
    DESCRIPTION = ("Treats a manifest of video URLs as one container of recordings, "
                   "each streamed from where it was published rather than downloaded. "
                   "One expedition is one input and one report.")

    SUPPORTED_EXTENSIONS: Set[str] = {EXTENSION}
    FOLDER_EXTENSIONS: Set[str] = set()
    # Always read the header: the file on disk is a few kilobytes of JSON and says
    # nothing about the hours of video behind it.
    CONTAINER_EXTENSIONS: Set[str] = {EXTENSION}

    def is_folder_supported(self, _path: Path) -> bool:
        return False

    def read_header(self, file_path: Path) -> FileInfo:
        """How many recordings, and the shape of one of them.

        The contract asks for a representative shape, so the first recording is
        probed - one HTTP request against a header, no decoding. A cruise films
        every dive with the same camera at the same settings, and where that turns
        out to be untrue the pipeline re-reads the real shape per record anyway.
        """
        links = read_links(file_path)
        if not links:
            raise ValueError(f"{file_path} lists no videos")
        meta = self._probe(links[0])
        return FileInfo(
            shape=tuple(int(x) for x in meta["shape"]),
            dtype=np.dtype(meta["dtype"]),
            dim_order=meta["dim_order"],
            n_images=len(links),
            deferred_dims="".join(d for d in meta["dim_order"] if d not in "TYX") or None,
        )

    def load_range(self, file_path: Path, start: int, stop: int) -> Iterator[Tuple[str, Record]]:
        """One record per link in [start, stop), named after the recording.

        A link that cannot be read is logged and skipped rather than failing the
        expedition: over a thousand recordings, one of them will be truncated or
        withdrawn, and losing the other nine hundred to it would be absurd.
        """
        links = read_links(file_path)
        for index in range(start, min(stop, len(links))):
            url = links[index]
            try:
                yield recording_name(url), self._read(url)
            except Exception as exc:
                logger.warning("expedition: skipping %s: %s", url, exc)

    def load(self, source: str) -> Record:
        """A single link, for when one recording is the whole input."""
        return self._read(source)

    # -- delegation to the video loader ---------------------------------------
    #
    # It already builds a dask array over frames, chooses between seeking and
    # sequential reads per format, and knows which dims to defer. All of that is
    # the same whether the bytes come off a disk or a web server, and ffmpeg takes
    # a URL wherever it takes a path.

    @staticmethod
    def _video_loader():
        from pixel_patrol_loader_video.plugins.loaders.video_loader import VideoLoader

        return VideoLoader()

    def _probe(self, url: str) -> Dict[str, Any]:
        from pixel_patrol_loader_video.plugins.loaders.video_loader import _probe_video

        return _probe_video(url)

    def _read(self, url: str) -> Record:
        record = self._video_loader().load(url)
        record.meta["source_url"] = url
        return record
