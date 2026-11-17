import pytest

from tutor_click import config


@pytest.fixture(autouse=True)
def _state_in_tmp(tmp_path, monkeypatch):
    """Tests never write to the real log, trace, consent file or lesson records."""
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "log")
    monkeypatch.setattr(config, "TRACE_FILE", tmp_path / "trace.jsonl")
    monkeypatch.setattr(config, "CONSENT_FILE", tmp_path / "providers.json")
    monkeypatch.setattr(config, "LESSONS_DIR", tmp_path / "lessons")
