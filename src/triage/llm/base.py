"""Provider-agnostic contract for the one thing we need: JSON in, JSON out.

Keeping this interface deliberately narrow is what lets the pipeline own retry,
repair and validation once, instead of once per vendor. Swapping Gemini for
Anthropic or OpenAI means adding a file here and one line in ``build_client``.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMError(Exception):
    """Base class for provider failures."""


class TransientLLMError(LLMError):
    """Worth retrying: rate limits, 5xx, timeouts, truncated responses.

    ``retry_after_s`` carries the provider's own advice when it gives any. A
    server that says "retry in 10s" knows more about its quota window than our
    exponential backoff curve does, so we believe it.
    """

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class PermanentLLMError(LLMError):
    """Not worth retrying: bad key, bad request, model not found."""


class QuotaExhaustedError(PermanentLLMError):
    """A per-day quota is gone. Not worth retrying, and not just for this row.

    Providers report daily and per-minute exhaustion through the same 429 with
    the same short ``retryDelay``. Treating them alike is expensive: obeying a
    "retry in 59s" hint on a quota that resets tomorrow turned one dead run into
    25 minutes of grinding through every remaining row.
    """


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient(ABC):
    """Minimal async client interface."""

    name: str
    model: str

    @abstractmethod
    async def generate_json(self, *, system: str, user: str) -> LLMResponse:
        """Return a JSON document as text. Raises ``LLMError`` subclasses."""

    async def aclose(self) -> None:  # pragma: no cover - most clients need nothing
        return None
