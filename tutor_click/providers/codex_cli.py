"""Headless Codex (`codex exec`) on the user's own ChatGPT sign-in. No API key.

Codex is a coding agent, so it is boxed in: read-only sandbox, an empty working directory, the
user's Codex config (MCP servers, plugins) ignored, and its app, plugin, browser and computer-use
features off. It has no system-prompt flag, so the tutor's instructions lead the prompt.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import config, settings
from .base import Cli, ProviderError, classify

OFF = ("apps", "plugins", "browser_use", "computer_use")


class CodexCLI(Cli):
    id = "codex"
    name = "Codex"
    menu = "Codex · ChatGPT plan"
    billing = "subscription"
    paid_by = "your ChatGPT plan"
    sends_to = "OpenAI"
    models = ()  # Codex's own default, or the one named in TUTOR_CLICK_CODEX_MODEL: its model names are not listed here
    efforts = ("low", "medium", "high", "xhigh", "max")
    model_env = "TUTOR_CLICK_CODEX_MODEL"

    def __init__(self):
        self.model = config.CODEX_MODEL
        self.effort = settings.start(self.id, "effort", "TUTOR_CLICK_CODEX_EFFORT", config.CODEX_EFFORT)

    def _exe(self) -> str:
        return shutil.which("codex") or str(Path.home() / ".local/bin/codex")

    def health(self) -> str | None:
        if not Path(self._exe()).exists():
            return "the codex command is not installed"
        r = subprocess.run([self._exe(), "login", "status"], capture_output=True, text=True, check=False)
        return None if r.returncode == 0 else "Codex is not signed in (run: codex login)"

    def ask(self, system: str, schema: dict, jpeg: bytes, prompt: str) -> dict:
        root = config.RUNTIME / "codex"  # in XDG_RUNTIME_DIR: memory-backed and private to this user
        root.mkdir(parents=True, exist_ok=True)
        # One folder per call, removed afterwards: calls can overlap, and the screenshot does not outlive its call.
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            work = Path(tmp)
            empty = work / "cwd"
            empty.mkdir()
            shot, schema_file, answer = work / "shot.jpg", work / "schema.json", work / "answer.json"
            shot.write_bytes(jpeg)
            schema_file.write_text(json.dumps(schema))
            cmd = [self._exe(), "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                   "-s", "read-only", "-C", str(empty), "-i", str(shot),
                   "--output-schema", str(schema_file), "-o", str(answer),
                   "-c", f'model_reasoning_effort="{self.effort}"']
            for feature in OFF:
                cmd += ["--disable", feature]
            if self.model:
                cmd += ["-m", self.model]
            cmd.append("-")  # prompt on stdin
            full = f"{system}\n\nAnswer with the JSON object only. Do not run commands or read files.\n\n{prompt}"
            p = self._run(cmd, full, empty)
            if p.returncode != 0 or not answer.exists():
                detail = p.stderr.strip() or f"exit {p.returncode}"
                raise ProviderError("Codex could not answer.", classify(detail)) from RuntimeError(detail[-500:])
            try:
                return json.loads(answer.read_text())
            except json.JSONDecodeError as e:
                raise ProviderError("Codex did not answer in the expected form.", "invalid_answer") from e
