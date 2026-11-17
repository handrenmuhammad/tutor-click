"""Per-step trace: one JSON line per look ("step") and one per performed action ("action").

The two lines of a step share an "id". Kept apart from the human log so runs can be counted and
compared. Bounded: at TRACE_MAX_BYTES the file moves to trace.jsonl.1, replacing the previous one.
"""
import json
import sys
import time

from . import config


def write(record: dict):
    path = config.TRACE_FILE
    try:
        if path.exists() and path.stat().st_size > config.TRACE_MAX_BYTES:
            path.replace(path.with_name(path.name + ".1"))
        with path.open("a") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **record}, separators=(",", ":")) + "\n")
    except OSError as e:  # a full disk must not stop a lesson
        print("trace:", e, file=sys.stderr)
