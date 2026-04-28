# wb-bridge — мост `parser` ⇄ `maxapi`

Маленький воркер, который связывает [`parser/`](../parser/) (поставщик
`ReadyPost`) с [`maxapi/`](../maxapi/) (публикующий gateway). Никакой
бизнес-логики (фильтрация, скоринг, переписывание текстов, политика
расписания) тут нет — всё это остаётся в parser.

См. [`docs/integration_plan.md`](../docs/integration_plan.md).

## Что делает за один цикл

1. `GET  parser  /api/v1/posts/next?limit=N` — забрать готовые `ReadyPost`.
2. `POST parser  /api/v1/posts/{post_id}/lock` — забронировать пост на
   `WBBRIDGE_LOCK_TTL_SECONDS`.
3. `POST maxapi  /v1/accounts/{account_id}/publication-jobs` — отдать
   `ReadyPost` гейтвею. `Idempotency-Key = "wb-bridge:{post_id}"` —
   повторная попытка с тем же `post_id` не приведёт к дублю в MAX.
4. По ответу гейтвея:
   * `status=published` → `POST parser /api/v1/posts/{post_id}/published`.
   * `status=scheduled` → ничего не репортим парсеру; обратную связь
     отправит webhook от gateway (см. `parser/app/api/routes_webhooks_maxapi.py`).
   * любая ошибка / `failed` →
     `POST parser /api/v1/posts/{post_id}/failed` с
     `retryable=true` для 5xx/transport и `retryable=false` для 4xx.

Bridge **не** загружает медиа отдельным `POST /v1/media`: gateway сам
импортирует `ReadyPostMedia.url` через `materialize_media_from_refs`
(см. [`maxapi/api/storage.py`](../maxapi/api/storage.py)).

## Адресация

В `ReadyPost` нет полей `target_account_id / target_channel_id`,
сознательно — пост абстрагирован от транспорта. Адресат задаётся
конфигом bridge'а:

```
WBBRIDGE_MAXAPI_ACCOUNT_ID=acc_…
WBBRIDGE_MAXAPI_CHANNEL_ID=…
```

Один bridge → один канал. Если нужно вести несколько каналов из одного
парсера — поднимаются несколько процессов bridge с разными
`WBBRIDGE_*_ID`.

## Установка

```bash
cd bridge
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Запуск

```bash
# одна итерация (для cron / отладки):
.venv/bin/wb-bridge run-once

# постоянный воркер:
.venv/bin/wb-bridge run-loop

# проверить, что и parser, и maxapi доступны:
.venv/bin/wb-bridge ping
```

## Конфигурация (env vars)

Все настройки имеют префикс `WBBRIDGE_` и читаются из окружения или из
`.env` в текущей рабочей директории. Полный список — в
[`.env.example`](./.env.example) и
[`bridge/config.py`](./bridge/config.py).

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `WBBRIDGE_PARSER_BASE_URL` | `http://localhost:8000` | URL парсера. |
| `WBBRIDGE_MAXAPI_BASE_URL` | `http://localhost:8080` | URL MAX gateway. |
| `WBBRIDGE_MAXAPI_TOKEN` | `dev-token` | Bearer-токен для gateway. |
| `WBBRIDGE_MAXAPI_ACCOUNT_ID` | — *(обязателен)* | Аккаунт MAX, от чьего имени публикуем. |
| `WBBRIDGE_MAXAPI_CHANNEL_ID` | — *(обязателен)* | Канал MAX, в который публикуем. |
| `WBBRIDGE_WORKER_ID` | `wb-bridge-1` | Идентификатор воркера в логе парсера. |
| `WBBRIDGE_LOCK_TTL_SECONDS` | `600` | TTL блокировки поста в парсере. |
| `WBBRIDGE_BATCH_SIZE` | `1` | Сколько постов забирать за цикл. |
| `WBBRIDGE_POLL_INTERVAL_SECONDS` | `15` | Пауза между холостыми циклами. |
| `WBBRIDGE_REQUEST_TIMEOUT_SECONDS` | `30` | HTTP-таймаут для обоих апстримов. |
| `WBBRIDGE_LOG_LEVEL` | `INFO` | Уровень логирования. |

## Тесты и линт

```bash
.venv/bin/pytest -q
.venv/bin/ruff check bridge tests
```

В `tests/test_worker_e2e.py` поднимаются *оба* реальных FastAPI-приложения
(parser и maxapi) через `httpx.ASGITransport`, и bridge прогоняет полный
цикл публикации без сети.
