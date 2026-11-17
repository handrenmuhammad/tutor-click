"""What every AI backend looks like to the tutor.

A provider turns (system prompt, JSON schema, screenshot, prompt) into one JSON object. It keeps no
lesson state: the prompt, history, validation and safety checks live in brain.py and daemon.py, so
a lesson can move from one provider to another.
"""
import subprocess
from typing import Protocol


class ProviderError(Exception):
    """A step failed in a way worth telling the user out loud.

    code: not_installed, needs_login, rate_limited, timeout, invalid_answer, declined, offline,
    unknown or failed.
    """

    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


class Provider(Protocol):
    id: str  # what TUTOR_CLICK_BACKEND and `tutor-click backend` call it
    name: str  # as spoken, e.g. "Codex"
    menu: str  # as listed in the panel, e.g. "Codex · ChatGPT plan"
    billing: str  # "subscription", "api_key" or "local": who pays for a step
    paid_by: str  # the same in words, e.g. "your ChatGPT plan"
    sends_to: str  # the company that receives the screenshots
    model: str  # what is asked for; "" means the client's default. Changed with `tutor-click model`
    effort: str  # how hard it thinks; "" where the provider has no such setting
    models: tuple  # (id, name) of each model offered in the panel; () = only the one from the environment
    efforts: tuple  # effort levels offered; () = none
    model_env: str  # the environment variable that sets the model

    def ask(self, system: str, schema: dict, jpeg: bytes, prompt: str) -> dict:
        """One answer matching schema. Raises ProviderError."""

    def abort(self) -> None:
        """Stop a call that is in flight (the user interrupted)."""

    def health(self) -> str | None:
        """None if this provider can be used right now, else the reason in a few words."""


def classify(text: str) -> str:
    """Best guess at why a command-line client failed, from what it printed.

    These patterns are guesses from the clients' wording, not a documented contract: neither a
    real usage-limit nor a logged-out failure has been observed here yet.
    """
    t = text.lower()
    if any(k in t for k in ("usage limit", "rate limit", "rate_limit", "429", "quota")):
        return "rate_limited"
    if any(k in t for k in ("not logged in", "please log in", "login required", "/login", "401", "unauthorized", "authentication")):
        return "needs_login"
    if any(k in t for k in ("network", "connection", "dns", "timed out", "unreachable")):
        return "offline"
    return "failed"


class Cli:
    """Shared by the providers that shell out to an official client the user is signed in to."""

    name = ""
    _proc: subprocess.Popen | None = None

    def abort(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def _run(self, cmd: list[str], stdin: str, cwd, timeout: int = 120) -> subprocess.CompletedProcess:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
        except FileNotFoundError as e:
            raise ProviderError(f"The {cmd[0].rsplit('/', 1)[-1]} command is not installed.", "not_installed") from e
        self._proc = proc
        try:
            out, err = proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            proc.communicate()
            raise ProviderError(f"{self.name} took too long to answer.", "timeout") from e
        finally:
            self._proc = None
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
