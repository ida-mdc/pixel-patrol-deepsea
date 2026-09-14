"""Serving a built collection.

The viewer decides how to open a report by asking the server for one byte: a 206
and it reads the parquet where it lies, anything else and it downloads the whole
thing. A 3.37 GB report does not survive being downloaded into WASM, so what is
checked here is that the ranges are real - the right bytes, the right headers, and
the footer-relative form a parquet reader opens with.
"""

import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from pixel_patrol_deepsea.serve import Ranged, serve


@pytest.fixture
def collection(tmp_path):
    """A built collection, with a file big enough to ask for a piece of."""
    (tmp_path / "index.html").write_text("<html>the page</html>")
    (tmp_path / "parquet").mkdir()
    (tmp_path / "parquet" / "EX2107.parquet").write_bytes(bytes(range(256)) * 40)
    return tmp_path


@pytest.fixture
def address(collection):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                partial(Ranged, directory=str(collection)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _ask(url, byte_range=None, method="GET"):
    request = urllib.request.Request(url, method=method)
    if byte_range:
        request.add_header("Range", byte_range)
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_a_range_is_answered_with_that_range_and_nothing_more(address):
    status, headers, body = _ask(f"{address}/parquet/EX2107.parquet", "bytes=100-199")
    assert status == 206
    assert headers["Content-Range"] == "bytes 100-199/10240"
    assert body == (bytes(range(256)) * 40)[100:200]


def test_the_one_byte_probe_says_how_big_the_file_is(address):
    """What the viewer asks before it decides to read in place."""
    status, headers, body = _ask(f"{address}/parquet/EX2107.parquet", "bytes=0-0")
    assert status == 206 and len(body) == 1
    assert headers["Content-Range"].split("/")[1] == "10240"


def test_a_range_open_at_the_end_runs_to_the_end(address):
    status, _, body = _ask(f"{address}/parquet/EX2107.parquet", "bytes=10140-")
    assert status == 206 and len(body) == 100


def test_the_footer_is_asked_for_from_the_end(address):
    """`bytes=-8` is how a parquet reader finds PAR1 and the metadata length."""
    status, headers, body = _ask(f"{address}/parquet/EX2107.parquet", "bytes=-8")
    assert status == 206
    assert headers["Content-Range"] == "bytes 10232-10239/10240"
    assert body == (bytes(range(256)) * 40)[-8:]


def test_a_range_past_the_end_is_refused_rather_than_wrapped(address):
    status, headers, _ = _ask(f"{address}/parquet/EX2107.parquet", "bytes=99999-")
    assert status == 416
    assert headers["Content-Range"] == "bytes */10240"


def test_a_whole_file_still_comes_whole_and_says_ranges_are_welcome(address):
    status, headers, body = _ask(f"{address}/index.html")
    assert status == 200 and b"the page" in body
    assert headers["Accept-Ranges"] == "bytes"


def test_a_head_admits_to_ranges_too(address):
    """Some clients ask a HEAD first and never try a range if it is not offered."""
    status, headers, _ = _ask(f"{address}/index.html", method="HEAD")
    assert status == 200 and headers["Accept-Ranges"] == "bytes"


def test_it_stays_on_this_machine_unless_it_is_told_otherwise(collection, monkeypatch):
    """A collection holds three archives' footage under three sets of terms, so
    handing it to the network is a thing to do on purpose."""
    from pixel_patrol_deepsea import serve as module

    asked = []
    monkeypatch.setattr(module, "serve", lambda *args: asked.append(args) or 0)
    module.main([str(collection)])
    module.main([str(collection), "--host", "0.0.0.0"])
    assert [call[2] for call in asked] == ["127.0.0.1", "0.0.0.0"]


def test_a_folder_that_was_never_built_is_named_rather_than_served(tmp_path, caplog):
    assert serve(tmp_path, 0) == 1
    assert "collect site" in caplog.text
