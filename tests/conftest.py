import pytest

from tutor_click import config


@pytest.fixture(autouse=True)
def _state_in_tmp(tmp_path, monkeypatch):
    """Tests never read or write the real log, trace, consent file, settings or lesson records."""
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "log")
    monkeypatch.setattr(config, "TRACE_FILE", tmp_path / "trace.jsonl")
    monkeypatch.setattr(config, "CONSENT_FILE", tmp_path / "providers.json")
    monkeypatch.setattr(config, "LESSONS_DIR", tmp_path / "lessons")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    for name in ("TUTOR_CLICK_MODEL", "TUTOR_CLICK_EFFORT", "TUTOR_CLICK_CODEX_EFFORT"):
        monkeypatch.delenv(name, raising=False)
