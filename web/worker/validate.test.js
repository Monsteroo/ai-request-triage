// The interesting cases are the ones where the model misbehaves. Structured
// output prevents most of them most of the time; this is what catches the rest.

import assert from "node:assert/strict";
import test from "node:test";

import { extractJson, normalizeQuirks, validateTriage } from "./validate.js";

const VOCAB = {
  category: [
    "автоматизація",
    "інтеграція",
    "звіт/аналітика",
    "баг/підтримка",
    "питання/консультація",
    "поза скоупом",
  ],
  priority: ["low", "medium", "high"],
  department: ["маркетинг", "продажі", "аналітика", "HR", "контент", "інше"],
};

const VALID = {
  category: "звіт/аналітика",
  target_department: "маркетинг",
  domain: "маркетинг",
  priority: "medium",
  short_summary: "Щотижневий звіт по Google Ads.",
  requested_actions: ["Автоматизувати щотижневий звіт"],
  needs_clarification: false,
  confidence: 0.9,
  clarifying_questions: [],
  mentioned_systems: ["Google Ads"],
  is_actionable: true,
};

test("a well-formed reply passes", () => {
  const result = validateTriage(VALID, VOCAB);
  assert.equal(result.ok, true);
  assert.equal(result.value.category, "звіт/аналітика");
  assert.equal(result.value.confidence, 0.9);
});

test("an invented category is rejected", () => {
  const result = validateTriage({ ...VALID, category: "щось вигадане" }, VOCAB);
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /category/);
});

test("an invented priority is rejected", () => {
  const result = validateTriage({ ...VALID, priority: "urgent" }, VOCAB);
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /priority/);
});

test("confidence outside 0..1 is rejected", () => {
  for (const bad of [7, -0.1, "0.9", NaN]) {
    const result = validateTriage({ ...VALID, confidence: bad }, VOCAB);
    assert.equal(result.ok, false, `confidence=${bad} should fail`);
  }
});

test("a hallucinated field is rejected rather than silently dropped", () => {
  const result = validateTriage({ ...VALID, urgency_score: 9 }, VOCAB);
  assert.equal(result.ok, false);
  assert.match(result.errors.join(" "), /urgency_score/);
});

test("an empty or oversized summary is rejected", () => {
  assert.equal(validateTriage({ ...VALID, short_summary: "" }, VOCAB).ok, false);
  assert.equal(validateTriage({ ...VALID, short_summary: "x".repeat(401) }, VOCAB).ok, false);
});

test("a non-object reply is rejected without throwing", () => {
  for (const bad of [null, [1, 2], "текст", 42]) {
    const result = validateTriage(bad, VOCAB);
    assert.equal(result.ok, false);
  }
});

for (const value of ["", "null", "невідомо", "n/a", "  -  "]) {
  test(`nullish department ${JSON.stringify(value)} becomes null`, () => {
    const result = validateTriage({ ...VALID, target_department: value }, VOCAB);
    assert.equal(result.ok, true);
    assert.equal(result.value.target_department, null);
  });
}

test("a bare string is lifted into a list", () => {
  const result = validateTriage({ ...VALID, requested_actions: "зробити звіт" }, VOCAB);
  assert.equal(result.ok, true);
  assert.deepEqual(result.value.requested_actions, ["зробити звіт"]);
});

test("null lists become empty lists", () => {
  const result = validateTriage(
    { ...VALID, clarifying_questions: null, mentioned_systems: null },
    VOCAB
  );
  assert.equal(result.ok, true);
  assert.deepEqual(result.value.clarifying_questions, []);
  assert.deepEqual(result.value.mentioned_systems, []);
});

test("list items are de-duplicated and whitespace-collapsed", () => {
  const result = validateTriage(
    { ...VALID, requested_actions: ["Зробити звіт", "зробити  звіт", "   ", "Інше"] },
    VOCAB
  );
  assert.deepEqual(result.value.requested_actions, ["Зробити звіт", "Інше"]);
});

test("normalizeQuirks does not mutate its input", () => {
  const original = { ...VALID, target_department: "невідомо" };
  normalizeQuirks(original);
  assert.equal(original.target_department, "невідомо");
});

test("extractJson unwraps what models put around the object", () => {
  assert.equal(extractJson('{"a": 1}'), '{"a": 1}');
  assert.equal(extractJson('```json\n{"a": 1}\n```'), '{"a": 1}');
  assert.equal(extractJson('Ось результат: {"a": 1}. Готово.'), '{"a": 1}');
  assert.equal(extractJson("не json взагалі"), "не json взагалі");
});
