"""Fetching the natural voice the first time the app starts."""
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tutor_click import config, voice

MODEL, VOICES = b"model " * 300_000, b"voices " * 50_000  # 1.8 MB and 350 KB: more than one read each


class Release(ThreadingHTTPServer):
    """Stands in for the GitHub release the voice files come from."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.files = {"/kokoro.onnx": MODEL, "/voices.bin": VOICES}
        self.asked = []
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.asked.append(self.path)
            body = self.server.files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *args):
            pass


@pytest.fixture
def release(tmp_path, monkeypatch):
    server = Release()
    monkeypatch.setattr(config, "KOKORO_DIR", tmp_path / "share" / "kokoro")
    monkeypatch.setattr(voice, "KOKORO_URL", f"http://127.0.0.1:{server.server_address[1]}/")
    monkeypatch.setattr(voice, "KOKORO_FILES", {name: (len(body), hashlib.sha256(body).hexdigest())
                                                for name, body in (("kokoro.onnx", MODEL), ("voices.bin", VOICES))})
    monkeypatch.setattr(voice, "SPARE_BYTES", 0)
    yield server
    server.shutdown()
    server.server_close()


def folder() -> dict:
    return {p.name: p.read_bytes() for p in config.KOKORO_DIR.iterdir()} if config.KOKORO_DIR.exists() else {}


def test_a_computer_without_the_voice_gets_both_files_whole(release):
    assert voice.missing_voice_files() == ["kokoro.onnx", "voices.bin"]
    assert voice.fetch_voice() is None
    assert folder() == {"kokoro.onnx": MODEL, "voices.bin": VOICES}
    assert voice.missing_voice_files() == []


def test_a_computer_that_has_the_voice_asks_for_nothing(release):
    voice.fetch_voice()
    release.asked.clear()
    assert voice.fetch_voice() is None
    assert release.asked == []


def test_only_the_missing_file_is_fetched(release):
    config.KOKORO_DIR.mkdir(parents=True)
    (config.KOKORO_DIR / "kokoro.onnx").write_bytes(MODEL)
    assert voice.fetch_voice() is None
    assert release.asked == ["/voices.bin"]


def test_a_file_that_arrives_changed_is_not_kept(release):
    release.files["/voices.bin"] = VOICES[:-7] + b"tampered"[:7]
    assert voice.fetch_voice() == "voices.bin did not arrive intact"
    assert folder() == {"kokoro.onnx": MODEL}  # the good one stays; no half file and no .part is left
    assert voice.missing_voice_files() == ["voices.bin"]


def test_a_file_that_keeps_coming_is_cut_off(release):
    release.files["/kokoro.onnx"] = MODEL * 3
    assert voice.fetch_voice() == "kokoro.onnx is larger than it should be"
    assert folder() == {}


def test_no_network_or_a_missing_file_is_said_in_words(release, monkeypatch):
    del release.files["/kokoro.onnx"]
    assert voice.fetch_voice() == "the download failed (Not Found)"
    monkeypatch.setattr(voice, "KOKORO_URL", "http://127.0.0.1:9/")  # nothing listens there
    assert voice.fetch_voice().startswith("the download failed (")
    assert folder() == {}


def test_a_full_disk_is_noticed_before_anything_is_fetched(release, monkeypatch):
    monkeypatch.setattr(voice, "SPARE_BYTES", 10**15)
    assert voice.fetch_voice() == "there is not enough free disk space"
    assert release.asked == []


def test_the_real_list_names_two_files_with_a_size_and_a_checksum_each(monkeypatch):
    monkeypatch.undo()
    assert sorted(voice.KOKORO_FILES) == ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]
    assert all(size > 10_000_000 and len(digest) == 64 for size, digest in voice.KOKORO_FILES.values())
    assert voice.KOKORO_URL.startswith("https://github.com/") and voice.KOKORO_URL.endswith("/")
