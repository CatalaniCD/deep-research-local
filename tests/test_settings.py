"""Runtime settings tests (no network). See deep_research/settings.py."""
import json
import os

from deep_research import settings


def test_build_from_env_overrides_and_defaults():
    env = {
        "MODEL": "base/model",
        "SCOPE_MODEL": "scope/model",
        "MODEL_TIMEOUT": "60",
        "MAX_TOOL_ITERATIONS": "7",
        "RATE_LIMIT_RPM": "5",
        "TAVILY_MAX_RESULTS": "9",
        "TAVILY_SEARCH_DEPTH": "advanced",
        "TAVILY_INCLUDE_RAW": "true",
    }
    s = settings.build_settings(environ=env)
    assert s.scope_model == "scope/model"
    assert s.research_model == "base/model"
    assert s.model_timeout == 60.0
    assert s.max_tool_iterations == 7
    assert s.rate_limit_rpm == 5.0
    assert s.tavily_max_results == 9
    assert s.tavily_search_depth == "advanced"
    assert s.tavily_include_raw is True


def test_defaults_when_env_empty():
    s = settings.build_settings(environ={})
    assert s.scope_model == settings.DEFAULT_MODEL
    assert s.model_timeout is None
    assert s.rate_limit_rpm == 18.0
    assert s.tavily_include_raw is False
    assert s.tavily_search_depth == "basic"


def test_file_overlay_replaces_and_coerces_unknown_ignored():
    s = settings.build_settings(environ={"MODEL": "base/model"}, file_data={
        "max_tool_iterations": "9",
        "rate_limit_rpm": "0",
        "tavily_include_raw": "yes",
        "model_timeout": "30",
        "unknown_key": 1,
    })
    assert s.max_tool_iterations == 9
    assert s.rate_limit_rpm == 0.0
    assert s.tavily_include_raw is True
    assert s.model_timeout == 30.0
    assert not hasattr(s, "unknown_key")


def test_model_timeout_blank_is_none():
    s = settings.build_settings(environ={}, file_data={"model_timeout": ""})
    assert s.model_timeout is None


def test_invalid_value_keeps_current():
    s = settings.build_settings(environ={}, file_data={"max_tool_iterations": "not-a-number"})
    assert s.max_tool_iterations == 4


def test_update_persists_and_applies(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_settings", settings.build_settings(environ={}))
    updated = settings.update_settings({"writer_model": "w/model", "rate_limit_rpm": 3})
    assert updated.writer_model == "w/model"
    assert settings.get_settings().rate_limit_rpm == 3.0
    assert "w/model" in (tmp_path / "settings.json").read_text()

    from deep_research.rate_limit import limiter
    assert limiter.rpm == 3.0


def test_api_key_stored_but_never_exposed(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SECRETS_PATH", tmp_path / ".secrets.json")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert settings.openrouter_key_set() is False
    settings.set_openrouter_api_key("sk-or-secret-value")
    assert settings.openrouter_key_set() is True
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-secret-value"
    # The key must never appear in the public settings payload.
    assert "sk-or-secret-value" not in json.dumps(settings.get_settings().to_dict())
    assert "sk-or-secret-value" in (tmp_path / ".secrets.json").read_text()


def test_blank_api_key_is_ignored(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings.set_openrouter_api_key("   ")
    assert settings.openrouter_key_set() is False
