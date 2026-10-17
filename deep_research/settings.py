"""Runtime settings: per-node models, timeout, caps, rate limit, Tavily params.

Single source of truth for tunables, editable live from the UI without a restart.

Precedence (low -> high):
    builtin defaults  <  environment (.env)  <  settings.json  <  runtime PUT /settings

The pipeline reads ``get_settings()`` at call time (see graph.py / utils.py), so
model / cap / Tavily / rate-limit changes apply to the next run. Runtime edits are
persisted to ``settings.json`` (gitignored) so they survive a restart.

Env:
    SCOPE_MODEL / RESEARCH_MODEL / SUPERVISOR_MODEL / SUMMARY_MODEL /
    COMPRESS_MODEL / WRITER_MODEL   — per-node model ids (fall back to MODEL).
    MODEL                           — global default model id.
    MODEL_TIMEOUT                   — per-call LLM timeout in seconds (fail fast).
    MAX_TOOL_ITERATIONS             — researcher tool-call iterations before compress.
    MAX_RESEARCHER_ITERATIONS       — supervisor delegations before forcing completion.
    MAX_CONCURRENT_RESEARCHERS      — parallel researcher sub-agents per round.
    RATE_LIMIT_RPM                  — outbound LLM requests/minute (0 disables).
    TAVILY_MAX_RESULTS              — results per search query.
    TAVILY_SEARCH_DEPTH             — "basic" | "advanced".
    TAVILY_INCLUDE_RAW              — include raw page content (true -> per-page summaries).
    SETTINGS_FILE                   — override the settings.json path.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

logger = logging.getLogger("deep_research.settings")

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = Path(os.getenv("SETTINGS_FILE") or (ROOT / "settings.json"))
# Secrets are kept out of Settings/settings.json so they are never returned by
# GET /settings or written to logs. Override the path with SECRETS_FILE.
SECRETS_PATH = Path(os.getenv("SECRETS_FILE") or (ROOT / ".secrets.json"))

DEFAULT_MODEL = "liquid/lfm-2.5-2.6b:free"
ROLES = ("scope", "research", "supervisor", "summary", "compress", "writer")

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Tunables surfaced in the UI Settings panel and read by the pipeline."""

    # Per-node models.
    scope_model: str = DEFAULT_MODEL
    research_model: str = DEFAULT_MODEL
    supervisor_model: str = DEFAULT_MODEL
    summary_model: str = DEFAULT_MODEL
    compress_model: str = DEFAULT_MODEL
    writer_model: str = DEFAULT_MODEL

    # Per-call LLM timeout in seconds (None -> no explicit timeout).
    model_timeout: float | None = None

    # Research depth caps.
    max_tool_iterations: int = 4
    max_researcher_iterations: int = 4
    max_concurrent_researchers: int = 2

    # Outbound LLM requests per minute (0 disables).
    rate_limit_rpm: float = 18.0

    # Tavily search parameters.
    tavily_max_results: int = 3
    tavily_search_depth: str = "basic"
    tavily_include_raw: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _env_bool(environ, name: str, default: bool) -> bool:
    v = environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in _TRUTHY


def _env_float(environ, name: str, default):
    v = environ.get(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        logger.warning("settings=bad_env %s=%r using=%s", name, v, default)
        return default


def _env_int(environ, name: str, default):
    v = environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        logger.warning("settings=bad_env %s=%r using=%s", name, v, default)
        return default


def build_settings(environ=None, file_data: dict | None = None) -> Settings:
    """Build a Settings from environment then overlay persisted file data."""
    environ = os.environ if environ is None else environ
    default_model = environ.get("MODEL") or DEFAULT_MODEL
    s = Settings(
        scope_model=environ.get("SCOPE_MODEL") or default_model,
        research_model=environ.get("RESEARCH_MODEL") or default_model,
        supervisor_model=environ.get("SUPERVISOR_MODEL") or default_model,
        summary_model=environ.get("SUMMARY_MODEL") or default_model,
        compress_model=environ.get("COMPRESS_MODEL") or default_model,
        writer_model=environ.get("WRITER_MODEL") or default_model,
        model_timeout=_env_float(environ, "MODEL_TIMEOUT", None),
        max_tool_iterations=_env_int(environ, "MAX_TOOL_ITERATIONS", 4),
        max_researcher_iterations=_env_int(environ, "MAX_RESEARCHER_ITERATIONS", 4),
        max_concurrent_researchers=_env_int(environ, "MAX_CONCURRENT_RESEARCHERS", 2),
        rate_limit_rpm=_env_float(environ, "RATE_LIMIT_RPM", 18.0),
        tavily_max_results=_env_int(environ, "TAVILY_MAX_RESULTS", 3),
        tavily_search_depth=environ.get("TAVILY_SEARCH_DEPTH") or "basic",
        tavily_include_raw=_env_bool(environ, "TAVILY_INCLUDE_RAW", False),
    )
    if file_data:
        _apply(s, file_data, source="file")
    return s


def _coerce(name: str, current, raw):
    if name == "model_timeout":
        if raw is None or raw == "":
            return None
        return float(raw)
    if isinstance(current, bool):
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in _TRUTHY
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return str(raw)


def _apply(s: Settings, data: dict, source: str = "update") -> list[str]:
    """Apply known fields from ``data`` onto ``s`` (typed); return changed field names."""
    valid = {f.name for f in fields(Settings)}
    changed: list[str] = []
    for name, raw in data.items():
        if name not in valid:
            logger.warning("settings=ignore_unknown field=%s source=%s", name, source)
            continue
        current = getattr(s, name)
        try:
            value = _coerce(name, current, raw)
        except (TypeError, ValueError):
            logger.warning("settings=invalid field=%s value=%r source=%s", name, raw, source)
            continue
        if value != current:
            setattr(s, name, value)
            changed.append(name)
    return changed


_settings: Settings | None = None


def get_settings() -> Settings:
    """Current settings singleton (loads on first use)."""
    global _settings
    if _settings is None:
        return init_settings()
    return _settings


def init_settings(environ=None) -> Settings:
    """Load settings from env + optional settings.json and apply runtime side effects."""
    global _settings
    _load_secrets()
    file_data = None
    if SETTINGS_PATH.exists():
        try:
            file_data = json.loads(SETTINGS_PATH.read_text())
            logger.info("settings=load path=%s", SETTINGS_PATH)
        except Exception:
            logger.exception("settings=load_error path=%s", SETTINGS_PATH)
    _settings = build_settings(environ, file_data)
    _apply_runtime(_settings)
    logger.info("settings=init %s", _settings.to_dict())
    return _settings


def update_settings(data: dict) -> Settings:
    """Apply a partial update, persist it, and push side effects (rate limiter)."""
    s = get_settings()
    changed = _apply(s, data, source="api")
    _save(s)
    _apply_runtime(s)
    logger.info("settings=update changed=%s", changed)
    return s


def _save(s: Settings) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(s.to_dict(), indent=2) + "\n")
    except Exception:
        logger.exception("settings=save_error path=%s", SETTINGS_PATH)


def _apply_runtime(s: Settings) -> None:
    from .rate_limit import limiter

    limiter.set_rpm(s.rate_limit_rpm)


# ===== SECRETS (API keys) =====
# The value is NEVER logged, never returned by GET /settings, and never written
# to settings.json. It lives only in the process env + .secrets.json (gitignored).


def _load_secrets() -> None:
    if not SECRETS_PATH.exists():
        return
    try:
        data = json.loads(SECRETS_PATH.read_text())
    except Exception:
        logger.exception("settings=secrets_load_error path=%s", SECRETS_PATH)
        return
    key = data.get("openrouter_api_key")
    if key and not os.getenv("OPENROUTER_API_KEY"):
        os.environ["OPENROUTER_API_KEY"] = key
        logger.info("settings=secrets_loaded field=openrouter_api_key set=true")


def openrouter_key_set() -> bool:
    """True if an OpenRouter key is configured (value is never exposed)."""
    return bool(os.getenv("OPENROUTER_API_KEY"))


def set_openrouter_api_key(key: str) -> None:
    """Set the OpenRouter key in the env + .secrets.json and reset cached models."""
    key = (key or "").strip()
    if not key:
        return
    os.environ["OPENROUTER_API_KEY"] = key
    data = {}
    if SECRETS_PATH.exists():
        try:
            data = json.loads(SECRETS_PATH.read_text())
        except Exception:
            data = {}
    data["openrouter_api_key"] = key
    try:
        SECRETS_PATH.write_text(json.dumps(data, indent=2) + "\n")
    except Exception:
        logger.exception("settings=secrets_save_error path=%s", SECRETS_PATH)
    from . import models

    models.clear_cache()
    logger.info("settings=update field=openrouter_api_key set=true")
