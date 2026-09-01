"""Pacing tests.

Time is mocked: a test that actually waits out a 60-second window is a test
nobody runs.
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


async def test_a_burst_within_the_window_is_not_delayed(clock):
    limiter = RateLimiter(5)
    for _ in range(5):
        await limiter.acquire()
    assert clock.now == 1000.0  # the quota allows a burst; do not slow it down


async def test_the_sixth_call_waits_for_the_window_to_roll(clock):
    limiter = RateLimiter(5)
    for _ in range(5):
        await limiter.acquire()
    await limiter.acquire()
    assert clock.now >= 1060.0


async def test_pacing_can_be_disabled(clock):
    limiter = RateLimiter(0)
    for _ in range(100):
        await limiter.acquire()
    assert clock.now == 1000.0


async def test_concurrent_workers_share_one_window(clock):
    """Ten workers must not each get their own allowance of five."""
    limiter = RateLimiter(5)
    await asyncio.gather(*(limiter.acquire() for _ in range(10)))
    assert clock.now >= 1060.0
