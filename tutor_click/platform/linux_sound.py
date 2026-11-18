"""Microphone and speaker through PipeWire's and PulseAudio's command-line tools."""
import signal
import subprocess
from pathlib import Path

_QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def record(path: Path) -> subprocess.Popen:
    """Record the microphone to a 16 kHz mono WAV until stop_recording()."""
    return subprocess.Popen(["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", str(path)], **_QUIET)


def stop_recording(proc: subprocess.Popen):
    proc.send_signal(signal.SIGINT)  # lets pw-record finalize the WAV header
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def play_pcm(rate: int) -> subprocess.Popen:
    """A player that takes raw 16-bit mono samples on its stdin."""
    return subprocess.Popen(["paplay", "--raw", f"--rate={rate}", "--channels=1", "--format=s16le"], stdin=subprocess.PIPE, **_QUIET)


def say(text: str) -> subprocess.Popen:
    """The fallback voice, for when the Kokoro model files are missing."""
    return subprocess.Popen(["espeak-ng", "-v", "en-us", "-s", "165", text], **_QUIET)
