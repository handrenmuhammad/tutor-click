"""The model and effort picked for each provider. Kept across restarts.

Order, per provider: the environment variable if it is set, else what was picked last, else the
default. So a launcher start remembers the panel, and `TUTOR_CLICK_MODEL=... tutor-click` still wins.
"""
import json
import os

from . import config


def _saved() -> dict:
    try:
        data = json.loads(config.SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def start(provider_id: str, key: str, env: str, from_env_or_default: str) -> str:
    """key is "model" or "effort"; from_env_or_default is what config read for env."""
    if env in os.environ:
        return from_env_or_default
    picked = _saved().get(provider_id, {})
    return picked.get(key) or from_env_or_default if isinstance(picked, dict) else from_env_or_default


def save(provider_id: str, key: str, value: str):
    data = _saved()
    data.setdefault(provider_id, {})[key] = value
    config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_FILE.write_text(json.dumps(data, indent=1))
