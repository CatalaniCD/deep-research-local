"""Central model factory. Supports OpenRouter (OpenAI-compatible) + legacy direct providers.

Env:
  OPENROUTER_API_KEY      — if set, ALL models route via OpenRouter base URL.
  OPENROUTER_BASE_URL     — default https://openrouter.ai/api/v1
  MODEL                   — global default model id (e.g. liquid/lfm-2.5-2.6b:free)
  RESEARCH_MODEL / SUPERVISOR_MODEL / SCOPE_MODEL / SUMMARY_MODEL / COMPRESS_MODEL / WRITER_MODEL
                          — per-role overrides; fall back to MODEL then builtin defaults.
  OPENROUTER_SITE_URL / OPENROUTER_APP_NAME — optional ranking headers.
  OPENAI_API_KEY / ANTHROPIC_API_KEY — used only when OPENROUTER_API_KEY is absent.
"""
from __future__ import annotations
import logging
import os

log = logging.getLogger("deep_research.models")

def create_default_models(model="liquid/lfm-2.5-2.6b:free"):
    return {k: model for k in ("research", "supervisor", "scope", "summary", "compress", "writer")}

def create_baseline_models():
    return {
       "research": "anthropic/claude-sonnet-4",
       "supervisor": "anthropic/claude-sonnet-4",
       "scope": "openai/gpt-4.1",
       "summary": "openai/gpt-4.1-mini",
       "compress": "openai/gpt-4.1",
       "writer": "openai/gpt-4.1",
    }

_DEFAULTS = create_default_models()

logging.info(f"Created Models: {_DEFAULTS}")

_ROLE_ENV = {
    "research": "RESEARCH_MODEL",
    "supervisor": "SUPERVISOR_MODEL",
    "scope": "SCOPE_MODEL",
    "summary": "SUMMARY_MODEL",
    "compress": "COMPRESS_MODEL",
    "writer": "WRITER_MODEL",
}


def model_name_for(role: str) -> str:
    name = os.getenv(_ROLE_ENV[role]) or os.getenv("MODEL") or _DEFAULTS[role]
    # Allow legacy "provider:model" form in MODEL (e.g. "openai:gpt-4.1") — strip provider for OpenRouter.
    if os.getenv("OPENROUTER_API_KEY") and ":" in name and "/" not in name:
        name = name.split(":", 1)[1]
    return name


def describe(model) -> str:
    """Best-effort model id across ChatOpenAI / ChatAnthropic / other classes."""
    for attr in ("model_name", "model", "model_id"):
        v = getattr(model, attr, None)
        if isinstance(v, str) and v:
            return v
    try:
        return type(model).__name__
    except Exception:
        return "unknown"


def short(text, n: int = 120) -> str:
    """Single-line truncation for log fields (queries, topics, reflections)."""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[:n] + "…"


def init_model(role: str, **kwargs):
    """Return a chat model for role. Routes via OpenRouter when configured."""
    name = model_name_for(role)
    if os.getenv("MODEL_TIMEOUT") and "request_timeout" not in kwargs:
        kwargs["request_timeout"] = float(os.environ["MODEL_TIMEOUT"])
    key = os.getenv("OPENROUTER_API_KEY")
    if key:
        from langchain_openai import ChatOpenAI

        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        headers = {}
        if os.getenv("OPENROUTER_SITE_URL"):
            headers["HTTP-Referer"] = os.environ["OPENROUTER_SITE_URL"]
        if os.getenv("OPENROUTER_APP_NAME"):
            headers["X-Title"] = os.environ["OPENROUTER_APP_NAME"]
        m = ChatOpenAI(
            model=name,
            openai_api_key=key,
            openai_api_base=base_url,
            default_headers=headers or None,
            **kwargs,
        )
        log.info("model_init role=%s model=%s via=openrouter base=%s timeout=%s", role, name, base_url,
                 kwargs.get("request_timeout", "none"))
        return m
    # Legacy: direct providers (needs OPENAI_API_KEY / ANTHROPIC_API_KEY).
    # Ensure a provider prefix for init_chat_model. Only keep the name as-is
    # when it already carries an explicit init_chat_model provider prefix
    # (e.g. "openai:gpt-4.1"); OpenRouter-style ids ("vendor/model", with or
    # without ":variant") and bare ids go via the openai: client.
    from langchain.chat_models import init_chat_model

    _KNOWN_PROVIDERS = {"openai", "anthropic", "google_genai", "google_vertexai", "groq",
                        "mistralai", "deepseek", "together", "fireworks", "ollama", "xai"}
    prefix = name.split(":")[0] if ":" in name else ""
    legacy_name = name if ("/" not in name and prefix in _KNOWN_PROVIDERS) else f"openai:{name}"
    m = init_chat_model(model=legacy_name, **kwargs)
    log.info("model_init role=%s model=%s via=direct", role, legacy_name)
    return m
