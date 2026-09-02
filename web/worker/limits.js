// Coarse rate limiting on top of Workers KV.
//
// This is a public page holding a live API key, so the job here is narrow and
// specific: make it impossible for one visitor — or one bad afternoon — to
// drain a free-tier quota that resets once a day. It is deliberately not an
// exact counter. KV is eventually consistent, so a determined attacker racing
// parallel requests can slip a few past the limit; that is an acceptable
// trade for not standing up a Durable Object to guard a demo.
//
// Takes any object with KV's get/put shape, so the logic is testable without
// a Worker runtime.

/** UTC day stamp — the window the provider's daily quota resets on. */
export function dayStamp(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

/**
 * Count one hit against `key` and say whether it is allowed.
 * Never throws: if KV itself is unavailable the request is allowed through,
 * because breaking the demo to protect a quota is the wrong failure mode.
 */
export async function hit(kv, key, limit, ttlSeconds) {
  if (!kv) return { allowed: true, used: 0, limit, degraded: true };

  let used = 0;
  try {
    const raw = await kv.get(key);
    used = raw ? parseInt(raw, 10) || 0 : 0;
  } catch {
    return { allowed: true, used: 0, limit, degraded: true };
  }

  if (used >= limit) {
    return { allowed: false, used, limit, degraded: false };
  }

  try {
    await kv.put(key, String(used + 1), { expirationTtl: ttlSeconds });
  } catch {
    // Counting failed but the read succeeded — let it through rather than
    // punish the visitor for our storage problem.
    return { allowed: true, used, limit, degraded: true };
  }

  return { allowed: true, used: used + 1, limit, degraded: false };
}

/**
 * The two limits every classify call must pass: this visitor's own budget,
 * and the whole demo's daily budget. The global one is what actually protects
 * the provider quota; the per-IP one stops a single visitor eating it.
 */
export async function checkTriageLimits(kv, ip, config, now = new Date()) {
  const day = dayStamp(now);

  const perIp = await hit(kv, `ip:${ip}:${day}`, config.perIpPerDay, 86400);
  if (!perIp.allowed) {
    return {
      allowed: false,
      reason: "per_ip",
      message: `Ліміт демо: ${config.perIpPerDay} класифікацій на добу з однієї адреси. Дошка нижче лишається повністю робочою.`,
    };
  }

  const global = await hit(kv, `global:${day}`, config.globalPerDay, 86400);
  if (!global.allowed) {
    return {
      allowed: false,
      reason: "global",
      message:
        "Денна квота демо вичерпана — безкоштовний тіер Gemini обмежений. Спробуйте завтра; дошка нижче лишається робочою.",
    };
  }

  return { allowed: true, reason: null, message: null };
}
