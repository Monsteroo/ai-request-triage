"""Async triage pipeline.

The contract of this module is one sentence: **every input row produces exactly
one output record.** A row can fail — the model can babble, the API can 429, the
network can drop — but it can never vanish. Failures are data, carried in
``status="failed"`` with the reason and the model's last raw answer attached,
so a human can see what happened without re-running anything.

Recovery is layered, cheapest first:

1. provider-side structured output (in the client) removes most malformed JSON;
2. tolerant extraction unwraps markdown fences the model adds anyway;
3. pydantic validation is the hard gate;
4. a repair prompt shows the model its own broken output plus the validator's
   complaint, which fixes most enum and type slips in one extra call;
5. transient API errors retry with exponential backoff and jitter;
6. whatever survives all of that is recorded as a failure.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from .config import Settings
from .llm.base import LLMClient, PermanentLLMError, TransientLLMError
from .models import ErrorInfo, ProcessedRequest, ProcessingMeta, RawRequest, TriageFields
from .prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from .rules import apply_business_rules

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)

MAX_BACKOFF_S = 8.0


@dataclass
class RunStats:
    total: int = 0
    ok: int = 0
    failed: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    wall_time_s: float = 0.0
    rules_fired: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


def extract_json(text: str) -> str:
    """Pull a JSON object out of a model reply.

    Structured output makes this mostly redundant, but "mostly" is doing real
    work: models still occasionally wrap JSON in fences or prepend a sentence,
    and recovering from that is far cheaper than another API call.
    """
    candidate = _FENCE.sub("", text.strip())
    if candidate.startswith("{") and candidate.endswith("}"):
        return candidate
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        return candidate[start : end + 1]
    return candidate


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors()[:8]
    )


async def _sleep_backoff(attempt: int) -> None:
    delay = min(MAX_BACKOFF_S, 2.0 ** (attempt - 1)) + random.uniform(0, 0.4)
    await asyncio.sleep(delay)


async def triage_one(
    request: RawRequest, client: LLMClient, settings: Settings
) -> tuple[ProcessedRequest, int]:
    """Triage a single request. Returns the record and the number of LLM calls made."""
    started = time.perf_counter()
    meta = ProcessingMeta(model=client.model)
    calls = 0

    def finish_failed(kind: str, message: str, raw: str | None = None) -> ProcessedRequest:
        meta.latency_ms = int((time.perf_counter() - started) * 1000)
        return ProcessedRequest(
            id=request.id,
            channel=request.channel,
            timestamp=request.timestamp,
            raw_text=request.raw_text,
            status="failed",
            error=ErrorInfo(kind=kind, message=message, last_raw_response=raw),
            meta=meta,
        )

    # No text means nothing to classify. Short-circuiting here is both correct
    # and free — we refuse to spend a token asking a model about an empty string.
    if not request.raw_text.strip():
        logger.warning("%s has empty raw_text — skipping the model call", request.id)
        return finish_failed("empty_input", "raw_text is empty"), calls

    last_raw: str | None = None
    last_error = "no attempt completed"
    last_kind = "unknown"

    for attempt in range(1, settings.max_attempts + 1):
        meta.attempts = attempt
        user_prompt = (
            build_user_prompt(request)
            if last_raw is None
            else build_repair_prompt(request, last_raw, last_error)
        )

        try:
            async with asyncio.timeout(settings.request_timeout_s):
                response = await client.generate_json(system=SYSTEM_PROMPT, user=user_prompt)
            calls += 1
        except PermanentLLMError as exc:
            logger.error("%s: unrecoverable provider error: %s", request.id, exc)
            return finish_failed("transport", str(exc)), calls
        except (TransientLLMError, TimeoutError, asyncio.TimeoutError) as exc:
            calls += 1
            last_kind, last_error = "transport", str(exc) or "request timed out"
            logger.warning("%s: attempt %d failed (%s)", request.id, attempt, last_error)
            if attempt < settings.max_attempts:
                await _sleep_backoff(attempt)
            continue

        meta.model = response.model
        meta.prompt_tokens = (meta.prompt_tokens or 0) + (response.prompt_tokens or 0)
        meta.output_tokens = (meta.output_tokens or 0) + (response.output_tokens or 0)

        try:
            payload = json.loads(extract_json(response.text))
            triage = TriageFields.model_validate(payload)
        except json.JSONDecodeError as exc:
            last_kind, last_raw, last_error = "validation", response.text, f"invalid JSON: {exc}"
        except ValidationError as exc:
            last_kind, last_raw = "validation", response.text
            last_error = _format_validation_error(exc)
        else:
            triage, fired = apply_business_rules(triage)
            meta.applied_rules = fired
            meta.latency_ms = int((time.perf_counter() - started) * 1000)
            return (
                ProcessedRequest(
                    id=request.id,
                    channel=request.channel,
                    timestamp=request.timestamp,
                    raw_text=request.raw_text,
                    status="ok",
                    triage=triage,
                    meta=meta,
                ),
                calls,
            )

        logger.warning(
            "%s: attempt %d produced invalid output (%s)", request.id, attempt, last_error
        )

    return finish_failed(last_kind, last_error, last_raw), calls


async def triage_all(
    requests: list[RawRequest],
    client: LLMClient,
    settings: Settings,
    on_done: Callable[[ProcessedRequest], None] | None = None,
) -> tuple[list[ProcessedRequest], RunStats]:
    """Triage every request concurrently, bounded by ``max_concurrency``.

    The bound is not decoration: free API tiers are rate limited per minute, and
    an unbounded ``gather`` over a large inbox is the fastest way to turn every
    call into a 429. Results keep input order regardless of completion order.
    """
    stats = RunStats(total=len(requests))
    if not requests:
        return [], stats

    semaphore = asyncio.Semaphore(max(1, settings.max_concurrency))
    started = time.perf_counter()

    async def worker(request: RawRequest) -> ProcessedRequest:
        async with semaphore:
            record, calls = await triage_one(request, client, settings)
        stats.llm_calls += calls
        if on_done is not None:
            on_done(record)
        return record

    records = await asyncio.gather(*(worker(r) for r in requests))

    stats.wall_time_s = time.perf_counter() - started
    for record in records:
        if record.status == "ok":
            stats.ok += 1
        else:
            stats.failed += 1
        stats.prompt_tokens += record.meta.prompt_tokens or 0
        stats.output_tokens += record.meta.output_tokens or 0
        for rule in record.meta.applied_rules:
            stats.rules_fired[rule] = stats.rules_fired.get(rule, 0) + 1

    return list(records), stats
