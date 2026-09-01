"""Load and normalise the inbox CSV.

Real exports are messy, so this is defensive on purpose: BOM-prefixed headers,
stray whitespace, blank rows, unparseable timestamps and duplicate ids are all
things we survive rather than crash on.
"""

import csv
import logging
from datetime import datetime
from pathlib import Path

from .models import RawRequest

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"id", "channel", "timestamp", "raw_text"}

_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d.%m.%Y %H:%M",
)


def _parse_timestamp(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Unparseable timestamp %r — keeping the raw string only", value)
        return None


def read_requests(path: Path) -> list[RawRequest]:
    """Read the CSV into ``RawRequest`` objects, in file order."""
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    # utf-8-sig transparently strips the BOM Excel/Sheets like to add.
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = {(h or "").strip() for h in (reader.fieldnames or [])}
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise ValueError(
                f"{path} is missing required column(s): {', '.join(sorted(missing))}. "
                f"Found: {', '.join(sorted(headers))}"
            )

        requests: list[RawRequest] = []
        seen_ids: set[str] = set()
        for line_no, row in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "") for k, v in row.items()}
            request_id = row["id"].strip()
            raw_text = row["raw_text"].strip()

            if not request_id and not raw_text:
                logger.debug("Skipping blank row at line %d", line_no)
                continue
            if not request_id:
                request_id = f"ROW-{line_no}"
                logger.warning("Row at line %d has no id — using %s", line_no, request_id)
            if request_id in seen_ids:
                logger.warning("Duplicate id %s at line %d — keeping both rows", request_id, line_no)
            seen_ids.add(request_id)

            requests.append(
                RawRequest(
                    id=request_id,
                    channel=row["channel"].strip() or "unknown",
                    timestamp=_parse_timestamp(row["timestamp"]),
                    timestamp_raw=row["timestamp"].strip(),
                    raw_text=raw_text,
                )
            )

    logger.info("Loaded %d request(s) from %s", len(requests), path)
    return requests
