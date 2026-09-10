"""Reading a remote recording without keeping any of it."""

import io

import pytest

from pixel_patrol_deepsea import remote_file
from pixel_patrol_deepsea.remote_file import RemoteFile, worth_retrying


@pytest.fixture
def served(monkeypatch):
    """A fake HTTP server holding one file, counting what is asked of it."""
    body = bytes(range(256)) * 400          # 102,400 bytes
    asked = []

    def fake_size(url):
        return len(body)

    def fake_range(url, start, end):
        asked.append((start, end))
        return body[start:end + 1]

    monkeypatch.setattr(remote_file, "_size_of", fake_size)
    monkeypatch.setattr(remote_file, "_range", fake_range)
    return body, asked


def test_reads_the_bytes_that_were_asked_for(served):
    body, _ = served
    handle = RemoteFile("http://example/x", chunk=1024)
    handle.seek(5000)
    assert handle.read(100) == body[5000:5100]


def test_fetches_only_the_chunks_a_read_touches(served):
    body, asked = served
    handle = RemoteFile("http://example/x", chunk=1024)
    handle.seek(2048)
    handle.read(10)
    assert len(asked) == 1
    assert handle.bytes_fetched == 1024


def test_a_read_spanning_two_chunks_fetches_both(served):
    body, asked = served
    handle = RemoteFile("http://example/x", chunk=1024)
    handle.seek(1000)
    assert handle.read(100) == body[1000:1100]
    assert len(asked) == 2


def test_reading_the_same_place_twice_costs_nothing_the_second_time(served):
    body, asked = served
    handle = RemoteFile("http://example/x", chunk=1024)
    handle.seek(4096); handle.read(50)
    handle.seek(4096); handle.read(50)
    assert len(asked) == 1


def test_seeking_from_the_end_works(served):
    body, _ = served
    handle = RemoteFile("http://example/x", chunk=1024)
    handle.seek(-20, io.SEEK_END)
    assert handle.read(20) == body[-20:]


def test_reading_past_the_end_stops_there(served):
    body, _ = served
    handle = RemoteFile("http://example/x", chunk=1024)
    handle.seek(len(body) - 10)
    assert len(handle.read(500)) == 10
    assert handle.read(1) == b""


def test_forgets_old_chunks_rather_than_holding_a_whole_dive(served):
    body, asked = served
    handle = RemoteFile("http://example/x", chunk=1024, cache_limit=2)
    for start in (0, 2048, 4096, 0):
        handle.seek(start)
        handle.read(10)
    assert len(asked) == 4          # the first chunk was dropped and refetched


def test_it_is_a_file_a_decoder_will_accept(served):
    handle = remote_file.remote_video("http://example/x", chunk=1024)
    assert handle.readable() and handle.seekable()
    assert isinstance(handle, io.BufferedReader)


def test_waits_out_a_busy_archive_but_not_a_wrong_url():
    import urllib.error

    def http(code):
        return urllib.error.HTTPError("http://x", code, "", None, None)

    assert worth_retrying(http(503))
    assert worth_retrying(http(504))
    assert worth_retrying(http(429))
    assert not worth_retrying(http(404))
    assert worth_retrying(TimeoutError())
