"""Runtime settings, read from the environment (and an optional .env file)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .llm.base import LLMClient, PermanentLLMError

# Pinned deliberately. Gemini aliases such as "gemini-flash-latest" move under
# you, which is the last thing you want when the report is supposed to be
# reproducible. Check availability with: python -m triage --list-models
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise PermanentLLMError(f"{name} must be an integer, got {raw!r}") from None


def _thinking_budget_env() -> int | None:
    """0 disables thinking, "auto" hands the decision to the model, N pins a budget."""
    raw = os.getenv("GEMINI_THINKING_BUDGET", "").strip().lower()
    if raw in {"auto", "default"}:
        return None
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        raise PermanentLLMError(
            f"GEMINI_THINKING_BUDGET must be an integer or 'auto', got {raw!r}"
        ) from None


@dataclass(frozen=True, slots=True)
class Settings:
    provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = DEFAULT_GEMINI_MODEL
    max_concurrency: int = 4
    max_attempts: int = 3
    request_timeout_s: int = 60
    backoff_base_s: float = 1.0
    thinking_budget: int | None = 0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False)
        return cls(
            provider=os.getenv("LLM_PROVIDER", "gemini").strip().lower(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip(),
            max_concurrency=_int_env("MAX_CONCURRENCY", 4),
            max_attempts=_int_env("MAX_ATTEMPTS", 3),
            request_timeout_s=_int_env("REQUEST_TIMEOUT_S", 60),
            backoff_base_s=float(os.getenv("BACKOFF_BASE_S", "1.0")),
            thinking_budget=_thinking_budget_env(),
        )


def build_client(settings: Settings) -> LLMClient:
    """Resolve the configured provider into a client.

    Adding a vendor is a branch here plus a module in ``triage/llm/``. Note that
    ``fake`` is never selected implicitly — a missing key raises rather than
    quietly degrading to stub output.
    """
    if settings.provider == "gemini":
        from .llm.gemini import GeminiClient

        return GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_s=settings.request_timeout_s,
            thinking_budget=settings.thinking_budget,
        )
    if settings.provider == "fake":
        from .llm.fake import FakeClient

        return FakeClient()
    raise PermanentLLMError(
        f"Unknown LLM_PROVIDER {settings.provider!r}. Supported: gemini, fake."
    )
