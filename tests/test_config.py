"""Environment parsing.

BACKOFF_BASE_S used to be parsed with a bare ``float()`` call, unlike every
other numeric setting, so a bad value raised an unattributed ``ValueError``
instead of naming the offending variable.
"""

import pytest

from triage.config import _float_env
from triage.llm.base import PermanentLLMError


def test_float_env_returns_the_default_when_unset(monkeypatch):
    monkeypatch.delenv("BACKOFF_BASE_S", raising=False)
    assert _float_env("BACKOFF_BASE_S", 1.0) == 1.0


def test_float_env_parses_a_valid_value(monkeypatch):
    monkeypatch.setenv("BACKOFF_BASE_S", "2.5")
    assert _float_env("BACKOFF_BASE_S", 1.0) == 2.5


def test_float_env_names_the_variable_on_a_bad_value(monkeypatch):
    monkeypatch.setenv("BACKOFF_BASE_S", "fast")
    with pytest.raises(PermanentLLMError, match="BACKOFF_BASE_S"):
        _float_env("BACKOFF_BASE_S", 1.0)
