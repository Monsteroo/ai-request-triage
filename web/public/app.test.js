// Tests for the pure board logic: sorting and the category/Done split.
// buildCard/render/boot() touch the DOM and are verified by hand in the
// browser instead — see web/README.md.

import assert from "node:assert/strict";
import test from "node:test";

import { buildCsv, CATEGORIES, DONE_COLUMN, groupIntoColumns, SAMPLES, sortColumn } from "./app.js";

function item(id, category, priority, needsClarification = false) {
  return { id, triage: { category, priority, needs_clarification: needsClarification } };
}

test("buildCsv produces valid CSV with BOM and correct columns", () => {
  const items = [
    {
      id: "REQ-001",
      channel: "Slack",
      raw_text: "текст з комою, та \"лапками\"",
      triage: {
        category: "автоматизація",
        priority: "high",
        target_department: "Маркетинг",
        domain: "Маркетинг",
        short_summary: "Створити бота",
        requested_actions: ["налаштувати", "запустити"],
        needs_clarification: true,
        clarifying_questions: ["Хто замовник?"],
        confidence: 0.95,
      },
      done: false,
    },
  ];

  const csv = buildCsv(items);
  assert.ok(csv.startsWith("\uFEFF")); // UTF-8 BOM
  assert.ok(csv.includes("REQ-001"));
  assert.ok(csv.includes("автоматизація"));
  assert.ok(csv.includes("Маркетинг"));
  assert.ok(csv.includes("В черзі"));
  assert.ok(csv.includes('"текст з комою, та ""лапками"""'));
});

test("SAMPLES contains 10 distinct non-empty examples under 1000 characters", () => {
  assert.equal(SAMPLES.length, 10);
  const set = new Set(SAMPLES);
  assert.equal(set.size, 10);
  for (const s of SAMPLES) {
    assert.ok(typeof s === "string");
    assert.ok(s.trim().length > 10);
    assert.ok(s.trim().length <= 1000);
  }
});

test("sortColumn orders high before medium before low", () => {
  const items = [item("a", "x", "low"), item("b", "x", "high"), item("c", "x", "medium")];
  assert.deepEqual(sortColumn(items).map((i) => i.id), ["b", "c", "a"]);
});

test("within the same priority, a card needing clarification surfaces first", () => {
  const items = [item("a", "x", "high", false), item("b", "x", "high", true)];
  assert.deepEqual(sortColumn(items).map((i) => i.id), ["b", "a"]);
});

test("sortColumn does not mutate its input", () => {
  const items = [item("a", "x", "low"), item("b", "x", "high")];
  const before = items.map((i) => i.id);
  sortColumn(items);
  assert.deepEqual(items.map((i) => i.id), before);
});

test("an unknown priority sorts after the three known ones, not first", () => {
  const items = [item("a", "x", "low"), item("b", "x", "weird")];
  assert.deepEqual(sortColumn(items).map((i) => i.id), ["a", "b"]);
});

test("groupIntoColumns places each item under its category", () => {
  const items = [item("a", "автоматизація", "high"), item("b", "баг/підтримка", "low")];
  const grouped = groupIntoColumns(items, new Set());
  assert.deepEqual(grouped.get("automation").map((i) => i.id), ["a"]);
  assert.deepEqual(grouped.get("bug").map((i) => i.id), ["b"]);
  assert.deepEqual(grouped.get("done"), []);
});

test("every configured column exists in the output even when empty", () => {
  const grouped = groupIntoColumns([], new Set());
  for (const col of [...CATEGORIES, DONE_COLUMN]) assert.ok(grouped.has(col.key));
});

test("a done item leaves its category column for Done, regardless of category", () => {
  const items = [item("a", "автоматизація", "high")];
  const grouped = groupIntoColumns(items, new Set(["a"]));
  assert.deepEqual(grouped.get("automation"), []);
  assert.deepEqual(grouped.get("done").map((i) => i.id), ["a"]);
});

test("only the checked ids move — the rest of the column is untouched", () => {
  const items = [item("a", "автоматизація", "high"), item("b", "автоматизація", "low")];
  const grouped = groupIntoColumns(items, new Set(["a"]));
  assert.deepEqual(grouped.get("automation").map((i) => i.id), ["b"]);
  assert.deepEqual(grouped.get("done").map((i) => i.id), ["a"]);
});

test("the Done column is itself sorted by priority", () => {
  const items = [item("a", "x", "low"), item("b", "x", "high")];
  const grouped = groupIntoColumns(items, new Set(["a", "b"]));
  assert.deepEqual(grouped.get("done").map((i) => i.id), ["b", "a"]);
});

test("a category outside the closed vocabulary lands in Done rather than vanishing", () => {
  // Should not happen given the schema, but the project's rule is that a row
  // is never silently dropped — see triage_one's last-resort catch in Python.
  const items = [item("a", "щось невідоме", "high")];
  const grouped = groupIntoColumns(items, new Set());
  assert.deepEqual(grouped.get("done").map((i) => i.id), ["a"]);
});
