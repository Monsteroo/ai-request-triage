"""Serialisation of the run: the full JSON result and the human-readable report.

``output.json`` is an object rather than a bare array on purpose — it carries
the run's provenance (when, which model, how many tokens, how many failures)
next to the data. A bare array tells you what was decided but not by whom or at
what cost, which is exactly what you want to know when two runs disagree.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .models import Category, Department, Priority, ProcessedRequest
from .pipeline import RunStats

UNSET_DEPARTMENT = "не визначено"


def _counts(records: list[ProcessedRequest], attr: str, order: list[str]) -> dict[str, int]:
    """Count one field across successful records, keeping the vocabulary order.

    Ordering by the enum rather than by frequency means two runs produce
    diff-able reports even when the numbers move.
    """
    counter: Counter[str] = Counter()
    for record in records:
        if record.triage is None:
            continue
        value = getattr(record.triage, attr)
        if value is None:
            counter[UNSET_DEPARTMENT] += 1
        else:
            counter[getattr(value, "value", str(value))] += 1
    ordered = {key: counter.get(key, 0) for key in order}
    for key, count in counter.items():
        ordered.setdefault(key, count)
    return ordered


def build_output_document(
    records: list[ProcessedRequest], stats: RunStats, *, source: str, provider: str, model: str
) -> dict:
    return {
        "schema_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "provider": provider,
        "model": model,
        "stats": {
            "total": stats.total,
            "ok": stats.ok,
            "failed": stats.failed,
            "llm_calls": stats.llm_calls,
            "prompt_tokens": stats.prompt_tokens,
            "output_tokens": stats.output_tokens,
            "wall_time_s": round(stats.wall_time_s, 2),
            "rules_fired": stats.rules_fired,
        },
        "requests": [record.dump() for record in records],
    }


def write_json(document: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _table(title: str, counts: dict[str, int], total: int) -> list[str]:
    lines = [f"### {title}", "", "| Значення | Кількість | Частка |", "|---|---:|---:|"]
    for key, count in counts.items():
        share = f"{count / total * 100:.0f}%" if total else "—"
        lines.append(f"| {key} | {count} | {share} |")
    lines.append("")
    return lines


def _escape(text: str, limit: int = 160) -> str:
    """Flatten a value so it cannot break out of a markdown table cell."""
    flat = " ".join(str(text).split())
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat.replace("|", "\\|")


def build_report(
    records: list[ProcessedRequest], stats: RunStats, *, source: str, provider: str, model: str
) -> str:
    ok_records = [r for r in records if r.status == "ok" and r.triage is not None]
    failed = [r for r in records if r.status != "ok"]
    total_ok = len(ok_records)

    lines: list[str] = [
        "# Звіт тріажу запитів",
        "",
        f"- **Джерело:** `{source}`",
        f"- **Згенеровано:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- **Провайдер / модель:** {provider} / `{model}`",
        f"- **Оброблено:** {stats.total} запит(ів) — {stats.ok} успішно, {stats.failed} з помилкою",
        f"- **Викликів до LLM:** {stats.llm_calls} · **токенів:** {stats.total_tokens:,}"
        f" ({stats.prompt_tokens:,} вхідних + {stats.output_tokens:,} вихідних)",
        f"- **Час виконання:** {stats.wall_time_s:.1f} с",
        "",
        "## Агрегати",
        "",
    ]

    lines += _table("За категорією", _counts(ok_records, "category", [c.value for c in Category]), total_ok)
    lines += _table("За пріоритетом", _counts(ok_records, "priority", [p.value for p in Priority]), total_ok)
    lines += _table(
        "За відділом",
        _counts(ok_records, "target_department", [d.value for d in Department] + [UNSET_DEPARTMENT]),
        total_ok,
    )

    # --- clarification queue ------------------------------------------------
    needs = [r for r in ok_records if r.triage and r.triage.needs_clarification]
    lines += [
        f"## Потребують уточнення ({len(needs)})",
        "",
        "Ці запити не можна брати в роботу як є.",
        "",
    ]
    if needs:
        lines += ["| ID | Канал | Суть | Що спитати | Впевненість |", "|---|---|---|---|---:|"]
        for record in needs:
            triage = record.triage
            assert triage is not None
            questions = "<br>".join(_escape(q, 90) for q in triage.clarifying_questions) or "—"
            lines.append(
                f"| {record.id} | {record.channel} | {_escape(triage.short_summary)} "
                f"| {questions} | {triage.confidence:.2f} |"
            )
    else:
        lines.append("_Немає — усі запити достатньо конкретні._")
    lines.append("")

    # --- high priority ------------------------------------------------------
    urgent = [r for r in ok_records if r.triage and r.triage.priority is Priority.HIGH]
    lines += [f"## Високий пріоритет ({len(urgent)})", ""]
    if urgent:
        lines += ["| ID | Канал | Категорія | Суть |", "|---|---|---|---|"]
        for record in urgent:
            triage = record.triage
            assert triage is not None
            lines.append(
                f"| {record.id} | {record.channel} | {triage.category.value} "
                f"| {_escape(triage.short_summary)} |"
            )
    else:
        lines.append("_Немає._")
    lines.append("")

    # --- failures -----------------------------------------------------------
    lines += [f"## Не вдалося обробити ({len(failed)})", ""]
    if failed:
        lines += ["| ID | Тип помилки | Спроб | Деталі |", "|---|---|---:|---|"]
        for record in failed:
            error = record.error
            lines.append(
                f"| {record.id} | {error.kind if error else '?'} | {record.meta.attempts} "
                f"| {_escape(error.message if error else '', 120)} |"
            )
    else:
        lines.append("_Немає — усі запити пройшли валідацію._")
    lines.append("")

    # --- post-processing transparency --------------------------------------
    if stats.rules_fired:
        lines += [
            "## Спрацювали бізнес-правила",
            "",
            "Детерміновані пост-правила, що узгоджують суперечності у відповіді моделі.",
            "",
            "| Правило | Разів |",
            "|---|---:|",
        ]
        lines += [f"| `{rule}` | {count} |" for rule, count in sorted(stats.rules_fired.items())]
        lines.append("")

    return "\n".join(lines)


def write_report(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
