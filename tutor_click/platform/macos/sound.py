"""Microphone and speaker on macOS: PortAudio child processes, and the system voice as the fallback."""
import subprocess

from ..pcm import play_pcm, record, stop_recording

__all__ = ["play_pcm", "record", "say", "stop_recording"]


def say(text: str) -> subprocess.Popen:
    """The fallback voice, for when the Kokoro model files are missing."""
    return subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
