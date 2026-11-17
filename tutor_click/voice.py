"""Push-to-talk recording, speech-to-text (faster-whisper) and speech (Kokoro).

The microphone, the speaker and the fallback voice come from the desktop backend's `sound`.
"""
import contextlib
import hashlib
import os
import queue
import re
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import numpy as np

from . import config


class Recorder:
    def __init__(self, sound):
        self.sound = sound
        self.path = config.RUNTIME / "ptt.wav"
        self.proc: subprocess.Popen | None = None

    def start(self):
        self.stop()
        self.path.unlink(missing_ok=True)
        self.proc = self.sound.record(self.path)

    def stop(self) -> Path | None:
        proc, self.proc = self.proc, None
        if proc is None:
            return None
        self.sound.stop_recording(proc)
        return self.path if self.path.exists() and self.path.stat().st_size > 44 else None


class Transcriber:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def warm(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel  # slow import, defer

                self._model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")

    def transcribe(self, wav: Path) -> str:
        self.warm()
        with self._lock:
            segments, _ = self._model.transcribe(str(wav), vad_filter=True)
            return " ".join(s.text.strip() for s in segments).strip()


KOKORO_RATE = 24000
# The Kokoro voice is too big for the repository, so the app fetches it the first time it starts.
# name: (bytes, SHA-256), as published in the kokoro-onnx release these come from.
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = {
    "kokoro-v1.0.int8.onnx": (92_361_271, "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"),
    "voices-v1.0.bin": (28_214_398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
SPARE_BYTES = 200_000_000  # disk space that must be left over after the download


def missing_voice_files() -> list[str]:
    return [name for name in KOKORO_FILES if not (config.KOKORO_DIR / name).exists()]


def fetch_voice() -> str | None:
    """Download the Kokoro files this computer lacks. None when all are in place, else why not, in a few words.

    A file only gets its real name once it is whole and its checksum matches, so a download that
    was cut short is never taken for the voice.
    """
    need = missing_voice_files()
    try:
        config.KOKORO_DIR.mkdir(parents=True, exist_ok=True)
        if need and shutil.disk_usage(config.KOKORO_DIR).free < sum(KOKORO_FILES[n][0] for n in need) + SPARE_BYTES:
            return "there is not enough free disk space"
        for name in need:
            _download(name)
    except ValueError as e:
        return str(e)
    except OSError as e:  # no network, the server said no, the disk filled up
        return f"the download failed ({getattr(e, 'reason', None) or e})"
    return None


def _download(name: str):
    size, digest = KOKORO_FILES[name]
    part = config.KOKORO_DIR / f"{name}.part"
    seen = hashlib.sha256()
    try:
        with urllib.request.urlopen(KOKORO_URL + name, timeout=30) as resp, part.open("wb") as f:
            while chunk := resp.read(1 << 20):
                seen.update(chunk)
                f.write(chunk)
                if f.tell() > size:
                    raise ValueError(f"{name} is larger than it should be")
        if seen.hexdigest() != digest:
            raise ValueError(f"{name} did not arrive intact")
        os.replace(part, config.KOKORO_DIR / name)
    finally:
        part.unlink(missing_ok=True)


class Speaker:
    """Kokoro voice streamed sentence by sentence into the desktop's player; its fallback voice if Kokoro is missing."""

    def __init__(self, sound):
        self.sound = sound
        self.proc: subprocess.Popen | None = None
        self._halt = threading.Event()
        self._kokoro = None
        self._lock = threading.Lock()

    def warm(self):
        self._engine()

    def _engine(self):
        with self._lock:
            model = config.KOKORO_DIR / "kokoro-v1.0.int8.onnx"
            voices = config.KOKORO_DIR / "voices-v1.0.bin"
            if self._kokoro is None and model.exists() and voices.exists():
                from kokoro_onnx import Kokoro  # slow import, defer

                self._kokoro = Kokoro(str(model), str(voices))
            return self._kokoro

    def say(self, text: str):
        """Blocks until speech finishes or stop() is called."""
        self.stop()
        halt = threading.Event()
        self._halt = halt
        engine = self._engine()
        if engine is None:
            self.proc = proc = self.sound.say(text)
            proc.wait()
            return

        self.proc = proc = self.sound.play_pcm(KOKORO_RATE)
        chunks: queue.Queue = queue.Queue()

        def feed():  # writes audio while the next sentence is being synthesized
            try:
                while (pcm := chunks.get()) is not None:
                    proc.stdin.write(pcm)
                    proc.stdin.flush()
                proc.stdin.close()
            except (BrokenPipeError, ValueError):  # stop() killed the player
                with contextlib.suppress(OSError):
                    proc.stdin.close()  # close now so GC doesn't retry the flush and print a warning

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            if halt.is_set() or not sentence:
                continue
            samples, rate = engine.create(sentence, voice=config.VOICE, speed=config.VOICE_SPEED, lang="en-us")
            if halt.is_set():
                break
            assert rate == KOKORO_RATE, rate
            chunks.put((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        chunks.put(None)
        proc.wait()
        writer.join(timeout=1)

    def stop(self):
        self._halt.set()
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
