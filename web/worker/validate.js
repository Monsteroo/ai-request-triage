// Validation of the model's reply, mirroring the pydantic gate in
// src/triage/models.py. Structured output makes most of this redundant most of
// the time — "most" is why it is here anyway.
//
// Kept in its own module with no network or Worker globals, so the failure
// paths can be tested directly.

const NULLISH = new Set([
  "",
  "null",
  "none",
  "n/a",
  "unknown",
  "невідомо",
  "не зрозуміло",
  "-",
]);

const LIST_FIELDS = {
  requested_actions: 10,
  clarifying_questions: 5,
  mentioned_systems: 10,
};

const ALLOWED_FIELDS = new Set([
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
]);

const FENCE = /^\s*```(?:json)?\s*|\s*```\s*$/gi;

/**
 * Pull a JSON object out of a model reply that may be wrapped or prefixed.
 * Cheaper than spending another API call on a response that is nearly right.
 */
export function extractJson(text) {
  const candidate = String(text ?? "").trim().replace(FENCE, "");
  if (candidate.startsWith("{") && candidate.endsWith("}")) return candidate;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start !== -1 && end > start) return candidate.slice(start, end + 1);
  return candidate;
}

function collapse(value) {
  return String(value).split(/\s+/).filter(Boolean).join(" ");
}

function cleanList(value, limit) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const out = [];
  for (const item of value) {
    const text = collapse(item);
    if (!text) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(text);
  }
  return out.slice(0, limit);
}

/**
 * Normalise the handful of ways a model spells "nothing here" before
 * validating. Strict about shape, forgiving about spelling — the same split
 * the Python model_validator makes.
 */
export function normalizeQuirks(data) {
  if (data === null || typeof data !== "object" || Array.isArray(data)) return data;
  const out = { ...data };

  for (const key of ["target_department", "domain"]) {
    const value = out[key];
    if (typeof value === "string" && NULLISH.has(value.trim().toLowerCase())) {
      out[key] = null;
    }
  }

  for (const key of Object.keys(LIST_FIELDS)) {
    const value = out[key];
    if (typeof value === "string") {
      out[key] = value.trim() ? [value] : [];
    } else if (value === null || value === undefined) {
      out[key] = [];
    }
  }

  return out;
}

/**
 * Validate one triage object against the controlled vocabularies.
 * Returns { ok: true, value } or { ok: false, errors: [...] } — never throws,
 * because a bad reply is an expected outcome, not an exception.
 */
export function validateTriage(payload, vocab) {
  const errors = [];
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return { ok: false, errors: ["відповідь не є JSON-об'єктом"] };
  }

  const data = normalizeQuirks(payload);

  // extra="forbid" — an invented field means the model went off-contract and
  // we want to know, not silently drop it.
  for (const key of Object.keys(data)) {
    if (!ALLOWED_FIELDS.has(key)) errors.push(`невідоме поле: ${key}`);
  }

  if (!vocab.category.includes(data.category)) {
    errors.push(`category: недозволене значення ${JSON.stringify(data.category)}`);
  }
  if (!vocab.priority.includes(data.priority)) {
    errors.push(`priority: недозволене значення ${JSON.stringify(data.priority)}`);
  }

  for (const key of ["target_department", "domain"]) {
    const value = data[key] ?? null;
    if (value !== null && !vocab.department.includes(value)) {
      errors.push(`${key}: недозволене значення ${JSON.stringify(value)}`);
    }
  }

  const summary = typeof data.short_summary === "string" ? collapse(data.short_summary) : "";
  if (summary.length < 3 || summary.length > 400) {
    errors.push("short_summary: має бути від 3 до 400 символів");
  }

  for (const key of ["needs_clarification", "is_actionable"]) {
    if (key in data && typeof data[key] !== "boolean") {
      errors.push(`${key}: має бути true або false`);
    }
  }
  if (typeof data.needs_clarification !== "boolean") {
    errors.push("needs_clarification: обов'язкове поле");
  }

  const confidence = typeof data.confidence === "number" ? data.confidence : NaN;
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    errors.push("confidence: має бути число від 0 до 1");
  }

  if (errors.length) return { ok: false, errors };

  return {
    ok: true,
    value: {
      category: data.category,
      target_department: data.target_department ?? null,
      domain: data.domain ?? null,
      priority: data.priority,
      short_summary: summary,
      requested_actions: cleanList(data.requested_actions, LIST_FIELDS.requested_actions),
      needs_clarification: data.needs_clarification,
      confidence,
      clarifying_questions: cleanList(
        data.clarifying_questions,
        LIST_FIELDS.clarifying_questions
      ),
      mentioned_systems: cleanList(data.mentioned_systems, LIST_FIELDS.mentioned_systems),
      is_actionable: typeof data.is_actionable === "boolean" ? data.is_actionable : true,
    },
  };
}
