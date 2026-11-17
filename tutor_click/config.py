import os
from pathlib import Path

# "claude-code": headless Claude Code CLI on your claude.ai subscription (no API key).
# "codex": headless Codex CLI on your ChatGPT plan (no API key).
# "local": a model server on this computer that speaks the OpenAI API (Ollama, LM Studio). Free.
# "api": Anthropic API with ANTHROPIC_API_KEY / ant auth login. Billed per use.
# "openai": OpenAI API with OPENAI_API_KEY. Billed per use, separate from a ChatGPT plan.
# "gemini": Gemini API with GEMINI_API_KEY. Billed by Google.
# This is the provider the app starts with; `tutor-click backend NAME` or the panel switches while it runs.
BACKEND = os.environ.get("TUTOR_CLICK_BACKEND", "claude-code")
MODEL = os.environ.get("TUTOR_CLICK_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("TUTOR_CLICK_EFFORT", "medium")  # low is faster, medium more careful
# Codex: "" uses the Codex client's own default model (gpt-6.2-sol on 2026-11-17). Effort: low, medium, high, xhigh, max.
CODEX_MODEL = os.environ.get("TUTOR_CLICK_CODEX_MODEL", "")
CODEX_EFFORT = os.environ.get("TUTOR_CLICK_CODEX_EFFORT", "low")
OPENAI_MODEL = os.environ.get("TUTOR_CLICK_OPENAI_MODEL", "gpt-6.2-astra")  # what OpenAI's guide starts new projects on
OPENAI_URL = os.environ.get("TUTOR_CLICK_OPENAI_URL", "https://api.openai.com/v1")
GEMINI_MODEL = os.environ.get("TUTOR_CLICK_GEMINI_MODEL", "")  # no default: set the model you have access to
GEMINI_URL = os.environ.get("TUTOR_CLICK_GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
LOCAL_MODEL = os.environ.get("TUTOR_CLICK_LOCAL_MODEL", "")  # a vision model you have pulled
LOCAL_URL = os.environ.get("TUTOR_CLICK_LOCAL_URL", "http://localhost:11434/v1")  # Ollama's default
# Providers the user has agreed to send screenshots to (see daemon._switch).
CONSENT_FILE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "tutor-click" / "providers.json"
# Longest edge of the screenshot sent to the model, in pixels.
MAX_EDGE = int(os.environ.get("TUTOR_CLICK_MAX_EDGE", "1568"))
# How many past exchanges are replayed as text each step (bounded).
HISTORY_TURNS = 8
# How many of the user's own requests stay pinned (so the goal survives long auto runs).
ASKS_KEPT = 5
WHISPER_MODEL = os.environ.get("TUTOR_CLICK_WHISPER", "base.en")
# Kokoro neural TTS; falls back to espeak-ng when the model files are missing.
KOKORO_DIR = Path.home() / ".local/share/tutor-click/kokoro"
VOICE = os.environ.get("TUTOR_CLICK_VOICE", "af_heart")  # e.g. am_michael, bf_emma, bm_george
VOICE_SPEED = float(os.environ.get("TUTOR_CLICK_VOICE_SPEED", "1.05"))

# Tutor-clicks mode: how many actions in a row before it pauses and asks to continue (bounded).
AUTO_MAX_STEPS = int(os.environ.get("TUTOR_CLICK_AUTO_MAX_STEPS", "12"))
# Window classes the tutor never clicks or types into: typing into a shell or an AI agent runs commands.
NEVER_ACT = tuple(
    c.strip().lower()
    for c in os.environ.get(
        "TUTOR_CLICK_NEVER_ACT",
        "kitty,foot,alacritty,wezterm,ghostty,konsole,xterm,gnome-terminal,com.anthropic.claude,claude",
    ).split(",")
    if c.strip()
)
# Global hotkeys registered in Hyprland while the app runs (removed on quit). "0" disables.
BINDS = os.environ.get("TUTOR_CLICK_BINDS", "1") != "0"
LOG_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "tutor-click" / "log"

# One JSON line per step and per action (see trace.py). Rotated once at this size.
TRACE_FILE = LOG_FILE.parent / "trace.jsonl"
TRACE_MAX_BYTES = 5_000_000
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "tutor-click"
SOCKET = RUNTIME / "ctl.sock"
LAYER_SHELL_LIB = "/usr/lib/libgtk4-layer-shell.so"
