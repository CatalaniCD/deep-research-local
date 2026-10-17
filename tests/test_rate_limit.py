"""Rate limiter tests (fast, injected window; no network)."""
import time

from deep_research.rate_limit import AsyncRateLimiter, default_rpm


def test_default_rpm_from_env(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_RPM", "18")
    assert default_rpm() == 18.0


def test_bad_env_falls_back_to_18(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_RPM", "not-a-number")
    assert default_rpm() == 18.0


async def test_disabled_rpm_is_instant():
    lim = AsyncRateLimiter(rpm=0)
    assert await lim.acquire() == 0.0
    assert await lim.acquire() == 0.0


async def test_within_budget_is_immediate():
    lim = AsyncRateLimiter(rpm=3, window=60.0)
    for _ in range(3):
        assert await lim.acquire() == 0.0


async def test_throttles_after_budget():
    lim = AsyncRateLimiter(rpm=2, window=0.2)
    assert await lim.acquire() == 0.0
    assert await lim.acquire() == 0.0
    start = time.monotonic()
    waited = await lim.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.19
    assert waited > 0.0
