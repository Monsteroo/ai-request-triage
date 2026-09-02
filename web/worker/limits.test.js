// The limiter guards a free-tier quota that resets once a day, so the cases
// worth pinning are: the ceiling actually stops requests, the global ceiling
// stops them even when one visitor is well-behaved, and a broken KV fails open
// rather than taking the demo down with it.

import assert from "node:assert/strict";
import test from "node:test";

import { checkTriageLimits, dayStamp, hit } from "./limits.js";

/** Minimal stand-in for a KV namespace. */
function fakeKv(initial = {}) {
  const store = new Map(Object.entries(initial));
  return {
    store,
    async get(key) {
      return store.has(key) ? store.get(key) : null;
    },
    async put(key, value) {
      store.set(key, value);
    },
  };
}

const CONFIG = { perIpPerDay: 3, globalPerDay: 5 };

test("dayStamp is a UTC calendar day", () => {
  assert.equal(dayStamp(new Date("2026-09-02T23:59:00Z")), "2026-09-02");
  assert.equal(dayStamp(new Date("2026-09-03T00:01:00Z")), "2026-09-03");
});

test("hits below the ceiling are allowed and counted", async () => {
  const kv = fakeKv();
  const first = await hit(kv, "k", 2, 60);
  const second = await hit(kv, "k", 2, 60);
  assert.equal(first.allowed, true);
  assert.equal(second.allowed, true);
  assert.equal(kv.store.get("k"), "2");
});

test("the hit at the ceiling is refused", async () => {
  const kv = fakeKv({ k: "2" });
  const result = await hit(kv, "k", 2, 60);
  assert.equal(result.allowed, false);
});

test("a missing KV binding fails open rather than breaking the demo", async () => {
  const result = await hit(undefined, "k", 1, 60);
  assert.equal(result.allowed, true);
  assert.equal(result.degraded, true);
});

test("a KV that throws on read fails open", async () => {
  const broken = {
    async get() {
      throw new Error("KV down");
    },
    async put() {},
  };
  const result = await hit(broken, "k", 1, 60);
  assert.equal(result.allowed, true);
  assert.equal(result.degraded, true);
});

test("one visitor is cut off at their own ceiling", async () => {
  const kv = fakeKv();
  const now = new Date("2026-09-02T10:00:00Z");
  for (let i = 0; i < CONFIG.perIpPerDay; i += 1) {
    const ok = await checkTriageLimits(kv, "1.2.3.4", CONFIG, now);
    assert.equal(ok.allowed, true);
  }
  const blocked = await checkTriageLimits(kv, "1.2.3.4", CONFIG, now);
  assert.equal(blocked.allowed, false);
  assert.equal(blocked.reason, "per_ip");
});

test("the global ceiling stops a crowd of well-behaved visitors", async () => {
  const kv = fakeKv();
  const now = new Date("2026-09-02T10:00:00Z");
  // Each visitor stays under their own limit; together they exhaust the day.
  for (let i = 0; i < CONFIG.globalPerDay; i += 1) {
    const ok = await checkTriageLimits(kv, `visitor-${i}`, CONFIG, now);
    assert.equal(ok.allowed, true);
  }
  const blocked = await checkTriageLimits(kv, "visitor-new", CONFIG, now);
  assert.equal(blocked.allowed, false);
  assert.equal(blocked.reason, "global");
  assert.match(blocked.message, /квота/i);
});

test("counters roll over to a new day", async () => {
  const kv = fakeKv();
  const day1 = new Date("2026-09-02T10:00:00Z");
  const day2 = new Date("2026-09-03T10:00:00Z");
  for (let i = 0; i < CONFIG.perIpPerDay; i += 1) {
    await checkTriageLimits(kv, "1.2.3.4", CONFIG, day1);
  }
  assert.equal((await checkTriageLimits(kv, "1.2.3.4", CONFIG, day1)).allowed, false);
  assert.equal((await checkTriageLimits(kv, "1.2.3.4", CONFIG, day2)).allowed, true);
});
