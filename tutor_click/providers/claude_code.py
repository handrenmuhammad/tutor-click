"""Headless Claude Code (`claude -p`) on the user's own claude.ai sign-in. No API key."""
import base64
import json
import shutil
from pathlib import Path

from .. import config
from .base import Cli, ProviderError, classify


class ClaudeCode(Cli):
    id = "claude-code"
    name = "Claude"
    menu = "Claude · subscription"
    billing = "subscription"
    paid_by = "your Claude subscription"
    sends_to = "Anthropic"

    def __init__(self):
        self.model = config.MODEL

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
            "--model", config.MODEL, "--effort", config.EFFORT,
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
