// Multi-provider fallback chain for classification.
//
// Gemini's free tier is primary: generous daily quota, native structured
// output. When it is rate-limited or unconfigured, the chain falls through to
// two providers that host OpenAI's open-weight gpt-oss-120b for free: Groq
// (fast, generous free tier) then Cerebras (a separate free tier for the same
// model). "OpenAI 120 OSS" is not something OpenAI itself hosts for free on
// api.openai.com — it is an open-weight model OpenAI published, and free
// access comes from third parties who run it. Groq and Cerebras both speak
// the OpenAI chat-completions dialect, so one adapter serves both.
//
// A provider only has to return raw text. Validation is uniform regardless of
// which one answered — see validate.js — so a provider's own structured-output
// support is a nice-to-have, not something this chain depends on. That is
// also why the request here uses the broadly-supported `json_object` mode
// rather than each provider's own JSON-schema dialect: fewer ways for two
// unfamiliar APIs to disagree, with the real gate applied uniformly after.

const GEMINI_MODEL = "gemini-3.1-flash-lite";
const GROQ_MODEL = "openai/gpt-oss-120b";
const CEREBRAS_MODEL = "gpt-oss-120b";
const MAX_OUTPUT_TOKENS = 1200;

export class ProviderError extends Error {
  constructor(message, { quotaExhausted = false } = {}) {
    super(message);
    this.quotaExhausted = quotaExhausted;
  }
}

async function callGemini(apiKey, systemPrompt, userPrompt, responseSchema) {
  const url =
    `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent` +
    `?key=${encodeURIComponent(apiKey)}`;

  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: systemPrompt }] },
      contents: [{ role: "user", parts: [{ text: userPrompt }] }],
      generationConfig: {
        responseMimeType: "application/json",
        responseSchema,
        temperature: 0,
        seed: 0,
        maxOutputTokens: MAX_OUTPUT_TOKENS,
        thinkingConfig: { thinkingBudget: 0 },
      },
    }),
  });

  if (!response.ok) {
    throw new ProviderError(`Gemini ${response.status}`, {
      quotaExhausted: response.status === 429,
    });
  }

  const data = await response.json();
  const candidate = data?.candidates?.[0];
  const finish = candidate?.finishReason;
  if (finish === "MAX_TOKENS") throw new ProviderError("відповідь обрізана лімітом токенів");
  if (finish && ["SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"].includes(finish)) {
    throw new ProviderError(`заблоковано (${finish})`);
  }
  const text = (candidate?.content?.parts || []).map((p) => p.text || "").join("");
  if (!text.trim()) throw new ProviderError("порожня відповідь");
  return text;
}

/** Groq and Cerebras both speak the OpenAI chat-completions dialect. */
function openAICompatible({ baseUrl, model }) {
  return async function call(apiKey, systemPrompt, userPrompt) {
    const response = await fetch(baseUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model,
        temperature: 0,
        max_tokens: MAX_OUTPUT_TOKENS,
        response_format: { type: "json_object" },
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ],
      }),
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new ProviderError(`${response.status}${detail ? `: ${detail.slice(0, 200)}` : ""}`, {
        quotaExhausted: response.status === 429,
      });
    }

    const data = await response.json();
    const text = data?.choices?.[0]?.message?.content;
    if (!text || !text.trim()) throw new ProviderError("порожня відповідь");
    return text;
  };
}

const callGroq = openAICompatible({
  baseUrl: "https://api.groq.com/openai/v1/chat/completions",
  model: GROQ_MODEL,
});
const callCerebras = openAICompatible({
  baseUrl: "https://api.cerebras.ai/v1/chat/completions",
  model: CEREBRAS_MODEL,
});

function formatProviderLabel(baseLabel, keyIndex) {
  if (keyIndex === 1) return baseLabel;
  if (baseLabel.includes("(")) {
    return baseLabel.replace(/\)$/, `, ключ ${keyIndex})`);
  }
  return `${baseLabel} (ключ ${keyIndex})`;
}

function collectKeys(env, baseKey, defaultLabel) {
  const keys = [];
  if (env[baseKey]) {
    keys.push({ value: env[baseKey], label: formatProviderLabel(defaultLabel, 1) });
  }
  for (let i = 2; i <= 9; i += 1) {
    const key = env[`${baseKey}_${i}`];
    if (key) {
      keys.push({ value: key, label: formatProviderLabel(defaultLabel, i) });
    }
  }
  return keys;
}

function rotateArray(arr, offset = 0) {
  if (arr.length <= 1) return arr;
  const n = ((offset % arr.length) + arr.length) % arr.length;
  return [...arr.slice(n), ...arr.slice(0, n)];
}

/**
 * Build the ordered chain from whichever secrets are actually configured. A
 * provider whose key is absent is skipped — the same graceful degradation the
 * rest of this Worker already uses for Gemini, now generalised to N providers.
 * Multiple keys per provider are rotated in round-robin fashion using rotateOffset
 * to distribute request load seamlessly without hitting rate limits prematurely.
 */
export function buildProviderChain(env, responseSchema, { rotateOffset = 0 } = {}) {
  const chain = [];

  const geminiKeys = collectKeys(env, "GEMINI_API_KEY", "Gemini");
  geminiKeys.forEach((key) => {
    chain.push({
      name: "gemini",
      label: key.label,
      call: (system, user) => callGemini(key.value, system, user, responseSchema),
    });
  });

  const groqKeys = collectKeys(env, "GROQ_API_KEY", "Groq (gpt-oss-120b)");
  groqKeys.forEach((key) => {
    chain.push({
      name: "groq",
      label: key.label,
      call: (system, user) => callGroq(key.value, system, user),
    });
  });

  const cerebrasKeys = collectKeys(env, "CEREBRAS_API_KEY", "Cerebras (gpt-oss-120b)");
  cerebrasKeys.forEach((key) => {
    chain.push({
      name: "cerebras",
      label: key.label,
      call: (system, user) => callCerebras(key.value, system, user),
    });
  });

  return rotateArray(chain, rotateOffset);
}

/**
 * Walk the chain in order. Within one provider, retry once on a validation
 * miss — often just a formatting slip the same model corrects on a second
 * try. A quota-exhausted provider is not retried: that attempt is not wasted
 * discovering a limit already known, it moves straight to the next provider,
 * which is the entire point of having a chain.
 *
 * `classify(rawText) -> {ok, value} | {ok:false, errors}` is the caller's
 * validation step, so this module has no opinion on response shape — only on
 * transport and on which provider gets to try next.
 */
export async function runProviderChain(chain, systemPrompt, userPrompt, classify) {
  if (!chain.length) {
    return { ok: false, error: "not_configured", attempts: [] };
  }

  const attempts = [];
  for (const provider of chain) {
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        const raw = await provider.call(systemPrompt, userPrompt);
        const result = classify(raw);
        if (result.ok) {
          return {
            ok: true,
            provider: provider.name,
            providerLabel: provider.label,
            value: result.value,
          };
        }
        attempts.push(`${provider.label}: ${result.errors.join("; ")}`);
      } catch (error) {
        attempts.push(`${provider.label}: ${error.message}`);
        if (error.quotaExhausted) break;
      }
    }
  }

  return { ok: false, error: "all_providers_failed", attempts };
}
