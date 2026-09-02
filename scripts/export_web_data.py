"""Turn a pipeline run into the JSON the demo page loads.

The board on the demo page is seeded with real output, not invented examples —
this script is what makes that claim true and repeatable. It keeps only the
fields the page renders: raw model replies, token counts and per-attempt
metadata stay out of a file served to the public internet.

Usage:
    python scripts/export_web_data.py
    python scripts/export_web_data.py --input output/output.json --output web/public/data/baseline.json
"""

import argparse
import json
import sys
from pathlib import Path

# Exactly the fields the page renders. Anything not listed here never reaches
# the browser — see the test that pins this list.
TRIAGE_FIELDS = (
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
)


def build_payload(document: dict) -> dict:
    """Reduce a full run document to what the demo page needs."""
    requests = []
    for record in document.get("requests", []):
        triage = record.get("triage")
        if record.get("status") != "ok" or not triage:
            # A failed row has nothing to place on the board. The page seeds
            # from a clean run, so this is a data problem worth surfacing.
            continue
        requests.append(
            {
                "id": record["id"],
                "channel": record.get("channel", ""),
                "timestamp": record.get("timestamp"),
                "raw_text": record.get("raw_text", ""),
                "triage": {field: triage.get(field) for field in TRIAGE_FIELDS},
            }
        )

    stats = document.get("stats", {})
    return {
        "generated_at": document.get("generated_at"),
        "model": document.get("model"),
        "source_run": {
            "total": stats.get("total"),
            "ok": stats.get("ok"),
            "failed": stats.get("failed"),
            "llm_calls": stats.get("llm_calls"),
            "prompt_tokens": stats.get("prompt_tokens"),
            "output_tokens": stats.get("output_tokens"),
            "wall_time_s": stats.get("wall_time_s"),
        },
        "requests": requests,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("output/output.json"))
    parser.add_argument("--output", type=Path, default=Path("web/public/data/baseline.json"))
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(
            f"{args.input} not found — run the pipeline first: python -m triage",
            file=sys.stderr,
        )
        return 1

    document = json.loads(args.input.read_text(encoding="utf-8"))
    payload = build_payload(document)

    if not payload["requests"]:
        print(f"{args.input} has no successful rows to export", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(payload['requests'])} request(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
