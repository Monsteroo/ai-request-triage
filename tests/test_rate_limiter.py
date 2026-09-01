"""Pacing tests.

Time is mocked: a test that actually waits out a quota window is a test nobody
runs.
"""

import asyncio

import pytest

from triage.pipeline import RateLimiter

# Captured before monkeypatching: patching ``triage.pipeline.asyncio.sleep``
# rebinds the attribute on the shared asyncio module, so a FakeClock that called
# asyncio.sleep by name would call itself.
_real_sleep = asyncio.sleep


class FakeClock:
    """Monotonic clock that only advances when a sleeper asks it to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await _real_sleep(0)  # yield to the loop without burning wall-clock time


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr("triage.pipeline.time.monotonic", fake.time)
    monkeypatch.setattr("triage.pipeline.asyncio.sleep", fake.sleep)
    return fake


async def test_calls_are_evenly_spaced_not_bursted(clock):
    """5/minute must mean one call per 12s, never five at once."""
    limiter = RateLimiter(5)
    stamps = []
    for _ in range(5):
        await limiter.acquire()
        stamps.append(clock.now)

    gaps = [round(b - a, 2) for a, b in zip(stamps, stamps[1:])]
    assert all(gap >= 12.0 for gap in gaps), gaps


async def test_a_full_batch_stays_inside_the_quota(clock):
    """18 calls at 5/minute must not fit into fewer than ~3.4 minutes."""
    limiter = RateLimiter(5)
    for _ in range(18):
        await limiter.acquire()
    assert clock.now - 1000.0 >= 17 * 12.0


async def test_pacing_can_be_disabled(clock):
    limiter = RateLimiter(0)
    for _ in range(100):
        await limiter.acquire()
    assert clock.now == 1000.0


async def test_one_workers_429_holds_back_the_whole_pool(clock):
    limiter = RateLimiter(5)
    await limiter.acquire()
    limiter.pause_for(30.0)  # the server said "retry in 30s"

    await asyncio.gather(*(limiter.acquire() for _ in range(3)))
    assert clock.now >= 1030.0


async def test_cooldown_applies_even_when_pacing_is_disabled(clock):
    """Turning pacing off is a throughput choice, not permission to ignore a 429."""
    limiter = RateLimiter(0)
    limiter.pause_for(20.0)
    await limiter.acquire()
    assert clock.now >= 1020.0


async def test_concurrent_workers_share_one_pace(clock):
    """Ten workers must not each get their own allowance."""
    limiter = RateLimiter(5)
    await asyncio.gather(*(limiter.acquire() for _ in range(10)))
    assert clock.now - 1000.0 >= 9 * 12.0
