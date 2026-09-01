"""Command-line entry point.

Exit codes are meaningful so this can sit in a cron job or a CI step:
``0`` everything triaged, ``2`` finished but some rows failed, ``1`` the run
itself could not start or complete.
"""

import argparse
import asyncio
import dataclasses
import logging
import sys
from pathlib import Path

from .config import Settings, build_client
from .llm.base import LLMError
from .models import ProcessedRequest
from .pipeline import triage_all
from .reader import read_requests
from .report import build_output_document, build_report, write_json, write_report

logger = logging.getLogger("triage")

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PARTIAL = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="triage",
        description="Classify free-form internal requests with an LLM into a strict schema.",
    )
    parser.add_argument(
        "--input", type=Path, default=Path("data/input_requests.csv"), help="input CSV"
    )
    parser.add_argument(
        "--outdir", type=Path, default=Path("output"), help="directory for output.json / report.md"
    )
    parser.add_argument(
        "--provider",
        choices=("gemini", "fake"),
        help="override LLM_PROVIDER; 'fake' runs offline with a deterministic stub",
    )
    parser.add_argument("--model", help="override the model id")
    parser.add_argument("--concurrency", type=int, help="max in-flight LLM calls")
    parser.add_argument(
        "--rpm",
        type=int,
        help="calls per minute to pace at; 0 disables pacing (free tier allows 5)",
    )
    parser.add_argument(
        "--limit", type=int, help="process only the first N rows (handy for a cheap smoke test)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def _resolve_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    overrides = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["gemini_model"] = args.model
    if args.concurrency:
        overrides["max_concurrency"] = args.concurrency
    if args.rpm is not None:
        overrides["requests_per_minute"] = args.rpm
    return dataclasses.replace(settings, **overrides) if overrides else settings


async def run(args: argparse.Namespace) -> int:
    settings = _resolve_settings(args)
    requests = read_requests(args.input)
    if args.limit is not None:  # `is not None`, not truthiness: --limit 0 must mean zero rows
        requests = requests[: args.limit]
    if not requests:
        logger.error("No requests found in %s", args.input)
        return EXIT_FATAL

    client = build_client(settings)
    logger.info(
        "Triaging %d request(s) via %s/%s, concurrency=%d, rpm=%s",
        len(requests),
        settings.provider,
        client.model,
        settings.max_concurrency,
        settings.requests_per_minute or "unlimited",
    )

    done = 0

    def progress(record: ProcessedRequest) -> None:
        nonlocal done
        done += 1
        mark = "ok " if record.status == "ok" else "FAIL"
        logger.info("[%d/%d] %s %s", done, len(requests), mark, record.id)

    try:
        records, stats = await triage_all(requests, client, settings, on_done=progress)
    finally:
        await client.aclose()

    document = build_output_document(
        records,
        stats,
        source=str(args.input),
        provider=settings.provider,
        model=client.model,
    )
    report = build_report(
        records, stats, source=str(args.input), provider=settings.provider, model=client.model
    )

    json_path = args.outdir / "output.json"
    report_path = args.outdir / "report.md"
    write_json(document, json_path)
    write_report(report, report_path)

    logger.info(
        "Done in %.1fs — %d ok, %d failed, %d LLM call(s), %d token(s)",
        stats.wall_time_s,
        stats.ok,
        stats.failed,
        stats.llm_calls,
        stats.total_tokens,
    )
    logger.info("Wrote %s and %s", json_path, report_path)
    return EXIT_PARTIAL if stats.failed else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    # google-genai is chatty about its HTTP layer at DEBUG level.
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        return asyncio.run(run(args))
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return EXIT_FATAL
    except LLMError as exc:
        logger.error("LLM configuration problem: %s", exc)
        return EXIT_FATAL
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
