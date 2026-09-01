# Звіт тріажу запитів

- **Джерело:** `data/input_requests.csv`
- **Згенеровано:** 2026-09-01T17:46:49+00:00
- **Провайдер / модель:** gemini / `gemini-3.5-flash`
- **Оброблено:** 18 запит(ів) — 6 успішно, 12 з помилкою
- **Викликів до LLM:** 45 · **токенів:** 11,780 (10,823 вхідних + 957 вихідних)
- **Час виконання:** 13.7 с

## Агрегати

### За категорією

| Значення | Кількість | Частка |
|---|---:|---:|
| автоматизація | 1 | 17% |
| інтеграція | 1 | 17% |
| звіт/аналітика | 3 | 50% |
| баг/підтримка | 0 | 0% |
| питання/консультація | 1 | 17% |
| поза скоупом | 0 | 0% |

### За пріоритетом

| Значення | Кількість | Частка |
|---|---:|---:|
| low | 2 | 33% |
| medium | 3 | 50% |
| high | 1 | 17% |

### За відділом

| Значення | Кількість | Частка |
|---|---:|---:|
| маркетинг | 0 | 0% |
| продажі | 1 | 17% |
| аналітика | 0 | 0% |
| PM | 0 | 0% |
| HR | 1 | 17% |
| фінанси/бухгалтерія | 1 | 17% |
| контент | 0 | 0% |
| SMM | 0 | 0% |
| підтримка | 0 | 0% |
| інше | 0 | 0% |
| не визначено | 3 | 50% |

## Потребують уточнення (4)

Ці запити не можна брати в роботу як є.

| ID | Канал | Суть | Що спитати | Впевненість |
|---|---|---|---|---:|
| REQ-003 | Email | Запит на автоматичне створення саммарі та списку домовленостей з транскриптів дзвінків та їх імпорт у картку угоди в PlanFix. | З якої системи телефонії або де саме зберігаються транскрипти дзвінків?<br>За якою логікою чи ідентифікатором шукати відповідну картку угоди в PlanFix?<br>Чи є вже готовий API або інтеграція між вашою телефонією та PlanFix? | 0.90 |
| REQ-005 | Telegram | Термінове вивантаження списку контрагентів із витратами понад 50к за травень для бухгалтерії. | З якої саме системи чи бази даних потрібно вивантажити витрати контрагентів?<br>У якому форматі (наприклад, Excel, Google Sheets) потрібно надати фінальний список? | 0.90 |
| REQ-009 | Email | Запит на автоматизацію первинного скринінгу PDF-резюме для технічних вакансій із виділенням структурованих полів та оцінки. | Куди саме замовник планує завантажувати PDF-резюме (наприклад, Google Drive, Telegram-бот…<br>Де саме мають зберігатися структуровані результати скринінгу (наприклад, у Google Sheets,…<br>Які саме технічні вимоги або критерії відповідності слід використовувати для оцінки? | 0.90 |
| REQ-011 | Telegram | Запит на створення таблиці без жодних деталей щодо її призначення та джерел даних. | Які саме дані мають бути в цій таблиці?<br>З яких джерел чи систем потрібно збирати інформацію?<br>Хто і для яких завдань буде використовувати цю таблицю? | 0.90 |

## Високий пріоритет (1)

| ID | Канал | Категорія | Суть |
|---|---|---|---|
| REQ-005 | Telegram | звіт/аналітика | Термінове вивантаження списку контрагентів із витратами понад 50к за травень для бухгалтерії. |

## Не вдалося обробити (12)

| ID | Тип помилки | Спроб | Деталі |
|---|---|---:|---|
| REQ-001 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-002 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-006 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-007 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-008 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-012 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-013 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-014 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-015 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-016 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-017 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
| REQ-018 | transport | 3 | Gemini rate limited or busy (429): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your curren… |
