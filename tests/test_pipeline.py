"""Failure-path tests.

The happy path is the least interesting thing this pipeline does. What matters
is that a misbehaving model cannot make a row disappear, and that we do not burn
calls on situations that cannot improve.
"""

import asyncio
import json

import pytest

from triage.config import Settings
from triage.llm.base import LLMClient, LLMResponse, PermanentLLMError, TransientLLMError
from triage.models import RawRequest
from triage.pipeline import extract_json, triage_all, triage_one

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
    requests = [request(rid=f"REQ-{i}") for i in range(6)]

    class FlakyClient(ScriptedClient):
        async def generate_json(self, *, system, user):
            self.prompts.append(user)
            if "REQ-3" in user or "REQ-4" in user:
                raise PermanentLLMError("nope")
            return LLMResponse(text=VALID, model=self.model)

    records, stats = await triage_all(requests, FlakyClient(), SETTINGS)
    assert [r.id for r in records] == [r.id for r in requests]
    assert stats.total == 6 and stats.ok + stats.failed == 6


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
