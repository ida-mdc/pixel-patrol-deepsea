"""Read video out of a huge remote archive without downloading it.

Expedition video is often published as one multi-gigabyte zip per specimen - the
Schmidt Ocean records on Zenodo are 7.5 to 22 GB each and hold a single 4K mp4.
Downloading one to look at twenty seconds of it is absurd, and on a slow link it
is not an option at all.

Two facts make it avoidable. A zip keeps its index at the end, so the member list
and each member's byte offset can be read with a couple of range requests. And
these archives store the video *uncompressed*, so the mp4's bytes sit contiguously
inside the zip and can be addressed directly.

What you get back is a sparse local file: the right size, with only the ranges you
asked for actually present. ffmpeg reads the index, seeks into the part that is
really there, and decodes it. Everything else stays a hole and costs nothing.

    url = zenodo_file_url("10987660")
    video = RemoteVideo(url, "/tmp/atolla.mp4")
    video.fetch_index()           # the mp4 index, a few MB
    video.fetch_seconds(241, 20)  # only the moment you care about
"""

import json
import os
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

from pixel_patrol_deepsea.remote_file import open_with_retry as _open
from pixel_patrol_deepsea.remote_file import worth_retrying as _worth_retrying
from typing import Dict, List, Optional, Tuple

VIDEO_SUFFIXES = (".mp4", ".mov", ".avi", ".mkv")
CHUNK = 8 << 20
ATTEMPTS = 6


def zenodo_file_url(record: str, suffix: str = ".zip") -> str:
    """Direct download URL of the first matching file in a Zenodo record."""
    with _open(f"https://zenodo.org/api/records/{record}", 60) as response:
        meta = json.load(response)
    for entry in meta.get("files", []):
        if entry["key"].lower().endswith(suffix):
            return entry["links"]["self"]
    raise LookupError(f"no {suffix} in Zenodo record {record}")


def zenodo_citation(record: str) -> str:
    """How the record asks to be cited, taken from the record rather than assumed.

    Written into the provenance file beside the report. Reading it from the API is
    the point: a hand-written label is a guess about whose data this is, and a wrong
    guess is worse than no label at all.
    """
    with _open(f"https://zenodo.org/api/records/{record}", 60) as response:
        meta = json.load(response).get("metadata", {})
    authors = [c.get("name", "") for c in meta.get("creators", []) if c.get("name")]
    who = " & ".join(authors) if len(authors) < 3 else f"{authors[0]} et al."
    year = (meta.get("publication_date") or "")[:4]
    title = meta.get("title") or f"Zenodo record {record}"
    licence = (meta.get("license") or {}).get("id", "")
    where = f"Zenodo, {licence.upper()}" if licence else "Zenodo"
    return " ".join(bit for bit in (who, f"({year})." if year else "", f"{title}.", where) if bit)


def _range(url: str, start: int, end: int) -> bytes:
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with _open(request, 300) as response:
        return response.read()


def _content_length(url: str) -> int:
    request = urllib.request.Request(url, method="HEAD")
    with _open(request, 60) as response:
        return int(response.headers["Content-Length"])


def _central_directory(url: str) -> bytes:
    """The zip index, read from the end of the archive."""
    total = _content_length(url)
    tail = _range(url, max(0, total - 65_600), total - 1)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("not a zip, or the end-of-directory record is not in the tail")
    size, offset = struct.unpack("<II", tail[eocd + 12:eocd + 20])
    locator = tail.rfind(b"PK\x06\x07")
    if locator != -1:                                    # ZIP64
        z64_at = struct.unpack("<Q", tail[locator + 8:locator + 16])[0]
        size, offset = struct.unpack("<QQ", _range(url, z64_at, z64_at + 55)[40:56])
    return _range(url, offset, offset + size - 1)


def _entries(directory: bytes) -> List[Dict]:
    out, at = [], 0
    while at + 46 <= len(directory) and directory[at:at + 4] == b"PK\x01\x02":
        method, = struct.unpack("<H", directory[at + 10:at + 12])
        stored, size = struct.unpack("<II", directory[at + 20:at + 28])
        name_len, extra_len, comment_len = struct.unpack("<HHH", directory[at + 28:at + 34])
        local, = struct.unpack("<I", directory[at + 42:at + 46])
        name = directory[at + 46:at + 46 + name_len].decode("utf-8", "replace")
        extra = directory[at + 46 + name_len:at + 46 + name_len + extra_len]
        if 0xFFFFFFFF in (stored, size, local):
            size, stored, local = _zip64_values(extra, size, stored, local)
        out.append({"name": name, "method": method, "stored": stored, "size": size, "local": local})
        at += 46 + name_len + extra_len + comment_len
    return out


def _zip64_values(extra: bytes, size: int, stored: int, local: int) -> Tuple[int, int, int]:
    at = 0
    while at + 4 <= len(extra):
        tag, length = struct.unpack("<HH", extra[at:at + 4])
        if tag == 0x0001:
            values = iter(struct.unpack("<" + "Q" * (length // 8), extra[at + 4:at + 4 + length - length % 8]))
            if size == 0xFFFFFFFF:
                size = next(values)
            if stored == 0xFFFFFFFF:
                stored = next(values)
            if local == 0xFFFFFFFF:
                local = next(values)
        at += 4 + length
    return size, stored, local


class RemoteVideo:
    """A video inside a remote zip, materialised one byte range at a time."""

    def __init__(self, url: str, path, member: Optional[str] = None):
        self.url = url
        self.path = Path(path)
        entries = [e for e in _entries(_central_directory(url))
                   if e["name"].lower().endswith(VIDEO_SUFFIXES)
                   and (member is None or e["name"] == member)]
        if not entries:
            raise LookupError("no video member in the archive")
        entry = max(entries, key=lambda e: e["size"])
        if entry["method"] != 0:
            raise ValueError(f"{entry['name']} is compressed inside the zip; "
                             "only stored members can be addressed by range")
        header = _range(url, entry["local"], entry["local"] + 29)
        name_len, extra_len = struct.unpack("<HH", header[26:30])
        self.name = entry["name"]
        self.size = entry["size"]
        self.base = entry["local"] + 30 + name_len + extra_len
        self._duration: Optional[float] = None

    def _write(self, start: int, length: int, note: str = "") -> None:
        if not self.path.exists():
            with open(self.path, "wb") as fh:
                fh.truncate(self.size)                   # sparse: no blocks yet
        start = max(0, min(start, self.size - 1))
        length = min(length, self.size - start)
        with open(self.path, "r+b") as fh:
            done = 0
            while done < length:
                want = min(CHUNK, length - done)
                fh.seek(start + done)
                fh.write(_range(self.url, self.base + start + done,
                                self.base + start + done + want - 1))
                done += want
        if note:
            print(f"  {note}: {length / 1e6:.0f} MB")

    def fetch_index(self, tail_mb: int = 12, head_mb: int = 2) -> None:
        """The pieces every read needs: the header, and the index at the end."""
        self._write(0, head_mb << 20)
        self._write(self.size - (tail_mb << 20), tail_mb << 20, "index")

    @property
    def duration(self) -> float:
        """Seconds, read from the index with ffprobe."""
        if self._duration is None:
            import subprocess
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(self.path)],
                capture_output=True, text=True).stdout.strip()
            self._duration = float(out) if out else 0.0
        return self._duration

    def fetch_seconds(self, second: float, span: float = 20.0, lead: float = 2.0) -> None:
        """Materialise roughly the footage around one moment.

        Byte position is interpolated from the average rate, which is close enough
        for a constant-bitrate recording; the lead-in covers the keyframe before it.
        """
        if not self.duration:
            raise RuntimeError("call fetch_index() first so the duration can be read")
        rate = self.size / self.duration
        start = int(max(0.0, second - lead) * rate)
        self._write(start, int((span + lead) * rate), f"t≈{second:.0f}s")

    def disk_used_mb(self) -> float:
        return os.stat(self.path).st_blocks * 512 / 1e6
