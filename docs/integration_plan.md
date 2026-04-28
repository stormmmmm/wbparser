# План связки `parser` ⇄ `maxapi`

> Этот документ описывает только **архитектуру связки**. Сам код связки
> (poster-worker, перевод схем, материализация медиа) пока не реализуется —
> это следующий шаг. Здесь зафиксировано, как два инструмента уже **готовы**
> к объединению и какие точки контакта между ними должны использоваться.

---

## 1. Роли двух подсистем

| Подсистема | Каталог | Назначение | Default port |
| --- | --- | --- | --- |
| `parser` (WB Parser) | [`parser/`](../parser/) | Сбор товаров с Wildberries, фильтрация, скоринг, сборка `ReadyPost` | `8000` |
| `maxapi` (MAX gateway) | [`maxapi/`](../maxapi/) | Публикация `ReadyPost` в каналы MAX через PyMax-userbot | `8080` |

Парсер **никогда не публикует сам**. Гейтвей **никогда не парсит сам**.
Контракт между ними — `ReadyPost`, и только он.

---

## 2. Где живёт контракт `ReadyPost`

`ReadyPost` — это согласованная JSON-схема, которую генерирует парсер и
понимает `maxapi`. Она существует в двух местах в одном и том же виде:

* **Парсер (источник):**
  * Pydantic-модель — [`parser/app/schemas/post.py`](../parser/app/schemas/post.py)
  * Пример полезной нагрузки — [`data/outbox/ready_posts_sample.json`](../data/outbox/ready_posts_sample.json)
  * Описана в `wb_parser_development_prompt.txt` § 13 и в README парсера.

* **MAX gateway (приёмник):**
  * Pydantic-модель — [`maxapi/api/models/jobs.py`](../maxapi/api/models/jobs.py) (`ReadyPost`, `ReadyPostMedia`, `ReadyPostItem`, `ReadyPostButton`)
  * OpenAPI-описание — [`maxapi/openapi.yml`](../maxapi/openapi.yml) (компоненты `ReadyPost*`, эндпоинт `POST /v1/jobs`)

> При расхождении полей **источником истины считается парсер**:
> любое расширение схемы делается сначала в `parser/app/schemas/post.py`
> и затем зеркально в `maxapi/api/models/jobs.py` + `openapi.yml`.

Поля `ReadyPost`, которые сейчас точно совпадают между сторонами:

`post_id`, `post_type` (`single | collection | visual_poll | discount | scenario`),
`title`, `text`, `parse_mode`, `media[]`, `items[]`, `buttons[]`,
`reactions_hint`, `planned_at`, `fresh_until`, `publication_status`,
`created_at`, `source` (`wb_parser`), `version`.

---

## 3. Точки соединения двух API

Связка не публикует ничего напрямую — она ходит между двумя HTTP-сервисами.

### 3.1. Парсер (publisher contract)

Эндпоинты, которые парсер **отдаёт** наружу для постинг-агента:

* `GET  /api/v1/posts/next?limit=…&post_type=…` — вернуть готовые посты.
* `POST /api/v1/posts/{post_id}/lock` — заблокировать пост на публикацию.
* `POST /api/v1/posts/{post_id}/published` — отметить пост опубликованным.
* `POST /api/v1/posts/{post_id}/failed` — сообщить об ошибке.
* `POST /api/v1/posts/{post_id}/metrics` — принять метрики канала.
* `POST /api/v1/clicks` — принять клики (если используется редирект).
* `GET  /api/v1/products/{article_id}` — карточка товара (debug / approve).

См. [`parser/app/api/`](../parser/app/api/) и [`parser/README.md`](../parser/README.md).

### 3.2. MAX gateway (consumer contract)

Эндпоинты, которые `maxapi` **отдаёт** наружу для постинг-агента:

* `POST /v1/jobs` (тег `jobs`) — создать публикационную задачу из `ReadyPost`.
* `GET  /v1/jobs?source=wb_parser&status=…` — список задач (фильтр по
  `ReadyPost.source` и статусу `accepted | scheduled | published | failed | cancelled | expired`).
* `GET  /v1/jobs/{job_id}` — статус конкретной задачи.
* `POST /v1/accounts/{accountId}/channels/{channelId}/posts` — низкоуровневая
  публикация (если когда-то понадобится отправлять не через `Job`).
* `POST /v1/accounts/login/start` / `POST /v1/accounts/login/verify` — phone+SMS логин.
* `POST /v1/media` — загрузка/импорт медиа.
* `POST /v1/webhooks` — подписка на `publication.*` / `metrics.collected`.

Полный контракт — [`maxapi/openapi.yml`](../maxapi/openapi.yml).

### 3.3. Файловый fallback

Если HTTP-связка временно недоступна, парсер уже умеет писать
`ReadyPost` в файл, а постер — отдавать результаты обратно файлом:

```
parser  ──ready_posts──▶ data/outbox/ready_posts.jsonl ──▶ poster
poster ──results──▶ data/inbox/publication_results.jsonl ──▶ parser
```

Эти пути уже захардкожены в [`parser/.env.example`](../parser/.env.example) и
выживают перезапуск.

---

## 4. Поток данных в связке (без кода)

```
┌─────────────────────────┐    1. собрать кандидатов с WB
│ parser                  │       (clients/wildberries.py)
│  collect → score        │    2. отфильтровать + посчитать score
│  build_posts → outbox   │    3. упаковать в ReadyPost
└────────────┬────────────┘
             │
       GET /api/v1/posts/next
             │
             ▼
┌─────────────────────────┐    4. POST /api/v1/posts/{id}/lock
│ poster (bridge worker)  │    5. при необходимости скачать медиа
│  TODO: следующий шаг,   │       и POST /v1/media в maxapi
│  здесь не реализуется   │    6. POST /v1/jobs со ссылкой на медиа
└────────────┬────────────┘    7. дождаться status=published
             │
       POST /v1/jobs (ReadyPost)
             │
             ▼
┌─────────────────────────┐    8. PyMaxBackend публикует в MAX
│ maxapi                  │    9. отсылает webhook publication.*
│  jobs router → backend  │
│  (PyMax / memory)       │
└─────────────────────────┘
             │
       POST /api/v1/posts/{id}/published    (от poster обратно)
       POST /api/v1/posts/{id}/metrics      (по webhook'у metrics.collected)
             │
             ▼
        ┌────────┐
        │ parser │ — обновляет статус, метрики, learning loop
        └────────┘
```

Шаги 4-7 — это и есть будущий «postinger / bridge worker». Сейчас
**ни одно из звеньев не реализовано в этом репозитории** — задача
данного шага только подготовить два инструмента, чтобы будущий мост
работал без переписывания контрактов.

---

## 5. Изоляция и отсутствие конфликтов

* **Код парсера** живёт только в [`parser/`](../parser/) (требование
  ТЗ — `wb_parser_development_prompt.txt` § 4) и не пересекается с
  кодом гейтвея.
* **Код гейтвея** живёт только в [`maxapi/`](../maxapi/) и не
  пересекается с парсером. Python-пакет — `api/`, не `app/`.
* **Порты не конфликтуют** (`parser` → `8000`, `maxapi` → `8080`).
* **Префиксы env vars не конфликтуют** (`WB_*`, `API_*`, `OUTBOX_PATH`,
  `MEDIA_CACHE_DIR`, … vs `MAXAPI_*`).
* **Виртуальные окружения раздельные**: `parser/.venv` и `maxapi/.venv`.
  Зависимости подсистем не смешиваются (у парсера — `httpx`, `sqlalchemy`,
  `typer` и т.д.; у гейтвея — `fastapi`, `pydantic`, опциональный
  `maxapi-python`/`PyMax`).
* **Общие, действительно разделяемые** ресурсы — только конфиги
  (`configs/` — правила парсинга), данные (`data/outbox`, `data/inbox`,
  `data/media_cache`, `data/raw_cache`) и документация (`docs/`).
  Эти каталоги допустимы в корне согласно ТЗ.

---

## 6. Что нужно сделать **в коде** при следующей итерации (не сейчас)

Перечислено для контекста, чтобы будущий агент (или этот же — на
следующем шаге) знал, чего касаться, а чего нет.

* В `parser/app/schemas/post.py` — добавить, при необходимости, поле
  `target_account_id` и `target_channel_id`, которые понадобятся
  гейтвею. Сейчас они в `ReadyPost` отсутствуют — пост абстрагирован
  от транспорта; адресат определяется конфигом постера.
* В `maxapi/api/routers/jobs.py` — проверить, что `POST /v1/jobs`
  действительно принимает `ReadyPost.source = "wb_parser"` и
  откатывает `ReadyPostMedia.url` (HTTP url WB-карточки) в
  материализованный `media_id` через `POST /v1/media`.
* Реализовать сам мост (новый каталог `bridge/` или `poster/`) —
  отдельный сервис, читающий `parser` API, загружающий медиа в
  `maxapi`, создающий `Job` и репортящий результаты обратно. Никакой
  бизнес-логики (фильтры, скоринг) в нём быть **не должно**.
* Webhook `publication.*` и `metrics.collected` от `maxapi` →
  принимающий эндпоинт у парсера (`POST /api/v1/posts/{id}/published`,
  `POST /api/v1/posts/{id}/metrics`). Можно протащить мостом или
  настроить прямой webhook из `maxapi` в парсер.
* Завести один `docker-compose.yml` или `Procfile` в корне, чтобы
  `parser`, `maxapi` и будущий `bridge` запускались одной командой
  для локальной разработки.

---

## 7. Известные расхождения схем (фиксируем для будущей итерации)

При сравнении `parser/app/schemas/post.py` и `maxapi/api/models/jobs.py`
найдены допустимые сейчас, но требующие выравнивания различия:

| Поле | parser | maxapi | Что делать |
| --- | --- | --- | --- |
| `post_id` | `str \| int` | `str` | парсер уже сериализует как строку — оставить, при необходимости в bridge привести `str(post_id)`. |
| `post_type` | свободная строка (`single \| collection \| visual_poll \| discount \| scenario`) | enum `single \| collection \| custom` | парсер генерирует `single` и `collection` — основные совпадают; `visual_poll/discount/scenario` сейчас попадут в `custom` через `extra="ignore"`. Документировать или расширить enum в `maxapi`. |
| `parse_mode` | свободная строка / `null` | enum `markdown \| html` / `null` | парсер сейчас всегда `null` — конфликта нет; при добавлении формата согласовать enum. |
| `ReadyPostItem.short_title` | обязательное | необязательное | совместимо в обе стороны. |
| `ReadyPostItem.canonical_url` | обязательное | необязательное | совместимо в обе стороны. |
| `ReadyPostMedia.type` | свободная строка `"photo"` | enum `photo \| image \| video \| document` | парсер всегда `"photo"` — совпадает, при добавлении видео согласовать enum. |
| `ReactionsHint.text` | строка по умолчанию `"Да - ❤️ Нет - 🔥"` | необязательная | совместимо. |
| `target_account_id`, `target_channel_id` | **отсутствуют** | передаются отдельно: `accountId` в URL, `channel_id` в теле `CreatePublicationJobRequest` | не пихать в `ReadyPost`; адресацию определяет конфиг будущего bridge-сервиса. |

Правило при расхождении: **источник истины — парсер**.
Расширения схемы — сначала в `parser/app/schemas/post.py`, затем в
`maxapi/api/models/jobs.py` + соответствующие компоненты в
`maxapi/openapi.yml`.

---

## 8. Что **не** нужно делать

* Не переносить парсинг WB внутрь `maxapi`.
* Не публиковать в MAX из самого `parser`.
* Не дублировать `ReadyPost` где-то ещё кроме двух точек выше.
* Не складывать общий код в корень репозитория — только в `parser/`,
  `maxapi/` или новый `bridge/`.
* Не превращать `parser` в Telegram/MAX-бота — это явный запрет в ТЗ.
