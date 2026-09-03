// Client-side logic for the demo page. Pulled out of index.html into its own
// module for the same reason the Worker is split into files: the pure parts
// (sorting, grouping) can be unit tested directly, the same way triage_one's
// failure paths are tested in the Python pipeline.

export const CATEGORIES = [
  { key: "automation", label: "автоматизація" },
  { key: "integration", label: "інтеграція" },
  { key: "reporting", label: "звіт/аналітика" },
  { key: "bug", label: "баг/підтримка" },
  { key: "question", label: "питання/консультація" },
  { key: "out", label: "поза скоупом" },
];
export const DONE_COLUMN = { key: "done", label: "Виконано" };
export const ALL_COLUMNS = [...CATEGORIES, DONE_COLUMN];

export const MAX_CHARS = 1000;
export const STORE_KEY = "triage-demo-added-v2";

const PRIORITY_RANK = { high: 0, medium: 1, low: 2 };

/**
 * Higher priority first. Within a priority, a card still needing clarification
 * surfaces above one that is ready to act on — it is the one blocking someone
 * right now.
 */
export function sortColumn(items) {
  return [...items].sort((a, b) => {
    const pa = PRIORITY_RANK[a.triage.priority] ?? 3;
    const pb = PRIORITY_RANK[b.triage.priority] ?? 3;
    if (pa !== pb) return pa - pb;
    const ca = a.triage.needs_clarification ? 0 : 1;
    const cb = b.triage.needs_clarification ? 0 : 1;
    return ca - cb;
  });
}

/**
 * Split a flat list into per-column buckets, each already sorted.
 *
 * A card in `doneIds` always lands in the Done column regardless of its
 * category — that is the whole point of checking it off: it leaves the triage
 * queue. A category outside the closed vocabulary should not happen (the
 * schema forbids it) but is placed in Done rather than silently dropped, in
 * keeping with this project's "never lose a row" rule.
 */
export function groupIntoColumns(items, doneIds) {
  const byKey = new Map(ALL_COLUMNS.map((c) => [c.key, []]));
  for (const item of items) {
    if (doneIds.has(item.id)) {
      byKey.get(DONE_COLUMN.key).push(item);
      continue;
    }
    const col = CATEGORIES.find((c) => c.label === item.triage.category);
    byKey.get(col ? col.key : DONE_COLUMN.key).push(item);
  }
  for (const [key, list] of byKey) byKey.set(key, sortColumn(list));
  return byKey;
}

/** Everything from the model or the visitor renders through this. */
export function display(value, fallback = "—") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

// ── DOM wiring — not unit tested, exercised by hand in the browser ─────────

function node(tag, className, textContent) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (textContent !== undefined) n.textContent = textContent;
  return n;
}

export function boot() {
  const el = (id) => document.getElementById(id);
  const board = el("board");
  const input = el("input");
  const alertBox = el("alert");
  const staging = el("staging");

  function showAlert(message) {
    alertBox.textContent = message;
    alertBox.hidden = false;
  }
  function clearAlert() {
    alertBox.hidden = true;
  }

  let baseline = [];
  let added = loadAdded();

  function loadAdded() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  function saveAdded() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(added));
    } catch {
      /* private mode — the session still works, it just won't persist */
    }
  }

  function buildCard(item, isNew) {
    const t = item.triage;
    const card = node("div", `card p-${t.priority}`);
    if (isNew) card.classList.add("is-new");
    if (item.done) card.classList.add("is-done");

    const head = node("div", "card-head");
    const id = node("div", "card-id");
    id.append(
      node("span", null, item.id),
      node("span", null, "·"),
      node("span", null, item.channel || "демо")
    );
    head.append(id);

    // Only visitor-submitted cards can be marked done. The 18 baseline cards
    // are the fixed, real output of a pipeline run — a proof artifact, not a
    // to-do list — so they stay exactly as the run produced them.
    if (item.local) {
      const label = node("label", "done-toggle");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = !!item.done;
      checkbox.setAttribute("aria-label", "Позначити виконаним");
      checkbox.addEventListener("change", () => {
        item.done = checkbox.checked;
        saveAdded();
        render();
      });
      label.append(checkbox);
      head.append(label);
    }
    card.append(head);

    card.append(node("div", "card-summary", display(t.short_summary)));

    const badges = node("div", "badges");
    badges.append(node("span", `badge prio-${t.priority}`, t.priority));
    if (t.domain) badges.append(node("span", "badge", t.domain));
    if (t.target_department) badges.append(node("span", "badge", `від: ${t.target_department}`));
    (t.mentioned_systems || []).slice(0, 3).forEach((s) => badges.append(node("span", "badge", s)));
    if (t.needs_clarification) badges.append(node("span", "badge clarify", "потребує уточнення"));
    if (item.providerLabel && item.provider !== "gemini") {
      badges.append(node("span", "badge fallback", `через ${item.providerLabel}`));
    }
    card.append(badges);

    if (t.needs_clarification && (t.clarifying_questions || []).length) {
      const box = node("div", "questions");
      box.append(node("div", null, "Що спитати замовника:"));
      const ul = node("ul");
      t.clarifying_questions.forEach((q) => ul.append(node("li", null, q)));
      box.append(ul);
      card.append(box);
    }

    if (item.local && !item.done) {
      const actions = node("div", "card-actions");
      const btn = node("button", "ghost", "Надіслати в Telegram");
      btn.addEventListener("click", () => sendToTelegram(item, actions, btn));
      actions.append(btn);
      card.append(actions);
    }
    return card;
  }

  function render(newId = null) {
    const all = [...baseline, ...added];
    const doneIds = new Set(added.filter((i) => i.done).map((i) => i.id));
    const grouped = groupIntoColumns(all, doneIds);
    board.replaceChildren();

    for (const colDef of ALL_COLUMNS) {
      const items = grouped.get(colDef.key) || [];
      const col = node("div", colDef.key === "done" ? "column column-done" : "column");
      const title = node("div", "column-title");
      title.append(node("span", null, colDef.label), node("span", "count", String(items.length)));
      col.append(title);
      items.forEach((i) => col.append(buildCard(i, i.id === newId)));
      board.append(col);
    }

    const clarify = all.filter((i) => i.triage.needs_clarification && !doneIds.has(i.id)).length;
    el("board-meta").textContent = `${all.length} запитів · ${clarify} потребують уточнення`;
  }

  async function classify() {
    const value = input.value.trim();
    clearAlert();
    if (!value) {
      showAlert("Спершу впишіть текст запиту.");
      return;
    }
    if (value.length > MAX_CHARS) {
      showAlert(`Задовгий запит: ${value.length} із ${MAX_CHARS} символів.`);
      return;
    }

    el("submit").disabled = true;
    staging.hidden = false;

    try {
      const response = await fetch("/api/triage", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: value }),
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        showAlert(data.error || `Помилка ${response.status}.`);
        return;
      }

      const item = {
        id: `NEW-${String(added.length + 1).padStart(3, "0")}`,
        channel: "веб-демо",
        raw_text: value,
        triage: data.triage,
        provider: data.provider,
        providerLabel: data.providerLabel,
        local: true,
        done: false,
      };
      added.push(item);
      saveAdded();
      input.value = "";
      updateCounter();
      render(item.id);

      const index = CATEGORIES.findIndex((c) => c.label === data.triage.category);
      if (index >= 0) board.children[index]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch {
      showAlert("Не вдалося зв'язатися з сервісом. Дошка нижче лишається робочою.");
    } finally {
      staging.hidden = true;
      el("submit").disabled = false;
    }
  }

  async function sendToTelegram(item, container, btn) {
    btn.disabled = true;
    btn.textContent = "Надсилаю…";
    try {
      const response = await fetch("/api/telegram", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ raw_text: item.raw_text, triage: item.triage }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.sent) {
        container.replaceChildren(node("span", "sent", "надіслано в Telegram"));
      } else {
        btn.disabled = false;
        btn.textContent = "Надіслати в Telegram";
        showAlert(data.error || "Не вдалося надіслати.");
      }
    } catch {
      btn.disabled = false;
      btn.textContent = "Надіслати в Telegram";
      showAlert("Не вдалося надіслати в Telegram.");
    }
  }

  function updateCounter() {
    const n = input.value.trim().length;
    const c = el("counter");
    c.textContent = `${n} / ${MAX_CHARS}`;
    c.classList.toggle("over", n > MAX_CHARS);
  }

  input.addEventListener("input", updateCounter);
  el("submit").addEventListener("click", classify);
  input.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") classify();
  });
  el("sample").addEventListener("click", () => {
    input.value =
      "Терміново! Аналітика просить дашборд, який раз на день тягне дані з BigQuery і показує різкі стрибки витрат по підрядниках.";
    updateCounter();
    input.focus();
  });

  fetch("/data/baseline.json")
    .then((r) => r.json())
    .then((data) => {
      baseline = data.requests || [];
      const run = data.source_run || {};
      el("run-meta").textContent =
        `дошка — реальний прогін пайплайна: ${run.ok}/${run.total} успішно, ` +
        `${run.llm_calls} викликів, ${(run.prompt_tokens + run.output_tokens).toLocaleString("uk")} токенів, модель ${data.model}`;
      render();
    })
    .catch(() => {
      el("run-meta").textContent = "не вдалося завантажити базові дані";
      render();
    });

  updateCounter();
}
