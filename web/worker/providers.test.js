// Routing logic only — no real network calls. Each fake provider is a plain
// object with a scripted `.call`, the same dependency-injection pattern the
// Python pipeline's ScriptedClient uses for triage_one.

import assert from "node:assert/strict";
import test from "node:test";

import { buildProviderChain, ProviderError, runProviderChain } from "./providers.js";

function scriptedProvider(name, script) {
  let i = 0;
  return {
    name,
    label: name,
    call: async () => {
      const step = script[Math.min(i, script.length - 1)];
      i += 1;
      if (step instanceof Error) throw step;
      return step;
    },
  };
}

const okClassify = (raw) => ({ ok: true, value: { raw } });
const alwaysInvalid = () => ({ ok: false, errors: ["не пройшла валідацію"] });

test("no configured providers is reported plainly, not as a crash", async () => {
  const result = await runProviderChain([], "sys", "usr", okClassify);
  assert.equal(result.ok, false);
  assert.equal(result.error, "not_configured");
});

test("the first provider to answer successfully wins, and later providers are never called", async () => {
  let secondCalled = false;
  const first = scriptedProvider("a", ["ok"]);
  const second = { name: "b", label: "b", call: async () => { secondCalled = true; return "ok"; } };

  const result = await runProviderChain([first, second], "sys", "usr", okClassify);

  assert.equal(result.ok, true);
  assert.equal(result.provider, "a");
  assert.equal(secondCalled, false);
});

test("a quota-exhausted provider is not retried — the chain moves on immediately", async () => {
  let callsToFirst = 0;
  const first = {
    name: "a",
    label: "a",
    call: async () => {
      callsToFirst += 1;
      throw new ProviderError("429", { quotaExhausted: true });
    },
  };
  const second = scriptedProvider("b", ["ok"]);

  const result = await runProviderChain([first, second], "sys", "usr", okClassify);

  assert.equal(result.ok, true);
  assert.equal(result.provider, "b");
  assert.equal(callsToFirst, 1); // no second attempt burned on a dead quota
});

test("a provider that fails validation once then succeeds recovers on its own retry", async () => {
  const first = scriptedProvider("a", ["bad", "good"]);
  let classifyCalls = 0;
  const classify = (raw) => {
    classifyCalls += 1;
    return raw === "good" ? { ok: true, value: { raw } } : alwaysInvalid();
  };

  const result = await runProviderChain([first], "sys", "usr", classify);

  assert.equal(result.ok, true);
  assert.equal(classifyCalls, 2);
});

test("a transient (non-quota) error is retried within the same provider", async () => {
  const first = scriptedProvider("a", [new ProviderError("502"), "ok"]);
  const result = await runProviderChain([first], "sys", "usr", okClassify);
  assert.equal(result.ok, true);
  assert.equal(result.provider, "a");
});

test("every provider failing is reported with every attempt's reason, nothing silently swallowed", async () => {
  const first = { name: "a", label: "A", call: async () => { throw new ProviderError("boom"); } };
  const second = { name: "b", label: "B", call: async () => { throw new ProviderError("bang"); } };

  const result = await runProviderChain([first, second], "sys", "usr", okClassify);

  assert.equal(result.ok, false);
  assert.equal(result.error, "all_providers_failed");
  assert.equal(result.attempts.length, 4); // 2 providers x 2 attempts each
  assert.ok(result.attempts.some((a) => a.includes("boom")));
  assert.ok(result.attempts.some((a) => a.includes("bang")));
});

test("buildProviderChain includes only providers whose secret is configured", () => {
  const chain = buildProviderChain({ GEMINI_API_KEY: "x" }, {});
  assert.deepEqual(chain.map((p) => p.name), ["gemini"]);
});

test("buildProviderChain orders providers Gemini, then Groq, then Cerebras", () => {
  const chain = buildProviderChain(
    { GEMINI_API_KEY: "g", GROQ_API_KEY: "q", CEREBRAS_API_KEY: "c" },
    {}
  );
  assert.deepEqual(chain.map((p) => p.name), ["gemini", "groq", "cerebras"]);
});

test("buildProviderChain works with only a non-Gemini key set", () => {
  // The chain should not assume Gemini is always present.
  const chain = buildProviderChain({ GROQ_API_KEY: "q" }, {});
  assert.deepEqual(chain.map((p) => p.name), ["groq"]);
});

test("an empty env produces an empty chain, not an error", () => {
  assert.deepEqual(buildProviderChain({}, {}), []);
});
