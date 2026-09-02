"""Generate the Worker's copy of the prompt and response schema from Python.

The demo's Worker is a JS twin of the Python pipeline, and a twin that is
maintained by hand drifts. This script removes that risk for the part that
matters most: the *contract* — the system prompt, the controlled vocabularies
and the response schema — is generated from the same modules the pipeline uses,
so editing a category in `models.py` updates the Worker on the next build.

What is deliberately NOT generated: the orchestration. The Worker keeps its own
simplified retry, because Python's retry/repair/RunGuard logic does not
translate to a request-scoped edge function.

Usage:
    python scripts/export_worker_contract.py
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from triage.llm.schema import TRIAGE_RESPONSE_SCHEMA  # noqa: E402
from triage.models import Category, Department, Priority  # noqa: E402
from triage.prompts import FEW_SHOT, SYSTEM_PROMPT  # noqa: E402

# The Python SDK takes snake_case schema keys; the REST API the Worker calls
# takes camelCase. Same schema, different spelling.
_REST_KEY = {"property_ordering": "propertyOrdering"}


def to_rest_schema(schema: dict) -> dict:
    """Rewrite the SDK-shaped schema into what the REST endpoint expects."""
    out: dict = {}
    for key, value in schema.items():
        rest_key = _REST_KEY.get(key, key)
        if isinstance(value, dict):
            out[rest_key] = to_rest_schema(value)
        elif isinstance(value, list):
            out[rest_key] = [
                to_rest_schema(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            out[rest_key] = value
    return out


def build_module() -> str:
    payload = {
        "systemPrompt": SYSTEM_PROMPT,
        "fewShot": FEW_SHOT,
        "responseSchema": to_rest_schema(TRIAGE_RESPONSE_SCHEMA),
        "vocabularies": {
            "category": [c.value for c in Category],
            "priority": [p.value for p in Priority],
            "department": [d.value for d in Department],
        },
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "// GENERATED FILE — do not edit by hand.\n"
        "// Source of truth: src/triage/prompts.py, src/triage/models.py,\n"
        "// src/triage/llm/schema.py. Regenerate with:\n"
        "//     python scripts/export_worker_contract.py\n"
        "//\n"
        "// Generating this instead of copying it is what keeps the Worker's\n"
        "// classification contract identical to the Python pipeline's.\n"
        f"export const CONTRACT = {body};\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("web/worker/generated/contract.js")
    )
    args = parser.parse_args(argv)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_module(), encoding="utf-8")
    print(f"wrote {args.output} ({args.output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
