"""Measure how stable the triage output actually is across repeated runs.

``temperature=0`` and a fixed seed are necessary for reproducibility but not
sufficient — providers batch requests, route across mixture-of-experts layers
and update models behind the scenes. The honest thing is to measure the drift
rather than claim there is none.

Usage:
    PYTHONPATH=src python scripts/check_determinism.py --limit 5 --runs 2
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from triage.config import Settings, build_client  # noqa: E402
from triage.pipeline import triage_all  # noqa: E402
from triage.reader import read_requests  # noqa: E402

COMPARED = ("category", "priority", "target_department", "needs_clarification")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/input_requests.csv"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()

    settings = Settings.from_env()
    requests = read_requests(args.input)[: args.limit]
    client = build_client(settings)

    seen: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    summaries: dict[str, set[str]] = defaultdict(set)

    for run in range(1, args.runs + 1):
        print(f"run {run}/{args.runs} ...", flush=True)
        records, _ = await triage_all(requests, client, settings)
        for record in records:
            if record.triage is None:
                continue
            for field in COMPARED:
                value = getattr(record.triage, field)
                seen[record.id][field].add(getattr(value, "value", value))
            summaries[record.id].add(record.triage.short_summary)

    await client.aclose()

    print(f"\n{'ID':<10} {'поля':<8} {'саммарі':<10} що розійшлося")
    unstable_fields = 0
    for rid in sorted(seen):
        drifted = [f for f in COMPARED if len(seen[rid][f]) > 1]
        unstable_fields += len(drifted)
        detail = ", ".join(f"{f}={sorted(seen[rid][f])}" for f in drifted) or "—"
        print(
            f"{rid:<10} {'стабільні' if not drifted else 'РІЗНІ':<8} "
            f"{'однакове' if len(summaries[rid]) == 1 else 'РІЗНЕ':<10} {detail}"
        )

    identical_summaries = sum(1 for rid in summaries if len(summaries[rid]) == 1)
    print(
        f"\nПоля класифікації: {len(seen) * len(COMPARED) - unstable_fields}"
        f"/{len(seen) * len(COMPARED)} стабільні між прогонами."
    )
    print(f"Дослівно однакове саммарі: {identical_summaries}/{len(summaries)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
