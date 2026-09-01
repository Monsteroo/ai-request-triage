"""GeminiClient error typing.

``generate_json`` promises to raise only ``LLMError`` subclasses — every
caller's retry/repair logic depends on that. Parsing the response used to sit
outside the try/except that enforces it, so a malformed candidate could raise
an untyped exception straight out of the client.

No network call is made: ``self._client.aio.models.generate_content`` is
replaced with a stub that returns a crafted response object, exercising only
this module's own error handling.
"""

import pytest

from triage.llm.base import LLMError, TransientLLMError
from triage.llm.gemini import GeminiClient


class _FinishReason:
    def __init__(self, name: str) -> None:
        self.name = name


class _Candidate:
    def __init__(self, finish_reason=None) -> None:
        self.finish_reason = finish_reason


class _Usage:
    prompt_token_count = 42
    candidates_token_count = 7
    thoughts_token_count = 0


class _GoodResponse:
    candidates = [_Candidate()]
    usage_metadata = _Usage()
    model_version = "test-model-v1"
    text = '{"ok": true}'


class _TruncatedResponse:
    """A generation cut off by the token cap — _raise_on_bad_finish's own case."""

    candidates = [_Candidate(finish_reason=_FinishReason("MAX_TOKENS"))]
    usage_metadata = None
    model_version = "test-model-v1"
    text = "{incomplete"


class _MalformedTextResponse:
    """Simulates google-genai's `.text` property raising on an unexpected
    candidate shape — a real failure mode, not a hypothetical one."""

    candidates = [_Candidate()]
    usage_metadata = None
    model_version = "test-model-v1"

    @property
    def text(self):
        raise AttributeError("simulated malformed candidate")


def _client_with_response(monkeypatch, response):
    client = GeminiClient(api_key="test-key", model="test-model")

    async def fake_generate_content(*, model, contents, config):
        return response

    monkeypatch.setattr(client._client.aio.models, "generate_content", fake_generate_content)
    return client


async def test_a_well_formed_response_still_returns_normally(monkeypatch):
    """Sanity check that restructuring the try/except did not break the
    ordinary path: parsing now happens inside the same try block."""
    client = _client_with_response(monkeypatch, _GoodResponse())

    result = await client.generate_json(system="sys", user="usr")

    assert result.text == '{"ok": true}'
    assert result.prompt_tokens == 42
    assert result.output_tokens == 7


async def test_truncated_generation_raises_its_specific_error_unchanged(monkeypatch):
    """_raise_on_bad_finish already raises the right typed error; the broader
    except Exception added to catch parsing bugs must not re-wrap it as a
    generic transport failure."""
    client = _client_with_response(monkeypatch, _TruncatedResponse())

    with pytest.raises(TransientLLMError, match="token cap"):
        await client.generate_json(system="sys", user="usr")


async def test_a_response_whose_text_accessor_raises_becomes_a_typed_error(monkeypatch):
    """Reproduces the bug directly: response.text raising used to escape
    generate_json untyped, which upstream defeats triage_one's LLMError
    handling and the pipeline's guarantee that a bad response never loses
    the row."""
    client = _client_with_response(monkeypatch, _MalformedTextResponse())

    with pytest.raises(LLMError):
        await client.generate_json(system="sys", user="usr")
