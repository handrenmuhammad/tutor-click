"""Push-to-talk recording, speech-to-text (faster-whisper) and speech (Kokoro).

The microphone, the speaker and the fallback voice come from the desktop backend's `sound`.
"""
import contextlib
import queue
import re
import subprocess
import threading
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
