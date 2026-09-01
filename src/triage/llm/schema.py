"""The response schema handed to the provider's structured-output mode.

Constraining generation at the API level is the cheapest possible guardrail —
it removes the whole class of "model wrapped the JSON in ```json fences" bugs.
It is *not* a substitute for validating with pydantic afterwards: it cannot
express our cross-field rules, and a provider without the feature would silently
lose the guarantee. Both layers stay.

``target_department`` is intentionally absent from ``required``: an enum field
that must be present pushes the model into picking *some* department rather than
admitting it does not know. Omitted means null, which is what we want there —
and precisely what we do *not* want for ``domain``, which is required.
"""

from ..models import Category, Department, Priority

_STRING_LIST = {"type": "ARRAY", "items": {"type": "STRING"}}

TRIAGE_RESPONSE_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "category": {"type": "STRING", "enum": [c.value for c in Category]},
        "target_department": {
            "type": "STRING",
            "enum": [d.value for d in Department],
            "nullable": True,
        },
        # Unlike target_department this one *is* required: we want the model to
        # actively decide the subject area rather than quietly omit it.
        "domain": {
            "type": "STRING",
            "enum": [d.value for d in Department],
            "nullable": True,
        },
        "priority": {"type": "STRING", "enum": [p.value for p in Priority]},
        "short_summary": {"type": "STRING"},
        "requested_actions": _STRING_LIST,
        "needs_clarification": {"type": "BOOLEAN"},
        "confidence": {"type": "NUMBER"},
        "clarifying_questions": _STRING_LIST,
        "mentioned_systems": _STRING_LIST,
        "is_actionable": {"type": "BOOLEAN"},
    },
    "required": [
        "category",
        "domain",
        "priority",
        "short_summary",
        "requested_actions",
        "needs_clarification",
        "confidence",
        "clarifying_questions",
        "mentioned_systems",
        "is_actionable",
    ],
    "property_ordering": [
        "category",
        "target_department",
        "domain",
        "priority",
        "short_summary",
        "requested_actions",
        "needs_clarification",
        "confidence",
        "clarifying_questions",
        "mentioned_systems",
        "is_actionable",
    ],
}
