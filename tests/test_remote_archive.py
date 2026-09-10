"""The zip parsing is exercised against real archives built on disk and served
back through a stub, so no network is involved and ZIP64 is covered honestly."""
import io
import struct
import zipfile

import pytest

from pixel_patrol_deepsea import remote_archive
from pixel_patrol_deepsea.remote_archive import RemoteVideo, _entries


@pytest.fixture
def served(monkeypatch, tmp_path):
    """Build a zip, then answer range requests against its bytes."""
    def build(members, force_zip64=False):
        path = tmp_path / "archive.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        blob = path.read_bytes()

        def fake_range(url, start, end):
            return blob[start:end + 1]

        monkeypatch.setattr(remote_archive, "_range", fake_range)
        monkeypatch.setattr(remote_archive, "_content_length", lambda url: len(blob))
        return blob
    return build


def test_finds_the_video_and_its_byte_offset(served, tmp_path):
    payload = b"\x00\x01\x02\x03" * 4096
    served({"notes.txt": b"hello", "dive.mp4": payload})
    video = RemoteVideo("http://example/archive.zip", tmp_path / "out.mp4")
    assert video.name == "dive.mp4"
    assert video.size == len(payload)


def test_picks_the_largest_video_when_several_are_present(served, tmp_path):
    served({"small.mp4": b"a" * 100, "big.mov": b"b" * 5000})
    assert RemoteVideo("http://example/a.zip", tmp_path / "o.mp4").name == "big.mov"


def test_ignores_members_that_are_not_video(served, tmp_path):
    served({"a.png": b"x" * 500, "b.csv": b"y" * 500})
    with pytest.raises(LookupError):
        RemoteVideo("http://example/a.zip", tmp_path / "o.mp4")


def test_refuses_a_compressed_member(monkeypatch, tmp_path):
    path = tmp_path / "deflated.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("dive.mp4", b"compressible " * 500)
    blob = path.read_bytes()
    monkeypatch.setattr(remote_archive, "_range", lambda url, s, e: blob[s:e + 1])
    monkeypatch.setattr(remote_archive, "_content_length", lambda url: len(blob))
    # Deflated members cannot be addressed by byte range, and pretending otherwise
    # would hand ffmpeg garbage rather than fail.
    with pytest.raises(ValueError, match="compressed"):
        RemoteVideo("http://example/a.zip", tmp_path / "o.mp4")


def test_materialises_only_the_requested_bytes(served, tmp_path):
    payload = bytes(range(256)) * 8192
    served({"dive.mp4": payload})
    out = tmp_path / "out.mp4"
    video = RemoteVideo("http://example/a.zip", out)
    video._write(0, 4096)
    assert out.stat().st_size == len(payload)          # right size...
    assert out.read_bytes()[:4096] == payload[:4096]   # ...with only the head real
    assert out.read_bytes()[4096:8192] == b"\x00" * 4096


def test_a_second_range_leaves_the_first_intact(served, tmp_path):
    payload = bytes(range(256)) * 8192
    served({"dive.mp4": payload})
    out = tmp_path / "out.mp4"
    video = RemoteVideo("http://example/a.zip", out)
    video._write(0, 2048)
    video._write(1 << 12, 2048)
    written = out.read_bytes()
    assert written[:2048] == payload[:2048]
    assert written[1 << 12:(1 << 12) + 2048] == payload[1 << 12:(1 << 12) + 2048]


def test_fetching_a_moment_needs_a_duration(served, tmp_path):
    served({"dive.mp4": b"z" * 4096})
    video = RemoteVideo("http://example/a.zip", tmp_path / "o.mp4")
    video._duration = 0.0
    with pytest.raises(RuntimeError, match="fetch_index"):
        video.fetch_seconds(10)


def test_a_moment_maps_to_a_plausible_byte_range(served, tmp_path):
    payload = b"q" * (1 << 20)
    served({"dive.mp4": payload})
    video = RemoteVideo("http://example/a.zip", tmp_path / "o.mp4")
    video._duration = 100.0
    asked = []
    video._write = lambda start, length, note="": asked.append((start, length))
    video.fetch_seconds(50, span=10, lead=0)
    start, length = asked[0]
    assert start == pytest.approx(len(payload) * 0.5, rel=0.01)
    assert length == pytest.approx(len(payload) * 0.1, rel=0.01)


def test_reads_a_zip64_directory(tmp_path, monkeypatch):
    path = tmp_path / "z64.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr("dive.mp4", b"v" * 1024)
    blob = bytearray(path.read_bytes())
    entries = _entries(_directory_of(blob))
    assert [e["name"] for e in entries] == ["dive.mp4"]
    assert entries[0]["size"] == 1024


def _directory_of(blob: bytearray) -> bytes:
    eocd = blob.rfind(b"PK\x05\x06")
    size, offset = struct.unpack("<II", blob[eocd + 12:eocd + 20])
    return bytes(blob[offset:offset + size])


def test_retries_a_failing_request(monkeypatch):
    monkeypatch.setattr(remote_archive.time, "sleep", lambda _: None)
    attempts = {"n": 0}

    def flaky(request, timeout):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("gateway timeout")
        return io.BytesIO(b"ok")

    monkeypatch.setattr(remote_archive.urllib.request, "urlopen", flaky)
    assert remote_archive._open("http://example", 5).read() == b"ok"
    assert attempts["n"] == 3


def test_waits_out_a_busy_server_but_not_a_wrong_url():
    import urllib.error

    from pixel_patrol_deepsea.remote_archive import _worth_retrying

    def http(code):
        return urllib.error.HTTPError("http://x", code, "", None, None)

    assert _worth_retrying(http(504))          # gateway timeout: the archive is busy
    assert _worth_retrying(http(500))
    assert _worth_retrying(http(429))          # rate limited: back off and try again
    assert not _worth_retrying(http(404))      # a missing record stays missing
    assert not _worth_retrying(http(403))
    assert _worth_retrying(TimeoutError())     # no code at all: worth another go
