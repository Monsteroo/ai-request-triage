// Tests for the pure board logic: sorting and the category/Done split.
// buildCard/render/boot() touch the DOM and are verified by hand in the
// browser instead — see web/README.md.

import assert from "node:assert/strict";
import test from "node:test";

import { CATEGORIES, DONE_COLUMN, groupIntoColumns, sortColumn } from "./app.js";

function item(id, category, priority, needsClarification = false) {
  return { id, triage: { category, priority, needs_clarification: needsClarification } };
}

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
