"""Failure-path tests.

The happy path is the least interesting thing this pipeline does. What matters
is that a misbehaving model cannot make a row disappear, and that we do not burn
calls on situations that cannot improve.
"""

import asyncio
import json

import pytest

import triage.pipeline as pipeline_module
from triage.config import Settings
from triage.llm.base import (
    LLMClient,
    LLMResponse,
    PermanentLLMError,
    QuotaExhaustedError,
    TransientLLMError,
)
from triage.llm.fake import FakeClient
from triage.models import RawRequest
from triage.pipeline import RunGuard, extract_json, triage_all, triage_one

VALID = json.dumps(
    {
        "category": "звіт/аналітика",
        "target_department": "маркетинг",
        "priority": "medium",
        "short_summary": "Щотижневий звіт по Google Ads.",
        "requested_actions": ["Автоматизувати щотижневий звіт"],
        "needs_clarification": False,
        "confidence": 0.9,
        "clarifying_questions": [],
        "mentioned_systems": ["Google Ads"],
        "is_actionable": True,
    },
    ensure_ascii=False,
)

BAD_ENUM = VALID.replace("звіт/аналітика", "щось вигадане")

# backoff_base_s=0 keeps the suite fast; the backoff itself is exercised in
# test_backoff_is_bounded_and_jittered below.
SETTINGS = Settings(
    provider="fake",
    max_attempts=3,
    request_timeout_s=5,
    max_concurrency=2,
    backoff_base_s=0,
    requests_per_minute=0,  # pacing is exercised separately, in test_rate_limiter.py
)


class ScriptedClient(LLMClient):
    """Replays a fixed script: strings are returned, exceptions are raised."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, *script):
        self.script = list(script)
        self.prompts: list[str] = []

    async def generate_json(self, *, system: str, user: str) -> LLMResponse:
        self.prompts.append(user)
        step = self.script.pop(0) if self.script else VALID
        if isinstance(step, Exception):
            raise step
        return LLMResponse(text=step, model=self.model, prompt_tokens=100, output_tokens=50)


def request(text="Можна автоматизувати щотижневий звіт по Google Ads?", rid="REQ-1"):
    return RawRequest(id=rid, channel="Slack", raw_text=text)


async def test_happy_path():
    record, calls = await triage_one(request(), ScriptedClient(VALID), SETTINGS)
    assert record.status == "ok" and calls == 1
    assert record.triage is not None
    assert record.triage.short_summary.startswith("Щотижневий")
    assert record.meta.prompt_tokens == 100


async def test_malformed_json_is_repaired_on_the_second_attempt():
    client = ScriptedClient("```json\n{broken", VALID)
    record, calls = await triage_one(request(), client, SETTINGS)
    assert record.status == "ok" and calls == 2 and record.meta.attempts == 2


async def test_repair_prompt_shows_the_model_its_own_mistake():
    client = ScriptedClient(BAD_ENUM, VALID)
    await triage_one(request(), client, SETTINGS)
    repair_prompt = client.prompts[1]
    assert "не пройшла валідацію" in repair_prompt
    assert "щось вигадане" in repair_prompt  # the bad output
    assert "category" in repair_prompt  # the validator's complaint


async def test_persistently_invalid_output_fails_without_losing_the_row():
    client = ScriptedClient(BAD_ENUM, BAD_ENUM, BAD_ENUM)
    record, calls = await triage_one(request(), client, SETTINGS)
    assert record.status == "failed" and calls == 3
    assert record.error is not None and record.error.kind == "validation"
    assert record.error.last_raw_response is not None  # evidence is kept
    assert record.raw_text == request().raw_text  # the input survives
    assert record.triage is None


async def test_transient_error_is_retried():
    client = ScriptedClient(TransientLLMError("429"), VALID)
    record, _ = await triage_one(request(), client, SETTINGS)
    assert record.status == "ok" and record.meta.attempts == 2


async def test_transient_errors_do_not_trigger_the_repair_prompt():
    """A rate limit says nothing about output quality — resend the normal prompt."""
    client = ScriptedClient(TransientLLMError("429"), VALID)
    await triage_one(request(), client, SETTINGS)
    assert "не пройшла валідацію" not in client.prompts[1]


async def test_exhausted_retries_fail_as_transport():
    client = ScriptedClient(*[TransientLLMError("429")] * 3)
    record, _ = await triage_one(request(), client, SETTINGS)
    assert record.status == "failed" and record.error is not None
    assert record.error.kind == "transport"


async def test_permanent_error_stops_immediately():
    client = ScriptedClient(PermanentLLMError("bad api key"), VALID, VALID)
    record, calls = await triage_one(request(), client, SETTINGS)
    assert record.status == "failed" and calls == 1  # no pointless retries
    assert record.error is not None and "bad api key" in record.error.message


async def test_empty_input_never_reaches_the_model():
    client = ScriptedClient(VALID)
    record, calls = await triage_one(request(text="   "), client, SETTINGS)
    assert record.status == "failed" and calls == 0
    assert record.error is not None and record.error.kind == "empty_input"
    assert client.prompts == []


async def test_business_rules_are_applied_and_recorded():
    payload = json.loads(VALID)
    payload.update({"category": "поза скоупом", "priority": "high"})
    record, _ = await triage_one(
        request(), ScriptedClient(json.dumps(payload, ensure_ascii=False)), SETTINGS
    )
    assert record.triage is not None and record.triage.priority.value == "medium"
    assert "R2:out_of_scope_priority_capped" in record.meta.applied_rules


async def test_timeout_is_treated_as_transient():
    class SlowClient(ScriptedClient):
        async def generate_json(self, *, system, user):
            await asyncio.sleep(0.2)
            return await super().generate_json(system=system, user=user)

    record, _ = await triage_one(
        request(), SlowClient(VALID), Settings(
            provider="fake",
            max_attempts=1,
            request_timeout_s=0,
            backoff_base_s=0,
            requests_per_minute=0,
        )
    )
    assert record.status == "failed" and record.error is not None
    assert record.error.kind == "transport"


async def test_every_row_produces_exactly_one_record_in_input_order():
    # The prompt carries the request text, not the id, so the stub keys off text.
    requests = [request(text=f"запит номер {i}", rid=f"REQ-{i}") for i in range(6)]

    class FlakyClient(ScriptedClient):
        async def generate_json(self, *, system, user):
            self.prompts.append(user)
            if "номер 3" in user or "номер 4" in user:
                raise PermanentLLMError("nope")
            return LLMResponse(text=VALID, model=self.model)

    records, stats = await triage_all(requests, FlakyClient(), SETTINGS)
    assert [r.id for r in records] == [r.id for r in requests]
    assert stats.total == 6 and stats.ok + stats.failed == 6
    assert stats.ok == 4 and stats.failed == 2
    assert [r.id for r in records if r.status == "failed"] == ["REQ-3", "REQ-4"]


async def test_concurrency_is_bounded():
    peak = 0
    live = 0

    class CountingClient(ScriptedClient):
        async def generate_json(self, *, system, user):
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return LLMResponse(text=VALID, model=self.model)

    await triage_all([request(rid=f"R{i}") for i in range(10)], CountingClient(), SETTINGS)
    assert peak <= SETTINGS.max_concurrency


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"a": 1}', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('Ось результат: {"a": 1}. Готово.', '{"a": 1}'),
        ("не json взагалі", "не json взагалі"),
    ],
)
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


async def test_backoff_grows_and_stays_bounded(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("triage.pipeline.asyncio.sleep", fake_sleep)
    settings = Settings(
        provider="fake", max_attempts=4, backoff_base_s=1.0, requests_per_minute=0
    )
    client = ScriptedClient(*[TransientLLMError("429")] * 4)
    await triage_one(request(), client, settings)

    assert len(slept) == 3  # no sleep after the final attempt
    assert slept == sorted(slept)  # strictly growing
    assert all(d <= 30.4 for d in slept)  # capped


async def test_server_retry_hint_beats_our_backoff_curve(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("triage.pipeline.asyncio.sleep", fake_sleep)
    settings = Settings(
        provider="fake", max_attempts=2, backoff_base_s=1.0, requests_per_minute=0
    )
    client = ScriptedClient(TransientLLMError("429 quota", retry_after_s=11.0), VALID)
    record, _ = await triage_one(request(), client, settings)

    assert record.status == "ok"
    # 11s hint (plus <=0.4s jitter), not the 1s the exponential curve would pick.
    assert 11.0 <= slept[0] <= 11.4


async def test_exhausted_daily_quota_stops_the_whole_run():
    """A per-day quota is a fact about the run, not about one row."""
    requests = [request(text=f"запит номер {i}", rid=f"REQ-{i}") for i in range(8)]

    class DeadKeyClient(ScriptedClient):
        async def generate_json(self, *, system, user):
            self.prompts.append(user)
            raise QuotaExhaustedError("Daily free-tier quota exhausted")

    client = DeadKeyClient()
    records, stats = await triage_all(requests, client, SETTINGS)

    assert len(records) == 8  # every row still produces a record
    assert all(r.status == "failed" for r in records)
    # One row discovers the problem; the rest must not re-pay for it. Without
    # the guard this would be 8 rows x 3 attempts = 24 calls.
    assert len(client.prompts) < 8
    assert stats.aborted_reason is not None
    aborted = [r for r in records if "run aborted" in (r.error.message if r.error else "")]
    assert aborted, "later rows should be short-circuited, not retried"


async def test_an_ordinary_permanent_error_does_not_abort_the_run():
    """One malformed request must not take the batch down with it."""
    requests = [request(text=f"запит номер {i}", rid=f"REQ-{i}") for i in range(4)]

    class OneBadRowClient(ScriptedClient):
        async def generate_json(self, *, system, user):
            self.prompts.append(user)
            if "номер 1" in user:
                raise PermanentLLMError("that one request was malformed")
            return LLMResponse(text=VALID, model=self.model)

    records, stats = await triage_all(requests, OneBadRowClient(), SETTINGS)
    assert stats.aborted_reason is None
    assert sum(r.status == "ok" for r in records) == 3
    assert [r.id for r in records if r.status == "failed"] == ["REQ-1"]


def test_the_guard_keeps_the_first_cause():
    guard = RunGuard()
    assert not guard.tripped
    guard.trip("quota exhausted")
    guard.trip("something later and less useful")
    assert guard.reason == "quota exhausted"


# --- "never lose a row" — the three layers of the safety net -----------------
#
# triage_one must never raise (row-level net); a progress callback's own bugs
# must not downgrade an already-successful result (isolation); and even a bug
# in triage_one itself must not cost the *other* rows their results
# (gather-level net). Each test isolates one layer.


async def test_unexpected_exception_does_not_lose_the_row():
    """A bug that is not one of the four typed LLMError cases — a real one
    turned out to be response.text raising inside the SDK — must still
    produce a status='failed' record instead of escaping triage_one."""

    class ExplodingClient(ScriptedClient):
        async def generate_json(self, *, system, user):
            raise RuntimeError("e.g. a bug in response parsing")

    record, calls = await triage_one(request(), ExplodingClient(), SETTINGS)

    assert record.status == "failed"
    assert record.error is not None and record.error.kind == "unknown"
    assert "bug in response parsing" in record.error.message


async def test_on_done_callback_failure_does_not_lose_the_record():
    """The progress callback is a side effect, not the result. A bug in it
    must not downgrade an already-successful triage to a synthetic failure."""
    requests = [request(text=f"запит номер {i}", rid=f"REQ-{i}") for i in range(4)]

    def exploding_on_done(record):
        if record.id == "REQ-2":
            raise RuntimeError("bug in a progress callback")

    records, _ = await triage_all(requests, ScriptedClient(), SETTINGS, on_done=exploding_on_done)

    assert [r.id for r in records] == [r.id for r in requests]
    assert all(r.status == "ok" for r in records)  # the real triage result survives


async def test_triage_all_survives_a_completely_unexpected_worker_failure(monkeypatch):
    """Last-resort net: even if triage_one itself explodes for one row — not
    just the LLM call inside it — the run must not lose the other rows with
    it. Before asyncio.gather got return_exceptions=True, this destroyed
    every result: reproduced with a client raising RuntimeError, 0 records
    returned for a batch of 5."""
    original_triage_one = pipeline_module.triage_one

    async def flaky_triage_one(request, client, settings, limiter=None, guard=None):
        if request.id == "REQ-2":
            raise RuntimeError("hypothetical bug elsewhere in triage_one")
        return await original_triage_one(request, client, settings, limiter, guard)

    monkeypatch.setattr(pipeline_module, "triage_one", flaky_triage_one)

    requests = [request(rid=f"REQ-{i}") for i in range(4)]
    records, _ = await triage_all(requests, ScriptedClient(), SETTINGS)

    assert [r.id for r in records] == [r.id for r in requests]
    assert sum(r.status == "ok" for r in records) == 3
    broken = next(r for r in records if r.id == "REQ-2")
    assert broken.status == "failed"
    assert broken.error is not None and broken.error.kind == "unknown"


# --- the offline stub must never be paced like a quota-bound provider --------


def test_fake_client_defaults_to_not_needing_pacing():
    assert FakeClient().needs_pacing is False


def test_llm_client_base_defaults_to_needing_pacing():
    assert LLMClient.needs_pacing is True


async def test_fake_provider_is_never_paced_by_triage_all(monkeypatch):
    """FakeClient makes no network call and has no quota to protect. Pacing it
    anyway turned `--provider fake` — the quick start README offers for trying
    the repo without an API key — into a multi-minute wait for stub output.

    Both time.monotonic and asyncio.sleep are mocked (as in test_rate_limiter.py):
    mocking only sleep while leaving the clock real means a real RateLimiter
    would spin — asyncio.sleep resolving instantly without wall-clock time
    actually advancing — rather than failing cleanly. That spin is exactly what
    happened re-running this test against the pre-fix pipeline while checking
    this test actually catches the regression: a bounded pytest run turned into
    a busy loop that had to be killed, instead of a fast, clear assertion
    failure. Faking the clock too keeps a reintroduced bug a quick failure.
    """
    now = 1000.0

    def fake_monotonic():
        return now

    async def fake_sleep(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr("triage.pipeline.time.monotonic", fake_monotonic)
    monkeypatch.setattr("triage.pipeline.asyncio.sleep", fake_sleep)
    settings = Settings(provider="fake", max_attempts=1, requests_per_minute=5)
    requests = [request(rid=f"REQ-{i}") for i in range(10)]

    await triage_all(requests, FakeClient(), settings)

    assert now == 1000.0  # no pacing delay of any kind — the fake clock never moved


# --- RunGuard must be honoured even after a worker was queued ---------------


async def test_guard_tripped_during_the_limiter_wait_prevents_the_call():
    """A worker queued behind the rate limiter must not fire its call if the
    guard tripped — because another row's response arrived — while it waited."""

    class GuardTrippingLimiter:
        """Stands in for RateLimiter: tripping the guard as a side effect of
        acquire() simulates another worker's row exhausting the quota while
        this one was still queued behind it."""

        def __init__(self, guard: RunGuard) -> None:
            self._guard = guard

        async def acquire(self) -> None:
            self._guard.trip("tripped while this worker was queued")

    guard = RunGuard()
    limiter = GuardTrippingLimiter(guard)
    client = ScriptedClient(VALID)

    record, calls = await triage_one(request(), client, SETTINGS, limiter, guard)

    assert calls == 0  # never reached the client
    assert client.prompts == []
    assert record.status == "failed"
    assert record.error is not None and "run aborted" in record.error.message


# --- BACKOFF_BASE_S=0 must disable only our own guessed curve ---------------


async def test_backoff_base_zero_still_honours_the_servers_retry_hint(monkeypatch):
    """BACKOFF_BASE_S=0 disables our own guessed exponential curve. It must
    not also discard an explicit "retry in 10s" the provider actually sent —
    that combination used to retry instantly into a 429 that had just asked
    for ten seconds of quiet."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("triage.pipeline.asyncio.sleep", fake_sleep)
    client = ScriptedClient(TransientLLMError("429 quota", retry_after_s=10.0), VALID)

    record, _ = await triage_one(request(), client, SETTINGS)  # SETTINGS.backoff_base_s == 0

    assert record.status == "ok"
    assert slept and 10.0 <= slept[0] <= 10.4  # the hint was honoured, not skipped
