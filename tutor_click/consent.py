"""Which providers the user has agreed to send screenshots to. Kept across restarts."""
import json

from . import config


def allowed() -> set[str]:
    """The provider the app was configured to start with counts: setting it was the user's choice."""
    try:
        saved = set(json.loads(config.CONSENT_FILE.read_text())["allowed"])
    except (OSError, ValueError, KeyError, TypeError):
        saved = set()
    return saved | {config.BACKEND}


def allow(provider_id: str):
    config.CONSENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.CONSENT_FILE.write_text(json.dumps({"allowed": sorted(allowed() | {provider_id})}))
