"""Google Gemini client.

Chosen because the brief points at it and its free tier covers this workload
comfortably. Two settings matter for reproducibility: ``temperature=0`` and a
fixed ``seed``. Together they make repeated runs *mostly* stable — see the
non-determinism section of the README for why "mostly" is the honest word.
"""

import logging
import re

from google import genai
from google.genai import errors, types

from .base import (
    LLMClient,
    LLMError,
    LLMResponse,
    PermanentLLMError,
    QuotaExhaustedError,
    TransientLLMError,
)
from .schema import TRIAGE_RESPONSE_SCHEMA

logger = logging.getLogger(__name__)

_RETRY_HINT = re.compile(r"retry in ([\d.]+)s", re.I)


def _quota_violations(exc: errors.APIError) -> list[dict]:
    """Pull the QuotaFailure violations out of a 429, if the provider sent any."""
    for entry in _error_details(exc):
        if entry.get("@type", "").endswith("QuotaFailure"):
            violations = entry.get("violations")
            return [v for v in violations if isinstance(v, dict)] if violations else []
    return []


def _is_daily_quota(exc: errors.APIError) -> bool:
    """True when the exhausted quota is a per-day one, which no wait will fix."""
    return any(
        "perday" in str(v.get("quotaId", "")).casefold() for v in _quota_violations(exc)
    )


def _error_details(exc: errors.APIError) -> list[dict]:
    details = getattr(exc, "details", None)
    entries: list = []
    if isinstance(details, dict):
        entries = details.get("error", {}).get("details", []) or []
    elif isinstance(details, list):
        entries = details
    return [e for e in entries if isinstance(e, dict)]


def _retry_after(exc: errors.APIError) -> float | None:
    """Dig the server's suggested wait out of a quota error.

    Gemini returns it twice: as a structured ``RetryInfo`` detail and as prose
    in the message. Check the structured form first, fall back to the prose.
    """
    for entry in _error_details(exc):
        if entry.get("@type", "").endswith("RetryInfo"):
            raw = str(entry.get("retryDelay", "")).rstrip("s")
            try:
                return float(raw)
            except ValueError:
                pass
    match = _RETRY_HINT.search(str(exc))
    return float(match.group(1)) if match else None

# Rate limiting is the one 4xx that is worth waiting out.
RETRYABLE_CLIENT_CODES = {408, 409, 429}

# Generous enough for ten fields plus a few clarifying questions; small enough
# that a runaway generation gets cut off instead of billed.
MAX_OUTPUT_TOKENS = 1200


class GeminiClient(LLMClient):
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_s: int = 60,
        thinking_budget: int | None = 0,
    ) -> None:
        if not api_key:
            raise PermanentLLMError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey, or run with --provider fake."
            )
        self.model = model
        self._thinking_budget = thinking_budget
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_s * 1000),
        )

    async def generate_json(self, *, system: str, user: str) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=TRIAGE_RESPONSE_SCHEMA,
            temperature=0.0,
            seed=0,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            # We expose no tools, so the SDK's automatic function calling is pure
            # overhead (and warns about it on every async call).
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self._thinking_budget is not None:
            # Triage is a short classification, not a reasoning problem: a zero
            # budget removes the thinking tokens without hurting quality on this
            # data. Set GEMINI_THINKING_BUDGET=auto to let the model decide.
            config.thinking_config = types.ThinkingConfig(
                thinking_budget=self._thinking_budget
            )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=user, config=config
            )
            # Parsing the response can fail too — a candidate holding an
            # unexpected shape can make the SDK's own `.text` property raise.
            # That failure belongs inside this same typed-error boundary, not
            # after it: anything escaping generate_json untyped defeats every
            # caller's LLMError handling and, upstream, the pipeline's
            # guarantee that a bad response never loses the row.
            self._raise_on_bad_finish(response)
            text = (response.text or "").strip()
        except errors.ClientError as exc:
            code = getattr(exc, "code", None)
            if code == 429 and _is_daily_quota(exc):
                limits = ", ".join(
                    f"{v.get('quotaValue', '?')} req/day"
                    for v in _quota_violations(exc)
                ) or "unknown"
                raise QuotaExhaustedError(
                    f"Daily free-tier quota exhausted for {self.model} ({limits}). "
                    "It resets on the provider's schedule, not in 59 seconds — "
                    "switch GEMINI_MODEL or wait."
                ) from exc
            if code in RETRYABLE_CLIENT_CODES:
                raise TransientLLMError(
                    f"Gemini rate limited or busy ({code}): {exc}",
                    retry_after_s=_retry_after(exc),
                ) from exc
            raise PermanentLLMError(f"Gemini rejected the request ({code}): {exc}") from exc
        except errors.ServerError as exc:
            raise TransientLLMError(
                f"Gemini server error: {exc}", retry_after_s=_retry_after(exc)
            ) from exc
        except errors.APIError as exc:
            raise TransientLLMError(f"Gemini API error: {exc}") from exc
        except LLMError:
            # Raised by _raise_on_bad_finish above (blocked / truncated
            # generation) — already the right typed error, pass it through
            # rather than re-wrapping it as a generic transport failure.
            raise
        except Exception as exc:  # network stack, timeouts, DNS, bad response shape
            raise TransientLLMError(f"Transport failure talking to Gemini: {exc}") from exc

        if not text:
            raise TransientLLMError("Gemini returned an empty response body")

        usage = response.usage_metadata
        # Thinking tokens are billed as output but reported separately, so fold
        # them in — otherwise the cost figures in the report understate reality.
        output = (getattr(usage, "candidates_token_count", None) or 0) + (
            getattr(usage, "thoughts_token_count", None) or 0
        )
        return LLMResponse(
            text=text,
            model=response.model_version or self.model,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=output or None,
        )

    @staticmethod
    def _raise_on_bad_finish(response: types.GenerateContentResponse) -> None:
        """Turn a silently-truncated or blocked generation into a typed error.

        Without this the caller sees empty or half-written JSON and blames the
        parser, which is a genuinely annoying hour to lose.
        """
        candidates = response.candidates or []
        if not candidates:
            raise TransientLLMError("Gemini returned no candidates")
        reason = getattr(candidates[0], "finish_reason", None)
        name = getattr(reason, "name", str(reason) if reason else "")
        if name == "MAX_TOKENS":
            raise TransientLLMError(
                f"Generation hit the {MAX_OUTPUT_TOKENS}-token cap and the JSON is truncated"
            )
        if name in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
            raise PermanentLLMError(f"Gemini blocked this request ({name})")
