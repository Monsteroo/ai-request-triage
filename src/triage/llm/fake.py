"""Deterministic offline stub.

It exists for two reasons:

* tests must be hermetic — no network, no key, no spend, no flakiness;
* a reviewer can clone this repo and run the whole pipeline end to end with
  ``--provider fake`` before deciding whether to provision an API key.

It is a keyword matcher, not a classifier. It is never a fallback for the real
provider at runtime: you have to ask for it explicitly, so nobody can mistake
stub output for model output.
"""

import json
import re

from ..models import Category, Department, Priority
from .base import LLMClient, LLMResponse, TransientLLMError

_TEXT_BLOCK = re.compile(r"<<<REQUEST_TEXT_START>>>\n(.*?)\n<<<REQUEST_TEXT_END>>>", re.S)

_RULES: list[tuple[Category, tuple[str, ...]]] = [
    (Category.OUT_OF_SCOPE, ("дякую", "супер", "класно", "ноутбук", "закупити", "бюджет")),
    (Category.BUG, ("зламал", "не парситься", "полагодити", "не працює", "висять")),
    (Category.QUESTION, ("теоретичне", "просто цікаво", "це баг чи", "хочу зрозуміти", "куди подати")),
    (Category.INTEGRATION, ("інтеграці", "автоматом створювались", "падало в", "з планфіксом")),
    (Category.REPORTING, ("звіт", "дашборд", "дайджест", "саммарі", "вивантажити", "аномалі", "summary")),
]

_SYSTEM_HINTS = (
    "Google Ads", "BigQuery", "PlanFix", "Slack", "Telegram", "Meta",
    "Google Doc", "Google Sheet", "PDF",
)

_DEPARTMENT_HINTS: list[tuple[Department, tuple[str, ...]]] = [
    (Department.SALES, ("продаж", "угоди")),
    (Department.HR, ("hr", "резюме", "скринінг")),
    (Department.FINANCE, ("бухгалтер", "контрагент", "інвойс", "рахунк")),
    (Department.ANALYTICS, ("аналітик", "bigquery")),
    (Department.CONTENT, ("контент", "блог", "стат")),
    (Department.SMM, ("smm", "соцмереж")),
    (Department.MARKETING, ("google ads", "кампані", "згадок про netpeak")),
    (Department.SUPPORT, ("#support", "підтримк")),
]


class FakeClient(LLMClient):
    """Rule-based stand-in for a real model.

    ``fail_for`` lets tests drive the error paths: map a substring of the
    request text to ``"invalid_json"``, ``"schema_violation"`` or ``"transient"``.
    """

    name = "fake"

    def __init__(self, model: str = "fake-1", fail_for: dict[str, str] | None = None) -> None:
        self.model = model
        self._fail_for = fail_for or {}
        self.calls = 0

    async def generate_json(self, *, system: str, user: str) -> LLMResponse:
        self.calls += 1
        match = _TEXT_BLOCK.search(user)
        text = (match.group(1) if match else user).strip()
        lowered = text.casefold()

        for needle, mode in self._fail_for.items():
            if needle.casefold() in lowered:
                return self._fail(mode, text)

        return LLMResponse(
            text=json.dumps(self._classify(text, lowered), ensure_ascii=False),
            model=self.model,
            prompt_tokens=len(system) // 4,
            output_tokens=120,
        )

    def _fail(self, mode: str, text: str) -> LLMResponse:
        if mode == "transient":
            raise TransientLLMError("simulated rate limit")
        if mode == "invalid_json":
            return LLMResponse(text="```json\n{oops, not json", model=self.model)
        if mode == "schema_violation":
            return LLMResponse(
                text=json.dumps(
                    {
                        "category": "щось вигадане",
                        "priority": "urgent",
                        "short_summary": "",
                        "requested_actions": [],
                        "needs_clarification": "maybe",
                        "confidence": 7,
                        "clarifying_questions": [],
                        "mentioned_systems": [],
                        "is_actionable": True,
                    },
                    ensure_ascii=False,
                ),
                model=self.model,
            )
        raise AssertionError(f"unknown failure mode {mode!r}")

    @staticmethod
    def _classify(text: str, lowered: str) -> dict:
        category = Category.AUTOMATION
        for candidate, needles in _RULES:
            if any(n in lowered for n in needles):
                category = candidate
                break

        department = None
        for candidate, needles in _DEPARTMENT_HINTS:
            if any(n in lowered for n in needles):
                department = candidate.value
                break

        if any(w in lowered for w in ("горить", "терміново", "сьогодні до вечора")):
            priority = Priority.HIGH
        elif any(w in lowered for w in ("не горить", "просто цікаво", "дякую")):
            priority = Priority.LOW
        else:
            priority = Priority.MEDIUM

        actionable = category is not Category.OUT_OF_SCOPE or "ноутбук" in lowered
        vague = len(text) < 40 and actionable

        return {
            "category": category.value,
            "target_department": department,
            "priority": priority.value,
            "short_summary": (text[:180] or "порожній запит").replace("\n", " "),
            "requested_actions": [] if not actionable else [text[:120].replace("\n", " ")],
            "needs_clarification": vague,
            "confidence": 0.35 if vague else 0.8,
            "clarifying_questions": ["Що саме треба зробити?"] if vague else [],
            "mentioned_systems": [s for s in _SYSTEM_HINTS if s.casefold() in lowered],
            "is_actionable": actionable,
        }
