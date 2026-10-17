"""Outbound request rate limiter (provider RPM guard).

A single shared sliding-window limiter caps outbound LLM calls to
`RATE_LIMIT_RPM` requests per minute (default 18). Every model invocation in
graph.py / utils.py awaits `limiter.acquire()` first, so concurrent researcher
fan-out is throttled globally instead of hammering the provider.

Set `RATE_LIMIT_RPM=0` (or a negative value) to disable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque

logger = logging.getLogger("deep_research.ratelimit")


def default_rpm() -> float:
    """RPM from env (RATE_LIMIT_RPM), default 18."""
    try:
        return float(os.getenv("RATE_LIMIT_RPM", "18"))
    except ValueError:
        logger.warning("ratelimit=bad_env RATE_LIMIT_RPM=%s using=18", os.getenv("RATE_LIMIT_RPM"))
        return 18.0


class AsyncRateLimiter:
    """Sliding-window limiter: at most `rpm` acquisitions per `window` seconds."""

    def __init__(self, rpm: float | None = None, window: float = 60.0):
        self.rpm = default_rpm() if rpm is None else rpm
        self.window = window
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    def set_rpm(self, rpm: float) -> None:
        """Update the budget live (called when Settings change). <=0 disables."""
        if rpm != self.rpm:
            logger.info("ratelimit=reconfigure rpm=%s -> %s", self.rpm, rpm)
            self.rpm = rpm

    async def acquire(self) -> float:
        """Block until a slot is free. Returns seconds waited (0 if immediate)."""
        if self.rpm <= 0:
            return 0.0
        waited = 0.0
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] >= self.window:
                    self._times.popleft()
                if len(self._times) < self.rpm:
                    self._times.append(now)
                    if waited:
                        logger.info("ratelimit=acquired rpm=%s waited=%.2fs", self.rpm, waited)
                    return waited
                delay = self.window - (now - self._times[0])
            logger.info("ratelimit=throttle rpm=%s in_flight=%d wait=%.2fs",
                        self.rpm, len(self._times), delay)
            await asyncio.sleep(max(delay, 0.0) + 0.01)
            waited += max(delay, 0.0) + 0.01


# Shared instance: one global budget for the whole process.
limiter = AsyncRateLimiter()
