"""The microphone and speaker child processes, with a stand-in for PortAudio."""
import signal
import subprocess
import sys
import time
import wave

import pytest

from tutor_click.platform import pcm

FAKE_SOUNDDEVICE = """
import os, threading

class RawInputStream:
    def __init__(self, samplerate, channels, dtype, callback):
        assert (samplerate, channels, dtype) == (16000, 1, "int16")
        self.callback, self.on = callback, threading.Event()
    def __enter__(self):
        def feed():
            while not self.on.wait(0.01):
                self.callback(b"\\x10\\x00" * 160, 160, None, None)   # 10 ms of a quiet, steady sound
        self.thread = threading.Thread(target=feed); self.thread.start()
        open(os.environ["FAKE_READY"], "w").close()
        return self
    def __exit__(self, *a):
        self.on.set(); self.thread.join()

class RawOutputStream:
    def __init__(self, samplerate, channels, dtype):
        self.out = open(os.environ["FAKE_PLAYED"], "wb")
        self.out.write(f"{samplerate} {channels} {dtype}\\n".encode())
    def __enter__(self): return self
    def __exit__(self, *a): self.out.close()
    def write(self, data):
        assert len(data) % 2 == 0
        self.out.write(data)
"""


@pytest.fixture
def portaudio(tmp_path, monkeypatch):
    (tmp_path / "sounddevice.py").write_text(FAKE_SOUNDDEVICE.replace("\\\\", "\\"))
    monkeypatch.setenv("PYTHONPATH", f"{tmp_path}:{':'.join(p for p in sys.path if p)}")
    monkeypatch.setenv("FAKE_READY", str(tmp_path / "ready"))
    monkeypatch.setenv("FAKE_PLAYED", str(tmp_path / "played"))
    monkeypatch.setattr(pcm, "_QUIET", {"stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE})
    return tmp_path


def test_recording_runs_until_told_to_stop_and_leaves_a_complete_wav(portaudio):
    path = portaudio / "ptt.wav"
    proc = pcm.record(path)
    for _ in range(300):
        if (portaudio / "ready").exists():
            break
        time.sleep(0.02)
    time.sleep(0.15)
    pcm.stop_recording(proc)
    assert proc.returncode == 0, proc.stderr.read().decode()
    with wave.open(str(path)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        frames = wav.readframes(wav.getnframes())
    assert len(frames) >= 320 * 5 and set(frames[::2]) == {0x10}


def test_a_recorder_that_ignores_the_stop_is_killed(monkeypatch):
    proc = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)"])
    time.sleep(0.3)
    monkeypatch.setattr(subprocess.Popen, "wait", lambda self, timeout=None, _wait=subprocess.Popen.wait: _wait(self, timeout=0.3 if timeout else None))
    pcm.stop_recording(proc)
    proc.wait()
    assert proc.returncode == -signal.SIGKILL


def test_the_player_plays_what_it_is_fed_at_the_rate_it_is_given(portaudio):
    proc = pcm.play_pcm(24000)
    samples = bytes(range(256)) * 40 + b"\x01"  # an odd byte at the very end is not a sample
    proc.stdin.write(samples)
    proc.stdin.close()
    assert proc.wait(timeout=10) == 0, proc.stderr.read().decode()
    played = (portaudio / "played").read_bytes()
    assert played.startswith(b"24000 1 int16\n")
    assert played.split(b"\n", 1)[1] == samples[:-1]


def test_run_with_nothing_to_do_it_explains_itself():
    r = subprocess.run([sys.executable, "-m", "tutor_click.platform.pcm"], capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "record PATH" in r.stderr
