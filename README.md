# wbparser — экосистема WB-канала

Этот репозиторий объединяет два независимых сервиса в одну экосистему
для автоматического ведения канала «находки на Wildberries» в
мессенджере [MAX](https://max.ru):

| Подсистема | Каталог | Что делает | Default port |
| --- | --- | --- | --- |
| **WB Parser** | [`parser/`](./parser/) | Собирает товары с Wildberries, фильтрует, считает `selection_score`, упаковывает их в `ReadyPost` payload. **Сам в MAX не публикует.** | `8000` |
| **MAX Gateway** | [`maxapi/`](./maxapi/) | REST-обвязка вокруг авторизованного userbot-аккаунта MAX (через [`maxapi-python`](https://github.com/MaxApiTeam/PyMax)). Принимает `ReadyPost` и публикует пост в канал. **Сам Wildberries не парсит.** | `8080` |

Контракт между ними — общий объект `ReadyPost`. План связки и точки
соединения подробно описаны в
[`docs/integration_plan.md`](./docs/integration_plan.md).

> На этом этапе репозиторий содержит только **подготовку** двух
> инструментов к объединению. Сам код связки (poster / bridge worker,
> материализация медиа, перевод схем) — следующий шаг и здесь не
> реализуется.

---

## Структура репозитория

```
.
├── README.md                       <- этот файл
├── docs/                           <- ТЗ и аналитические отчёты
│   ├── wb_parser_development_prompt.txt
│   ├── wb_parser_implementation_report.txt
│   ├── marketplace_parsing_logic_report.txt
│   ├── channel_analysis_report.txt
│   └── integration_plan.md         <- план связки parser ⇄ maxapi
├── configs/                        <- YAML-правила парсера
│   ├── categories.yml
│   ├── search_queries.yml
│   ├── trend_keywords.yml
│   ├── scoring.yml
│   ├── stop_words.yml
│   └── excluded_articles.yml
├── data/                           <- runtime-обмен между parser и постером
│   ├── outbox/                     <- ReadyPost, выходящий из parser
│   │   └── ready_posts_sample.json
│   ├── inbox/                      <- результаты публикации обратно в parser
│   ├── media_cache/                <- скачанные медиа
│   └── raw_cache/                  <- сырые ответы WB (для отладки)
├── parser/                         <- WB Parser (изолированный пакет)
│   ├── app/                        <- API, CLI, services, db, schemas
│   ├── tests/
│   ├── pyproject.toml
│   ├── README.md
│   └── .env.example
└── maxapi/                         <- MAX userbot SDK + REST gateway
    ├── api/                        <- backends, routers, models, storage
    ├── tests/
    ├── openapi.yml                 <- авторитетный OpenAPI gateway'я
    ├── pyproject.toml
    ├── README.md
    └── images.jpg                  <- маленькая фикстура для SDK quickstart
```

Корневые `configs/`, `data/`, `docs/` — это **общие** ресурсы экосистемы.
Любой другой код парсера должен жить только внутри `parser/`, а код
гейтвея — только внутри `maxapi/` (см.
[`docs/integration_plan.md`](./docs/integration_plan.md) § 5).

---

## Быстрый старт

Каждый сервис ставится и запускается **в своём виртуальном окружении** —
смешивать зависимости не нужно.

### WB Parser

```bash
cd parser
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
wb-parser init-db
wb-parser serve --host 0.0.0.0 --port 8000
```

Полный набор CLI-команд, эндпоинтов и переменных окружения —
[`parser/README.md`](./parser/README.md).

### MAX Gateway

```bash
cd maxapi
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,pymax]"
export MAXAPI_BACKEND=memory       # для локальной разработки без MAX-сети
export MAXAPI_TOKEN=dev-token
maxapi                             # uvicorn api.main:app on 0.0.0.0:8080
```

Phone+SMS логин, идемпотентность, медиа, webhooks, `Job`'ы под
`ReadyPost` — [`maxapi/README.md`](./maxapi/README.md) и
[`maxapi/openapi.yml`](./maxapi/openapi.yml).

---

## Тесты

```bash
# WB Parser
cd parser && . .venv/bin/activate && pytest -q

# MAX Gateway
cd maxapi && . .venv/bin/activate && pytest -q
```

---

## Документы

* [`docs/wb_parser_development_prompt.txt`](./docs/wb_parser_development_prompt.txt) — исходное ТЗ парсера.
* [`docs/wb_parser_implementation_report.txt`](./docs/wb_parser_implementation_report.txt) — отчёт об уже сделанной работе по парсеру.
* [`docs/marketplace_parsing_logic_report.txt`](./docs/marketplace_parsing_logic_report.txt) — логика отбора товаров.
* [`docs/channel_analysis_report.txt`](./docs/channel_analysis_report.txt) — продуктовая логика будущего MAX-канала.
* [`docs/integration_plan.md`](./docs/integration_plan.md) — план связки двух сервисов в одну экосистему.

---

## Разработка дальше

Следующий шаг — реализовать **bridge / poster worker**, который читает
`ReadyPost` из парсера, материализует медиа в `maxapi`, создаёт
`Job`'ы и репортит результаты обратно. Подробности и список
конкретных задач — в
[`docs/integration_plan.md`](./docs/integration_plan.md) §§ 4 и 6.
