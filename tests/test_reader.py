import pytest

from triage.reader import read_requests


def _write(tmp_path, text, name="in.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return path


def test_reads_rows_in_order(tmp_path):
    path = _write(
        tmp_path,
        "id,channel,timestamp,raw_text\n"
        "REQ-1,Slack,2026-06-08 09:14,перший\n"
        "REQ-2,Email,2026-06-08 10:00,другий\n",
    )
    rows = read_requests(path)
    assert [r.id for r in rows] == ["REQ-1", "REQ-2"]
    assert rows[0].timestamp is not None and rows[0].timestamp.hour == 9


def test_strips_bom_from_headers(tmp_path):
    path = _write(tmp_path, "﻿id,channel,timestamp,raw_text\nREQ-1,Slack,2026-06-08 09:14,текст\n")
    assert read_requests(path)[0].id == "REQ-1"


def test_missing_column_is_a_clear_error(tmp_path):
    path = _write(tmp_path, "id,channel,raw_text\nREQ-1,Slack,текст\n")
    with pytest.raises(ValueError, match="timestamp"):
        read_requests(path)


def test_blank_rows_are_skipped_and_bad_timestamps_survive(tmp_path):
    path = _write(
        tmp_path,
        "id,channel,timestamp,raw_text\n"
        ",,,\n"
        "REQ-1,Slack,колись у червні,текст\n",
    )
    rows = read_requests(path)
    assert len(rows) == 1
    assert rows[0].timestamp is None
    assert rows[0].timestamp_raw == "колись у червні"


def test_duplicate_ids_are_kept_not_merged(tmp_path):
    path = _write(
        tmp_path,
        "id,channel,timestamp,raw_text\n"
        "REQ-1,Slack,2026-06-08 09:14,перший\n"
        "REQ-1,Email,2026-06-08 09:20,другий\n",
    )
    assert len(read_requests(path)) == 2


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_requests(tmp_path / "nope.csv")
