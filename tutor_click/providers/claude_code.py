"""Headless Claude Code (`claude -p`) on the user's own claude.ai sign-in. No API key."""
import base64
import json
import shutil
import subprocess
import threading
from pathlib import Path

from .. import config, settings
from .base import Cli, ProviderError, classify

# Ids and effort levels from Anthropic's model list, read 2026-11-17. Every one of these models takes all five levels.
MODELS = (("claude-fable-5-2", "Fable 5.2"), ("claude-opus-5-5", "Opus 5.5"), ("claude-sonnet-5-5", "Sonnet 5.5"), ("claude-haiku-5-5", "Haiku 5.5"))
EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ClaudeCode(Cli):
    id = "claude-code"
    name = "Claude"
    menu = "Claude · subscription"
    billing = "subscription"
    paid_by = "your Claude subscription"
    sends_to = "Anthropic"
    models, efforts, model_env = MODELS, EFFORTS, "TUTOR_CLICK_MODEL"

    def __init__(self):
        self.model = settings.start(self.id, "model", "TUTOR_CLICK_MODEL", config.MODEL)
        self.effort = settings.start(self.id, "effort", "TUTOR_CLICK_EFFORT", config.EFFORT)
        # The command for the next step, started as soon as this one is answered and waiting for its
        # input. Measured: 3.0 s per step against 3.9 s when it is started on demand. Each process
        # still answers one question and exits, so nothing carries over from step to step.
        self._ready: tuple[list[str], subprocess.Popen] | None = None
        self._lock = threading.Lock()

    def _exe(self) -> str:
        # A desktop launcher may not have ~/.local/bin on PATH.
        return shutil.which("claude") or str(Path.home() / ".local/bin/claude")

    def health(self) -> str | None:
        return None if Path(self._exe()).exists() else "the claude command is not installed"

    def close(self):
        """Stop the process that was started ahead, if any."""
        with self._lock:
            ready, self._ready = self._ready, None
        if ready:
            ready[1].kill()
            ready[1].wait()

    def abort(self) -> None:
        super().abort()
        self.close()  # the next step may be a different one

    def _take(self, cmd: list[str]) -> tuple[subprocess.Popen | None, bool]:
        """The process started ahead, if it is for this exact command and still alive."""
        with self._lock:
            ready, self._ready = self._ready, None
        if ready and ready[0] == cmd and ready[1].poll() is None:
            return ready[1], True
        if ready:
            ready[1].kill()
            ready[1].wait()
        return None, False

    def _prepare(self, cmd: list[str]):
        try:
            proc = self._start(cmd, config.RUNTIME)
        except (ProviderError, OSError):
            return  # the next step starts it on demand and reports the problem
        with self._lock:
            self._ready = (cmd, proc)

    def ask(self, system: str, schema: dict, jpeg: bytes, prompt: str) -> dict:
        cmd = [
            self._exe(), "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--json-schema", json.dumps(schema),
            "--system-prompt", system,
            "--tools", "",  # no file/shell access: it only looks and answers
            "--strict-mcp-config", "--setting-sources", "",  # no MCP servers, no user hooks
            "--no-session-persistence",
            "--model", self.model, "--effort", self.effort,
        ]
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(jpeg).decode()}}
        msg = {"type": "user", "message": {"role": "user", "content": [image, {"type": "text", "text": prompt}]}}
        ahead, was_ahead = self._take(cmd)
        p = self._run(cmd, json.dumps(msg) + "\n", config.RUNTIME, proc=ahead)
        if was_ahead and p.returncode != 0 and not p.stdout.strip():  # it died while it waited: ask a fresh one
            p = self._run(cmd, json.dumps(msg) + "\n", config.RUNTIME)
        self._prepare(cmd)
        result = None
        for line in p.stdout.splitlines():
            if line.startswith("{"):
                event = json.loads(line)
                if event.get("type") == "result":
                    result = event
        if result is None or result.get("is_error") or not result.get("structured_output"):
            detail = (result or {}).get("result") or p.stderr.strip() or f"exit {p.returncode}"
            raise ProviderError("Claude Code could not answer.", classify(detail)) from RuntimeError(detail[:500])
        return result["structured_output"]
