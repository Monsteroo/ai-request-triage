import json

from triage.models import ErrorInfo, ProcessedRequest, ProcessingMeta, TriageFields
from triage.pipeline import RunStats
from triage.report import build_output_document, build_report, write_json


def make(rid, *, category="автоматизація", priority="medium", department=None, domain=None,
         clarify=False, questions=None, summary="суть запиту", status="ok"):
    triage = None
    if status == "ok":
        triage = TriageFields(
            category=category,
            target_department=department,
            domain=domain,
            priority=priority,
            short_summary=summary,
            requested_actions=["дія"],
            needs_clarification=clarify,
            confidence=0.4 if clarify else 0.9,
            clarifying_questions=questions or ([] if not clarify else ["Що саме?"]),
        )
    return ProcessedRequest(
        id=rid,
        channel="Slack",
        raw_text="текст",
        status=status,
        triage=triage,
        error=None if status == "ok" else ErrorInfo(kind="validation", message="зламалось"),
        meta=ProcessingMeta(attempts=1, model="m"),
    )


def _stats(records):
    return RunStats(
        total=len(records),
        ok=sum(r.status == "ok" for r in records),
        failed=sum(r.status != "ok" for r in records),
        llm_calls=len(records),
        prompt_tokens=1000,
        output_tokens=200,
        wall_time_s=1.5,
        rules_fired={"R2:out_of_scope_priority_capped": 1},
    )


def _report(records):
    return build_report(
        records, _stats(records), source="in.csv", provider="fake", model="fake-1"
    )


def test_aggregates_count_only_successful_records():
    records = [
        make("A", category="звіт/аналітика", department="маркетинг"),
        make("B", category="звіт/аналітика", department="маркетинг"),
        make("C", category="баг/підтримка", priority="high"),
        make("D", status="failed"),
    ]
    text = _report(records)
    assert "| звіт/аналітика | 2 | 67% |" in text  # 2 of 3 successful, not 4
    assert "| баг/підтримка | 1 | 33% |" in text
    assert "| маркетинг | 2 | 67% |" in text


def test_requests_without_a_department_are_bucketed_not_dropped():
    text = _report([make("A"), make("B", department="HR")])
    assert "| не визначено | 1 | 50% |" in text


def test_requester_and_domain_get_separate_tables():
    """An unknown sender must not hide a perfectly clear subject area."""
    text = _report([make("A", department=None, domain="маркетинг")])
    assert "За відділом-замовником (хто просить)" in text
    assert "За предметною областю (про що запит)" in text

    requester = text.split("За відділом-замовником")[1].split("###")[0]
    subject = text.split("За предметною областю")[1].split("##")[0]
    assert "| не визначено | 1 | 100% |" in requester
    assert "| маркетинг | 1 | 100% |" in subject


def test_clarification_section_lists_the_questions():
    text = _report([make("A", clarify=True, questions=["Які метрики?", "Хто читач?"])])
    assert "## Потребують уточнення (1)" in text
    assert "Які метрики?" in text and "Хто читач?" in text


def test_failed_requests_get_their_own_section():
    text = _report([make("A"), make("B", status="failed")])
    assert "## Не вдалося обробити (1)" in text
    assert "зламалось" in text


def test_high_priority_section():
    text = _report([make("A", priority="high"), make("B", priority="low")])
    assert "## Високий пріоритет (1)" in text


def test_pipe_characters_cannot_break_the_table():
    text = _report([make("A", summary="звіт | по | кампаніях", clarify=True)])
    assert "звіт \\| по \\| кампаніях" in text


def test_applied_rules_are_disclosed():
    assert "R2:out_of_scope_priority_capped" in _report([make("A")])


def test_empty_run_does_not_divide_by_zero():
    text = build_report([], RunStats(), source="in.csv", provider="fake", model="fake-1")
    assert "Немає" in text


def test_output_json_round_trips(tmp_path):
    records = [make("A", department="HR"), make("B", status="failed")]
    document = build_output_document(
        records, _stats(records), source="in.csv", provider="fake", model="fake-1"
    )
    path = tmp_path / "output.json"
    write_json(document, path)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert len(loaded["requests"]) == 2
    assert loaded["stats"]["failed"] == 1
    assert loaded["requests"][0]["triage"]["target_department"] == "HR"
    assert loaded["requests"][1]["triage"] is None
    assert loaded["requests"][1]["error"]["kind"] == "validation"
    assert loaded["provider"] == "fake" and "generated_at" in loaded


def test_output_csv_round_trips(tmp_path):
    from triage.report import write_csv
    records = [make("A", department="HR"), make("B", status="failed")]
    path = tmp_path / "report.csv"
    write_csv(records, path)

    content = path.read_text(encoding="utf-8-sig")
    lines = content.strip().split("\n")
    assert len(lines) == 3  # header + 2 records
    assert "HR" in lines[1]
    assert "A" in lines[1]
    assert "B" in lines[2]
