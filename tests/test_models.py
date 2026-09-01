import pytest
from pydantic import ValidationError

from triage.models import Category, Department, Priority, TriageFields

BASE = {
    "category": "автоматизація",
    "priority": "medium",
    "short_summary": "щось корисне",
    "requested_actions": [],
    "needs_clarification": False,
    "confidence": 0.8,
}


def test_accepts_a_well_formed_payload():
    t = TriageFields(**BASE, target_department="маркетинг")
    assert t.category is Category.AUTOMATION
    assert t.priority is Priority.MEDIUM
    assert t.target_department is Department.MARKETING


@pytest.mark.parametrize("value", ["", "null", "невідомо", "n/a", "  -  "])
def test_nullish_department_becomes_none(value):
    assert TriageFields(**BASE, target_department=value).target_department is None


def test_bare_string_is_lifted_into_a_list():
    t = TriageFields(**{**BASE, "requested_actions": "зробити звіт"})
    assert t.requested_actions == ["зробити звіт"]


def test_none_lists_become_empty_lists():
    t = TriageFields(**{**BASE, "clarifying_questions": None, "mentioned_systems": None})
    assert t.clarifying_questions == [] and t.mentioned_systems == []


def test_actions_are_deduplicated_and_whitespace_normalised():
    t = TriageFields(**{**BASE, "requested_actions": ["Зробити звіт", "зробити  звіт", "  ", "Інше"]})
    assert t.requested_actions == ["Зробити звіт", "Інше"]


@pytest.mark.parametrize(
    "override",
    [
        {"category": "щось вигадане"},
        {"priority": "urgent"},
        {"confidence": 7},
        {"confidence": -0.1},
        {"short_summary": ""},
        {"needs_clarification": "можливо"},
    ],
)
def test_invalid_payloads_are_rejected(override):
    with pytest.raises(ValidationError):
        TriageFields(**{**BASE, **override})


def test_hallucinated_fields_are_rejected():
    with pytest.raises(ValidationError) as exc:
        TriageFields(**BASE, urgency_score=9)
    assert exc.value.errors()[0]["type"] == "extra_forbidden"
