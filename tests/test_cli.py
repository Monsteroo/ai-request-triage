"""CLI argument handling.

The interesting bug here was a truthiness check: ``if args.limit:`` treats 0
and "unset" as the same thing, so ``--limit 0`` silently ran the whole inbox
instead of nothing.
"""

import json

from triage.cli import EXIT_FATAL, EXIT_OK, build_parser, run

CSV_HEADER = "id,channel,timestamp,raw_text\n"


def _write_csv(tmp_path, rows: int):
    lines = [
        f"REQ-{i},Slack,2026-06-08 09:{i:02d},текст запиту номер {i}" for i in range(rows)
    ]
    path = tmp_path / "in.csv"
    path.write_text(CSV_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    return path


async def test_limit_zero_processes_nothing(tmp_path):
    """--limit 0 must mean zero rows, not 'no limit was given'."""
    csv_path = _write_csv(tmp_path, rows=3)
    outdir = tmp_path / "out"
    args = build_parser().parse_args(
        ["--input", str(csv_path), "--outdir", str(outdir), "--provider", "fake", "--limit", "0"]
    )

    exit_code = await run(args)

    assert exit_code == EXIT_FATAL
    assert not (outdir / "output.json").exists()


async def test_limit_one_processes_exactly_one_row(tmp_path):
    csv_path = _write_csv(tmp_path, rows=3)
    outdir = tmp_path / "out"
    args = build_parser().parse_args(
        ["--input", str(csv_path), "--outdir", str(outdir), "--provider", "fake", "--limit", "1"]
    )

    exit_code = await run(args)

    assert exit_code == EXIT_OK
    document = json.loads((outdir / "output.json").read_text(encoding="utf-8"))
    assert document["stats"]["total"] == 1
    assert document["requests"][0]["id"] == "REQ-0"


async def test_unset_limit_processes_every_row(tmp_path):
    csv_path = _write_csv(tmp_path, rows=3)
    outdir = tmp_path / "out"
    args = build_parser().parse_args(
        ["--input", str(csv_path), "--outdir", str(outdir), "--provider", "fake"]
    )

    exit_code = await run(args)

    assert exit_code == EXIT_OK
    document = json.loads((outdir / "output.json").read_text(encoding="utf-8"))
    assert document["stats"]["total"] == 3
