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
