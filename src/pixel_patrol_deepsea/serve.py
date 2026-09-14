"""Serve a built collection over HTTP, byte ranges and all.

    python -m pixel_patrol_deepsea.serve collection/

The page has to be served rather than opened: a `file://` page cannot fetch the
tile store, so the wall stays empty. The obvious answer is `python3 -m
http.server`, and it is the wrong one here - it ignores `Range` and answers the
whole file. The viewer probes for ranges before it opens a report, so where they
are ignored it falls back to downloading the parquet whole, and GOA2004 is 3.37
GB: more than WASM's address space can hold twice, which is what the download
needs while it joins its chunks. Served with ranges, the same report is opened by
reading its footer and then only the row groups a query touches - 0.3 MB rather
than 3.37 GB.

This is a viewer for a collection on a laptop, not a public server: it binds to
localhost and serves one directory. `--host 0.0.0.0` offers it to the network
instead, which is a thing to do on purpose and not by default - a collection
holds an archive's footage under three different sets of terms.
"""

import argparse
import functools
import http.server
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ASKED = re.compile(r"bytes=(\d*)-(\d*)$")


class Ranged(http.server.SimpleHTTPRequestHandler):
    """`SimpleHTTPRequestHandler`, plus the one header it is missing."""

    def send_head(self):
        said = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not said or not os.path.isfile(path):
            return super().send_head()
        match = ASKED.match(said.strip())
        if not match:
            return super().send_head()
        size = os.path.getsize(path)
        # `bytes=-500` is the last 500 bytes, which is how a parquet footer is read.
        if match.group(1):
            first = int(match.group(1))
            last = int(match.group(2)) if match.group(2) else size - 1
        else:
            first, last = max(0, size - int(match.group(2) or 0)), size - 1
        last = min(last, size - 1)
        if first >= size or first > last:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        handle = open(path, "rb")
        handle.seek(first)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
        self.send_header("Content-Length", str(last - first + 1))
        self.end_headers()
        return _Piece(handle, last - first + 1)

    def end_headers(self):
        # Said on every answer, so a client that asks a HEAD first believes it.
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def log_message(self, format, *args):
        logger.debug(format, *args)


class _Piece:
    """The slice of the file that was asked for, and not a byte past it."""

    def __init__(self, handle, length: int):
        self.handle, self.left = handle, length

    def read(self, want: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        data = self.handle.read(self.left if want < 0 else min(want, self.left))
        self.left -= len(data)
        return data

    def close(self):
        self.handle.close()


def serve(root: Path, port: int = 8000, host: str = "127.0.0.1") -> int:
    root = Path(root)
    if not (root / "index.html").exists():
        logger.error("%s has no index.html - build it with `collect site` first", root)
        return 1
    handler = functools.partial(Ranged, directory=str(root))
    with http.server.ThreadingHTTPServer((host, port), handler) as httpd:
        if host in ("0.0.0.0", "::"):
            logger.info("serving %s to the whole network on port %d", root, port)
        logger.info("%s at http://%s:%d/ - ctrl-c to stop", root,
                    "localhost" if host in ("0.0.0.0", "::") else host, port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("stopped")
    return 0


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="a collection built by `collect site`")
    parser.add_argument("-p", "--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1",
                        help="0.0.0.0 to offer it to the network as well")
    args = parser.parse_args(argv)
    return serve(args.root, args.port, args.host)


if __name__ == "__main__":
    sys.exit(main())
