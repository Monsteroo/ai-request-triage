"""Business rules applied *after* schema validation.

Schema validation answers "is this well-formed?". These rules answer "is this
self-consistent?" — a model can return a perfectly typed object that still
contradicts itself (``needs_clarification=false`` next to ``confidence=0.2``).

Every rule is deterministic, cheap, and records what it changed in
``meta.applied_rules``, so a reviewer can always tell the model's raw opinion
apart from our post-processing.
"""

from .models import Category, Priority, TriageFields

# Below this, we do not trust the model enough to route the request unattended.
CONFIDENCE_FLOOR = 0.5


def apply_business_rules(triage: TriageFields) -> tuple[TriageFields, list[str]]:
    """Return a reconciled copy plus the list of rule ids that fired."""
    fired: list[str] = []
    patch: dict = {}

    # R1 — concrete asks beat the boolean. If the model listed actions but also
    # said the request is not actionable, the actions are the stronger signal.
    if not triage.is_actionable and triage.requested_actions:
        patch["is_actionable"] = True
        fired.append("R1:actionable_implied_by_actions")

    # R2 — out of the AI unit's scope means it is not our high-priority work,
    # however loudly it was phrased. It still shows up in the report.
    if triage.category is Category.OUT_OF_SCOPE and triage.priority is Priority.HIGH:
        patch["priority"] = Priority.MEDIUM
        fired.append("R2:out_of_scope_priority_capped")

    # R3 — low confidence is itself a reason for a human to look at the request.
    if triage.confidence < CONFIDENCE_FLOOR and not triage.needs_clarification:
        patch["needs_clarification"] = True
        fired.append("R3:low_confidence_forces_clarification")

    # R4 — we cannot invent the questions, so we only flag the gap. The report
    # surfaces it; a human still gets a useful "this one is vague" signal.
    if triage.needs_clarification and not triage.clarifying_questions:
        fired.append("R4:clarification_without_questions")

    if not patch:
        return triage, fired
    return triage.model_copy(update=patch), fired
