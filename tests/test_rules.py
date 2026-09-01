from triage.models import Category, Priority, TriageFields
from triage.rules import apply_business_rules

BASE = {
    "category": "автоматизація",
    "priority": "medium",
    "short_summary": "щось корисне",
    "needs_clarification": False,
    "confidence": 0.9,
}


def test_no_rules_fire_on_a_consistent_result():
    triage, fired = apply_business_rules(TriageFields(**BASE))
    assert fired == []


def test_listed_actions_override_is_actionable_false():
    triage, fired = apply_business_rules(
        TriageFields(**BASE, is_actionable=False, requested_actions=["зробити звіт"])
    )
    assert triage.is_actionable is True
    assert "R1:actionable_implied_by_actions" in fired


def test_out_of_scope_cannot_stay_high_priority():
    triage, fired = apply_business_rules(
        TriageFields(**{**BASE, "category": "поза скоупом", "priority": "high"})
    )
    assert triage.priority is Priority.MEDIUM
    assert triage.category is Category.OUT_OF_SCOPE
    assert "R2:out_of_scope_priority_capped" in fired


def test_low_confidence_forces_a_human_look():
    triage, fired = apply_business_rules(TriageFields(**{**BASE, "confidence": 0.2}))
    assert triage.needs_clarification is True
    assert "R3:low_confidence_forces_clarification" in fired


def test_clarification_without_questions_is_flagged_not_fixed():
    triage, fired = apply_business_rules(
        TriageFields(**{**BASE, "needs_clarification": True, "clarifying_questions": []})
    )
    assert triage.clarifying_questions == []
    assert "R4:clarification_without_questions" in fired


def test_rules_do_not_mutate_the_input():
    original = TriageFields(**{**BASE, "confidence": 0.2})
    apply_business_rules(original)
    assert original.needs_clarification is False
