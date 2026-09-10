"""A read-only file over HTTP range requests, for handing straight to a decoder.

Nothing is written to disk. `av.open(remote_video(url))` decodes a recording that
stays where it was published, and only the byte ranges the decoder actually asks
for cross the network - which, if you seek about rather than play through, is a
small share of the file.

It cannot make the pixels free: to look at a frame you must fetch the bytes that
encode it. What it does is stop you fetching the rest, and stop you keeping any of
it afterwards.
"""

import io
import time
import urllib.error
import urllib.request
from typing import Dict

CHUNK = 1 << 20          # 1 MB: small enough that seeking is cheap, large enough
                         # that an archive does not see a request per frame and
                         # start answering 503
TIMEOUT = 120
ATTEMPTS = 6


class RemoteFile(io.RawIOBase):
    """Seekable, read-only, fetched in chunks and cached in memory."""

    def __init__(self, url: str, chunk: int = CHUNK, cache_limit: int = 512):
        self.url = url
        self.chunk = chunk
        self.size = _size_of(url)
        self.bytes_fetched = 0
        self.requests = 0
        self._position = 0
        self._chunks: Dict[int, bytes] = {}
        self._cache_limit = cache_limit

    # -- the file protocol a decoder needs -----------------------------------
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self.size}[whence]
        self._position = max(0, min(self.size, base + offset))
        return self._position

    def readinto(self, buffer) -> int:
        wanted = min(len(buffer), self.size - self._position)
        if wanted <= 0:
            return 0
        data = self._read(self._position, wanted)
        buffer[:len(data)] = data
        self._position += len(data)
        return len(data)

    # -- fetching ------------------------------------------------------------
    def _read(self, start: int, length: int) -> bytes:
        first, last = start // self.chunk, (start + length - 1) // self.chunk
        joined = b"".join(self._chunk(index) for index in range(first, last + 1))
        offset = start - first * self.chunk
        return joined[offset:offset + length]

    def _chunk(self, index: int) -> bytes:
        if index in self._chunks:
            return self._chunks[index]
        start = index * self.chunk
        end = min(self.size, start + self.chunk) - 1
        data = _range(self.url, start, end)
        self.bytes_fetched += len(data)
        self.requests += 1
        if len(self._chunks) >= self._cache_limit:
            # A decoder walks forward; the chunks it wanted longest ago are the ones
            # it is least likely to want again, and holding a whole dive in memory
            # would defeat the point of not holding it on disk.
            self._chunks.pop(next(iter(self._chunks)))
        self._chunks[index] = data
        return data


def remote_video(url: str, chunk: int = CHUNK) -> io.BufferedReader:
    """The URL as something `av.open` will accept."""
    return io.BufferedReader(RemoteFile(url, chunk), buffer_size=chunk)


def _size_of(url: str) -> int:
    request = urllib.request.Request(url, method="HEAD")
    with open_with_retry(request, TIMEOUT) as response:
        if response.headers.get("Accept-Ranges") != "bytes":
            raise ValueError(f"{url} does not serve byte ranges")
        return int(response.headers["Content-Length"])


def _range(url: str, start: int, end: int) -> bytes:
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with open_with_retry(request, TIMEOUT) as response:
        return response.read()


def open_with_retry(request, timeout: int):
    """Retry with a growing pause.

    Archives that publish thousands of hours of video answer a burst of range
    requests with 503 or 504 often enough that a single failure means nothing, and
    a scan that dies two-thirds of the way through wastes everything already read.
    """
    for attempt in range(ATTEMPTS):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == ATTEMPTS - 1 or not worth_retrying(exc):
                raise
            time.sleep(3 * 2 ** attempt)


def worth_retrying(exc: Exception) -> bool:
    """Wait out a busy server; do not wait out a wrong URL.

    A missing or forbidden record answers the same way however long you leave it,
    and the backoff turns that into a minute and a half of sleeping before the
    caller finds out. Too many requests and anything the server blames on itself
    are worth another go.
    """
    code = getattr(exc, "code", None)
    return not (isinstance(code, int) and 400 <= code < 500 and code != 429)
