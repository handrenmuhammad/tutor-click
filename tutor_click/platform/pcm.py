"""Microphone and speaker through PortAudio (the sounddevice package), each as a small child process.

    python -m tutor_click.platform.pcm record PATH    16 kHz mono WAV until it gets SIGINT
    python -m tutor_click.platform.pcm play RATE      raw 16-bit mono samples from stdin

For the desktops with no command-line recorder and player of their own (macOS, Windows).
"""
import signal
import subprocess
import sys
import threading
import wave
from pathlib import Path

RECORD_RATE = 16000
_QUIET = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def record(path: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "tutor_click.platform.pcm", "record", str(path)], **_QUIET)


def stop_recording(proc: subprocess.Popen):
    proc.send_signal(signal.SIGINT)  # the child closes the WAV file and exits
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def play_pcm(rate: int) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "tutor_click.platform.pcm", "play", str(rate)], stdin=subprocess.PIPE, **_QUIET)


def _record(path: str):
    import sounddevice

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RECORD_RATE)
        with sounddevice.RawInputStream(samplerate=RECORD_RATE, channels=1, dtype="int16",
                                        callback=lambda data, _frames, _time, _status: wav.writeframes(bytes(data))):
            while not stop.wait(0.1):  # a short wait, so the signal handler gets its turn
                pass


def _play(rate: int):
    import sounddevice

    with sounddevice.RawOutputStream(samplerate=rate, channels=1, dtype="int16") as out:
        while chunk := sys.stdin.buffer.read(4096):
            out.write(chunk[: len(chunk) & ~1])  # whole 16-bit samples only


if __name__ == "__main__":
    if sys.argv[1:2] == ["record"] and len(sys.argv) == 3:
        _record(sys.argv[2])
    elif sys.argv[1:2] == ["play"] and len(sys.argv) == 3:
        _play(int(sys.argv[2]))
    else:
        sys.exit(__doc__)
