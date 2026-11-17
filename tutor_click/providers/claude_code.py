"""Headless Claude Code (`claude -p`) on the user's own claude.ai sign-in. No API key."""
import base64
import json
import shutil
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

    def _exe(self) -> str:
        # A desktop launcher may not have ~/.local/bin on PATH.
        return shutil.which("claude") or str(Path.home() / ".local/bin/claude")

    def health(self) -> str | None:
        return None if Path(self._exe()).exists() else "the claude command is not installed"

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
        p = self._run(cmd, json.dumps(msg) + "\n", config.RUNTIME)
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
