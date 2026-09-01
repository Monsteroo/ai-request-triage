"""Domain vocabulary and the strict schema every LLM answer must satisfy.

Two layers live here on purpose:

* the *controlled vocabularies* (``Category``, ``Priority``, ``Department``) —
  closed sets that both constrain the model and make aggregation in the report
  meaningful. An open string field would give us "маркетинг", "Маркетинг",
  "відділ маркетингу" and "marketing" as four different buckets.
* ``TriageFields`` — the contract. Anything the model returns is parsed through
  it, and anything that does not fit is a failure we handle explicitly rather
  than a surprise ``KeyError`` three layers up.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Category(str, Enum):
    """Fixed by the assignment brief."""

    AUTOMATION = "автоматизація"
    INTEGRATION = "інтеграція"
    REPORTING = "звіт/аналітика"
    BUG = "баг/підтримка"
    QUESTION = "питання/консультація"
    OUT_OF_SCOPE = "поза скоупом"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Department(str, Enum):
    """Requesting department.

    Deliberately a closed list: the brief names marketing / sales / analytics /
    PM / HR, and the inbox adds finance, content, SMM and support. Extending it
    is a one-line change here plus a re-run — the value is injected into the
    prompt from this enum, so code and prompt cannot drift apart.
    """

    MARKETING = "маркетинг"
    SALES = "продажі"
    ANALYTICS = "аналітика"
    PM = "PM"
    HR = "HR"
    FINANCE = "фінанси/бухгалтерія"
    CONTENT = "контент"
    SMM = "SMM"
    SUPPORT = "підтримка"
    OTHER = "інше"


# The handful of ways a model spells "nothing here" instead of returning null.
NULLISH_STRINGS = {"", "null", "none", "n/a", "unknown", "невідомо", "не зрозуміло", "-"}


def _clean_list(values: list[str], *, limit: int) -> list[str]:
    """Strip, drop blanks, de-duplicate case-insensitively, cap the length."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = " ".join(str(value).split())
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:limit]


class TriageFields(BaseModel):
    """The structured view of one request.

    ``extra="forbid"`` is intentional. If the model invents a field we want to
    know about it and run the repair pass, not silently drop data.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    # --- required by the brief -------------------------------------------
    category: Category
    target_department: Department | None = Field(
        default=None, description="null when the request does not reveal a requester"
    )
    priority: Priority
    short_summary: str = Field(min_length=3, max_length=400)
    requested_actions: list[str] = Field(default_factory=list)
    needs_clarification: bool

    # --- extensions; each one earns its place, see README -----------------
    confidence: float = Field(ge=0.0, le=1.0)
    clarifying_questions: list[str] = Field(default_factory=list)
    mentioned_systems: list[str] = Field(default_factory=list)
    is_actionable: bool = True

    # Known encoding quirks are normalised *before* validation; genuine schema
    # breaches still fail loudly afterwards. Strict about shape, forgiving about
    # the handful of ways an LLM likes to spell "nothing here".
    @model_validator(mode="before")
    @classmethod
    def _normalise_quirks(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)

        dept = data.get("target_department")
        if isinstance(dept, str) and dept.strip().casefold() in NULLISH_STRINGS:
            data["target_department"] = None

        # A single action returned as a bare string instead of a list.
        for key in ("requested_actions", "clarifying_questions", "mentioned_systems"):
            value = data.get(key)
            if isinstance(value, str):
                data[key] = [value] if value.strip() else []
            elif value is None:
                data[key] = []

        return data

    @field_validator("short_summary")
    @classmethod
    def _collapse_whitespace(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("requested_actions")
    @classmethod
    def _clean_actions(cls, v: list[str]) -> list[str]:
        return _clean_list(v, limit=10)

    @field_validator("clarifying_questions")
    @classmethod
    def _clean_questions(cls, v: list[str]) -> list[str]:
        return _clean_list(v, limit=5)

    @field_validator("mentioned_systems")
    @classmethod
    def _clean_systems(cls, v: list[str]) -> list[str]:
        return _clean_list(v, limit=10)


class RawRequest(BaseModel):
    """One row of the input CSV, after normalisation."""

    model_config = ConfigDict(extra="allow")

    id: str
    channel: str
    timestamp: datetime | None = None
    timestamp_raw: str = ""
    raw_text: str


class ErrorInfo(BaseModel):
    """Why a request could not be triaged, kept alongside the request itself."""

    kind: Literal["validation", "transport", "empty_input", "unknown"]
    message: str
    last_raw_response: str | None = None


class ProcessingMeta(BaseModel):
    attempts: int = 0
    model: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    applied_rules: list[str] = Field(default_factory=list)


class ProcessedRequest(BaseModel):
    """Input row + triage outcome. Produced for *every* row, always.

    A row is never dropped: if the model or the network let us down the record
    still shows up with ``status="failed"`` and an ``error`` explaining why.
    """

    id: str
    channel: str
    timestamp: datetime | None = None
    raw_text: str
    status: Literal["ok", "failed"]
    triage: TriageFields | None = None
    error: ErrorInfo | None = None
    meta: ProcessingMeta = Field(default_factory=ProcessingMeta)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=False)
