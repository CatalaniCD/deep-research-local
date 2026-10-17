"""Logging configuration + small helpers shared by server, CLI, and graphs.

The project logs in a light `key=value` style (no secrets). Nodes time their own
work with `timed()` so every stage is visible when LOG_LEVEL=DEBUG.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
import time

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: str | int | None = None) -> None:
    """Configure root logging once. Adds a handler only if none exists so we
    never duplicate uvicorn's output."""
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = level.upper()
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(handler)


def kv(**fields) -> str:
    """Render fields as a compact key=value string, single-line and truncated."""
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        text = " ".join(str(value).split())
        if len(text) > 200:
            text = text[:200] + "…"
        parts.append(f"{key}={text}")
    return " ".join(parts)


@contextlib.contextmanager
def timed(logger: logging.Logger, event: str, **fields):
    """Log `<event> start/done` around a block with elapsed seconds.

        with timed(logger, "node=llm_call", iteration=n):
            ...
    """
    start = time.perf_counter()
    logger.info("%s start %s", event, kv(**fields))
    try:
        yield
    except Exception:
        logger.exception("%s error elapsed=%.2fs %s", event, time.perf_counter() - start, kv(**fields))
        raise
    else:
        logger.info("%s done elapsed=%.2fs %s", event, time.perf_counter() - start, kv(**fields))
