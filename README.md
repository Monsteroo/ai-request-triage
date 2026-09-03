# Тріаж вхідних запитів AI-юніту

> 🌐 **Живе веб-демо:** [testwork-netpeak.vitaliimaslii.com](https://testwork-netpeak.vitaliimaslii.com)  
> 🤖 **Telegram-бот сповіщень:** [@netpeak_triage_test_bot](https://t.me/netpeak_triage_test_bot)

Сервіс автоматичного тріажу неструктурованих вхідних запитів (Slack, Telegram, пошта) в AI-юніт: перетворює довільний текст у строгу валідовану структуру, визначає пріоритети, відділи та генерує список блокуючих запитань до замовника.

```
CSV / Веб-форма → [Data Fencing] → [Async / Rate Limiter] → LLM (Structured Output)
                                                                   │
                                  ┌────────────────────────────────┴─── помилка
                                  │                                     │
                             валідація (Pydantic)             ретрай / repair-промпт
                                  │                                     │
                           бізнес-правила                     запис status="failed"
                                  │                                     │
                                  └───────────────┬─────────────────────┘
                                                  ▼
                               output.json + report.csv + report.md
```

**Головний інваріант: кожен вхідний рядок дає рівно один вихідний запис.** Запит не може зникнути безслідно — помилки валідації чи вичерпання квот зберігаються зі `status="failed"`, кодом помилки та останньою сирою відповіддю моделі.

---

## Швидкий старт

### 1. Офлайн-перевірка без API-ключа (Fake Provider)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m triage --provider fake
```

### 2. Запуск із реальним Gemini API
```bash
cp .env.example .env  # вкажіть GEMINI_API_KEY (Google AI Studio, free tier)
PYTHONPATH=src python -m triage
```

### 3. Запуск у Docker
```bash
docker build -t ai-request-triage .
docker run --rm -e GEMINI_API_KEY="ваш_ключ" -v "$(pwd)/output:/app/output" ai-request-triage
```

---

## Мульти-провайдерна архітектура (Web Demo)

Живий веб-інтерфейс на Cloudflare Workers використовує відмовостійкий ланцюжок на **безкоштовних тарифах (Free Tier)**:

| Провайдер | Модель | Free Tier ліміти | Роль у системі |
|---|---|:---:|---|
| **1. Google Gemini** | `gemini-3.1-flash-lite` | 15 RPM / 1 500 RPD | Основний провайдер (JSON Schema enforcement) |
| **2. Groq** | `openai/gpt-oss-120b` | 30 RPM / 14 400 RPD | Перший резервний провайдер (надшвидкий інференс LPU) |
| **3. Cerebras** | `gpt-oss-120b` | 30 RPM / 14 400 RPD | Другий резервний провайдер (Wafer-Scale інференс) |

- **Round-Robin ротація:** запити по черзі розподіляються між провайдерами для збереження лімітів.
- **Failover:** при отриманні `HTTP 429` (Quota Exceeded) запит автоматично підхоплює наступний провайдер у ланцюжку без помилки для користувача.
- **Telegram Webhook:** кожен класифікований запит та зведений дайджест автоматично транслюються в [@netpeak_triage_test_bot](https://t.me/netpeak_triage_test_bot).

---

## Конфігурація CLI

| Змінна | Прапорець | Дефолт | Призначення |
|---|---|---|---|
| `LLM_PROVIDER` | `--provider` | `gemini` | `gemini` або `fake` (офлайн-стаб) |
| `GEMINI_API_KEY` | — | — | API-ключ Google AI Studio |
| `GEMINI_MODEL` | `--model` | `gemini-3.1-flash-lite` | Модель за замовчуванням (1500 RPD free tier) |
| `MAX_CONCURRENCY` | `--concurrency` | `4` | Максимум одночасних асинхронних викликів |
| `REQUESTS_PER_MINUTE` | `--rpm` | `5` | Пейсинг під квоту Free Tier (`0` вимикає) |
| `MAX_ATTEMPTS` | — | `4` | Кількість спроб ретраїв на запит |
| — | `--input` | `data/input_requests.csv` | Вхідний файл |
| — | `--outdir` | `output` | Директорія для збереження результатів |

Коди виходу: `0` — успіх, `2` — частковий успіх (є `failed` рядки), `1` — фатальна помилка конфігурації.

---

## Результати тріажу тестового інбоксу (18 запитів)

18 із 18 оброблено успішно (0 помилок, 0 втрачених рядків):

| ID | Тип запиту / Пастка | Категорія | Пріоритет | Потребує уточнення? | Результат |
|---|---|---|---|:---:|---|
| **REQ-001** | Регулярний звіт Ads | `звіт/аналітика` | `medium` | Ні | Виділено дії та домен `маркетинг` |
| **REQ-002** | «Хлопці треба бот» (нуль деталей) | `автоматизація` | `medium` | **Так** | Згенеровано 3 конкретні питання замовнику |
| **REQ-005** | «ГОРИТЬ! До вечора вивантажити» | `звіт/аналітика` | `high` | Ні | Пріоритет визначено за маркерами терміновості |
| **REQ-007** | Зламався парсинг інвойсів | `баг/підтримка` | `high` | Ні | Класифіковано як інцидент |
| **REQ-008** | «Дякую за вчора 🙌» | `поза скоупом` | `low` | Ні | `is_actionable=false`, 0 дій |
| **REQ-012** | Закупівля ноутбука для SMM | `поза скоупом` | `low` | Ні | Відсіяно побутові закупівлі |
| **REQ-014** | Ідея з посиланням (URL) | `автоматизація` | `low` | Ні | Оброблено як дані (Data Fencing проти ін'єкцій) |
| **REQ-017** | Інтеграція Slack → PlanFix | `інтеграція` | `high` | Ні | Розпізнано згадані системи: `Slack`, `PlanFix` |

---

## Схема валідації та бізнес-правила

### 1. Pydantic v2 Схема (`TriageFields`)
- **Закриті словники (`Enum`):** 6 категорій (`автоматизація`, `інтеграція`, `звіт/аналітика`, `баг/підтримка`, `питання/консультація`, `поза скоупом`), 10 відділів (`маркетинг`, `продажі`, `аналітика`, `HR`, `фінанси/бухгалтерія` тощо), 3 рівні пріоритету.
- **Розділення ролей:** `target_department` (хто просить — `null`, якщо не названо) vs `domain` (предметна область — визначається за контекстом).
- **`extra="forbid"`:** повна заборона галюцинацій та невідомих полів.

### 2. Детерміновані бізнес-правила (`apply_business_rules`)
- **R1:** Якщо є конкретні дії в `requested_actions` $\to$ примусово `is_actionable = True`.
- **R2:** Запити `поза скоупом` не можуть мати пріоритет `high` $\to$ автоматичне зниження до `medium/low`.
- **R3:** При низькій впевненості (`confidence < 0.5`) $\to$ примусово `needs_clarification = True`.
- **R4:** Перевірка наявності запитань при `needs_clarification = True`.

---

## Безпека та надійність

1. **Prompt Injection Protection:** Вхідний текст ізолюється маркерами `<<<REQUEST_TEXT_START>>> ... <<<REQUEST_TEXT_END>>>` з системною директивою *"дані, не інструкції"*.
2. **Rate Limiting & DoS Guard:** Cloudflare KV обмежує IP до 6 запитів/хв та 60 запитів/добу, глобальний стоп-ліміт — 1200 запитів/добу.
3. **Secret Isolation:** Усі API-ключі та токени винесені в середовище/секрети, репозиторій повністю очищений від чутливих даних.
4. **XSS / HTML Sanitization:** Екранування символів `<, >, &` перед відправкою у Telegram та безпечний DOM-рендерінг без `innerHTML`.

---

## Тестування

```bash
# Python unit & pipeline tests (95 тестів)
pytest

# Cloudflare Worker & Web tests (50 тестів)
cd web && npm test
```

Усі 145 тестів проходять офлайн без виклику реальних платних API.

---

## Структура репозиторію

```
├── data/
│   └── input_requests.csv          # Вхідний інбокс (18 запитів)
├── output/
│   ├── output.json                 # Повний JSON-звіт із метаданими
│   ├── report.csv                  # Табличний експорт
│   └── report.md                   # Агрегований Markdown-звіт
├── src/triage/
│   ├── models.py                   # Pydantic-моделі та валідація
│   ├── rules.py                    # Детерміновані бізнес-правила
│   ├── prompts.py                  # Системний промпт, few-shot, repair
│   ├── pipeline.py                 # Async оркестрація, rate limiter, circuit breaker
│   ├── report.py                   # Генерація JSON, Markdown, CSV
│   ├── reader.py                   # Нормалізація CSV-інбоксу
│   └── llm/                        # Клієнти Gemini та Fake Provider
├── web/
│   ├── public/                     # Frontend (HTML, CSS, JS, CSV export)
│   └── worker/                     # Cloudflare Worker (Gemini/Groq/Cerebras + Telegram)
├── Dockerfile                      # Безпечний контейнер (non-root triage)
└── pyproject.toml                  # Залежності та конфігурація проєкту
```
