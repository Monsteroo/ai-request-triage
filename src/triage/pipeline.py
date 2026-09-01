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
from .llm.base import (
    LLMClient,
    PermanentLLMError,
    QuotaExhaustedError,
    TransientLLMError,
)
from .models import ErrorInfo, ProcessedRequest, ProcessingMeta, RawRequest, TriageFields
from .prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from .rules import apply_business_rules

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)

MAX_BACKOFF_S = 30.0


class RunGuard:
    """One-way switch that short-circuits the rest of a doomed run.

    Some failures are facts about the whole run, not about one request: a
    per-day quota is gone for everyone, and a rejected API key will not start
    working on row nine. Without this, an exhausted key spends the full retry
    budget on all eighteen rows — measured at 25 minutes and 54 pointless calls
    for a run that was already over after the first one.

    The remaining rows still produce records. They are marked failed with the
    reason, so the output stays complete and the cause is obvious.
    """

    def __init__(self) -> None:
        self.reason: str | None = None

    def trip(self, reason: str) -> None:
        if self.reason is None:  # keep the first cause, not the last
            self.reason = reason
            logger.error("Aborting the rest of the run: %s", reason)

    @property
    def tripped(self) -> bool:
        return self.reason is not None


class RateLimiter:
    """Paces call starts to a steady interval, with a cooldown shared by all workers.

    Evenly spaced rather than a sliding window. A window limiter is allowed to
    fire five calls at 0:59 and five more at 1:01 — ten inside one of the
    server's minutes — and so trips a quota our own accounting says we are under.
    That is exactly what happened on the first live run of this pipeline: 17
    rate-limit hits while the limiter believed it was compliant. For a full
    batch the burst buys nothing anyway, since the total time is governed by the
    quota either way.

    ``pause_for`` is the other half. A 429 is information about the *pool*, not
    about one request: without sharing it, the other in-flight workers keep
    firing into a limit already known to be closed.
    """

    def __init__(self, per_minute: int) -> None:
        # per_minute <= 0 disables pacing, but never disables the cooldown.
        self._interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    def pause_for(self, seconds: float) -> None:
        """Hold every worker back for ``seconds`` — used when the server says so."""
        self._next_slot = max(self._next_slot, time.monotonic() + seconds)

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                if now >= self._next_slot:
                    self._next_slot = now + self._interval
                    return
                wait = self._next_slot - now
            # Sleep outside the lock so the pool keeps draining in order.
            await asyncio.sleep(wait + 0.02)


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
    aborted_reason: str | None = None

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


async def _sleep_backoff(attempt: int, base_s: float, retry_after_s: float | None = None) -> None:
    """Wait before the next attempt.

    The provider's own ``retry_after`` hint is honoured even when ``base_s``
    disables our own guessed curve. ``base_s`` only controls the exponential
    curve we invent when the provider gives no hint at all — it was never
    meant to license ignoring an explicit "retry in Ns" from the server, and
    treating it that way sent BACKOFF_BASE_S=0 instant-retrying straight into
    a 429 that had just asked for a minute of quiet.
    """
    if retry_after_s is not None:
        delay = min(MAX_BACKOFF_S, retry_after_s)
    elif base_s <= 0:
        return
    else:
        delay = min(MAX_BACKOFF_S, base_s * 2.0 ** (attempt - 1))
    jitter_span = base_s if base_s > 0 else 1.0
    await asyncio.sleep(delay + random.uniform(0, 0.4 * jitter_span))


async def triage_one(
    request: RawRequest,
    client: LLMClient,
    settings: Settings,
    limiter: RateLimiter | None = None,
    guard: RunGuard | None = None,
) -> tuple[ProcessedRequest, int]:
    """Triage a single request. Returns the record and the number of LLM calls made.

    Never raises. Every path — including one we did not anticipate — ends in a
    ``ProcessedRequest``, because that is the one guarantee the rest of the
    pipeline is built on.
    """
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

    try:
        for attempt in range(1, settings.max_attempts + 1):
            if guard is not None and guard.tripped:
                return finish_failed("transport", f"run aborted: {guard.reason}"), calls
            meta.attempts = attempt
            user_prompt = (
                build_user_prompt(request)
                if last_raw is None
                else build_repair_prompt(request, last_raw, last_error)
            )

            try:
                if limiter is not None:
                    await limiter.acquire()
                # Re-check: a worker can queue behind the limiter for a full
                # interval, and another worker's row can trip the guard during
                # that wait. Without this second check the call fires anyway —
                # exactly the waste RunGuard exists to prevent.
                if guard is not None and guard.tripped:
                    return finish_failed("transport", f"run aborted: {guard.reason}"), calls
                async with asyncio.timeout(settings.request_timeout_s):
                    response = await client.generate_json(system=SYSTEM_PROMPT, user=user_prompt)
                calls += 1
            except QuotaExhaustedError as exc:
                # Not just this row's problem — stop the whole run.
                calls += 1
                if guard is not None:
                    guard.trip(str(exc))
                return finish_failed("transport", str(exc)), calls
            except PermanentLLMError as exc:
                # The request was sent and rejected, so it counts against quota even
                # though retrying it cannot help.
                calls += 1
                logger.error("%s: unrecoverable provider error: %s", request.id, exc)
                return finish_failed("transport", str(exc)), calls
            except (TransientLLMError, TimeoutError) as exc:
                calls += 1
                last_kind, last_error = "transport", str(exc) or "request timed out"
                logger.warning(
                    "%s: attempt %d failed (%s)", request.id, attempt, last_error[:160]
                )
                if attempt < settings.max_attempts:
                    retry_after = getattr(exc, "retry_after_s", None)
                    if limiter is not None and retry_after is not None:
                        # Hand the wait to the limiter so every worker observes it,
                        # then only jitter locally — otherwise we would wait twice.
                        limiter.pause_for(retry_after)
                        await _sleep_backoff(attempt, settings.backoff_base_s)
                    else:
                        await _sleep_backoff(attempt, settings.backoff_base_s, retry_after)
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
    except Exception as exc:
        # Last-resort net: the module's contract is that every input row
        # produces exactly one output record, and a bug we did not
        # anticipate here (a malformed SDK response, a business-rule crash,
        # anything) must not take the row down with it — or, one level up
        # in triage_all's bare gather, the entire run down with it.
        logger.exception("%s: unexpected failure during triage", request.id)
        return finish_failed("unknown", f"unexpected error: {exc}"), calls


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
    # The offline stub makes no network call and has no quota to protect —
    # pacing it anyway turned the "try it without a key" quick start into a
    # multi-minute wait for stub output.
    limiter = RateLimiter(settings.requests_per_minute if client.needs_pacing else 0)
    guard = RunGuard()
    started = time.perf_counter()

    async def worker(request: RawRequest) -> ProcessedRequest:
        async with semaphore:
            record, calls = await triage_one(request, client, settings, limiter, guard)
        stats.llm_calls += calls
        if on_done is not None:
            try:
                on_done(record)
            except Exception:
                # A progress callback is a side effect, not the result. A bug in
                # it must not cost the row its already-computed record.
                logger.exception("%s: on_done callback raised; keeping the record anyway", record.id)
        return record

    # return_exceptions=True plus the reconciliation below is the outermost
    # layer of the "never lose a row" guarantee: triage_one already never
    # raises, but a bare gather would still let a bug anywhere else in worker
    # (or a future change to it) take out every result instead of just one.
    raw_results = await asyncio.gather(*(worker(r) for r in requests), return_exceptions=True)

    records: list[ProcessedRequest] = []
    for req, result in zip(requests, raw_results, strict=True):
        if isinstance(result, BaseException):
            logger.error("%s: worker task failed unexpectedly: %s", req.id, result)
            records.append(
                ProcessedRequest(
                    id=req.id,
                    channel=req.channel,
                    timestamp=req.timestamp,
                    raw_text=req.raw_text,
                    status="failed",
                    error=ErrorInfo(kind="unknown", message=f"worker task failed: {result}"),
                )
            )
        else:
            records.append(result)

    stats.wall_time_s = time.perf_counter() - started
    stats.aborted_reason = guard.reason
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
