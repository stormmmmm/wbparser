# WB Parser

Парсер Wildberries и сборщик payload-ов постов для Telegram-канала находок.  
Публикацию в Telegram не выполняет: отдает готовые `ReadyPost` через API и/или outbox-файл.

## Что реализовано

- сбор кандидатов из WB (`search`, `category`, `manual`, `refresh`);
- нормализация карточек в `ParsedProduct`;
- фильтрация по hard-правилам и risk-флагам;
- скоринг `selection_score` (0-100) по формуле из ТЗ;
- сборка `single` и `collection` постов;
- экспорт `ReadyPost` в `data/outbox/ready_posts.jsonl`;
- импорт статусов публикации из `data/inbox/publication_results.jsonl`;
- REST API для постинг-скрипта;
- SQLite/PostgreSQL через `DATABASE_URL`;
- тесты на нормализацию, фильтры, скоринг, построение поста и API-контракт.

## Быстрый старт

```bash
cd parser
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
copy .env.example .env
```

Инициализация БД:

```bash
wb-parser init-db
```

Сбор и pipeline:

```bash
wb-parser collect --source search --query "сумка багет" --limit 100
wb-parser score --limit 500
wb-parser build-posts --type collection --limit 10
wb-parser export-ready --format jsonl --output ../data/outbox/ready_posts.jsonl
```

Запуск API:

```bash
wb-parser serve --host 0.0.0.0 --port 8000
```

## CLI

- `wb-parser init-db`
- `wb-parser collect --source search --query "..."`
- `wb-parser collect --source category --category-id ...`
- `wb-parser collect --source trend --limit 100`
- `wb-parser refresh --article-id <id>`
- `wb-parser score --limit 500`
- `wb-parser build-posts --type single|collection --limit 10`
- `wb-parser export-ready --format jsonl --output ../data/outbox/ready_posts.jsonl`
- `wb-parser import-publication-results --input ../data/inbox/publication_results.jsonl`
- `wb-parser serve --host 0.0.0.0 --port 8000`
- `wb-parser worker --loop --interval 300`

## API контракт

- `GET /health`
- `GET /api/v1/posts/next?limit=1&post_type=collection`
- `POST /api/v1/posts/{post_id}/lock`
- `POST /api/v1/posts/{post_id}/published`
- `POST /api/v1/posts/{post_id}/failed`
- `POST /api/v1/posts/{post_id}/metrics`
- `POST /api/v1/clicks`
- `GET /api/v1/products/{article_id}`

## Структура

Весь исполняемый код и тесты находятся в `parser/`:

- `app/` — API, CLI, клиенты, сервисы, БД, схемы;
- `tests/` — unit/integration тесты с фикстурами.

Внешняя интеграция:

- outbox: `../data/outbox/ready_posts.jsonl`
- inbox: `../data/inbox/publication_results.jsonl`

