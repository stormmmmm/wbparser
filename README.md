# wbparser — экосистема WB-канала

Этот репозиторий объединяет три сервиса в одну экосистему для
автоматического ведения канала «находки на Wildberries» в мессенджере
[MAX](https://max.ru):

| Подсистема | Каталог | Что делает | Default port |
| --- | --- | --- | --- |
| **WB Parser** | [`parser/`](./parser/) | Собирает товары с Wildberries, фильтрует, считает `selection_score`, упаковывает их в `ReadyPost` payload. **Сам в MAX не публикует.** | `8000` |
| **MAX Gateway** | [`maxapi/`](./maxapi/) | REST-обвязка вокруг авторизованного userbot-аккаунта MAX (через [`maxapi-python`](https://github.com/MaxApiTeam/PyMax)). Принимает `ReadyPost` и публикует пост в канал. **Сам Wildberries не парсит.** | `8080` |
| **Bridge worker** | [`bridge/`](./bridge/) | Маленький воркер, который опрашивает parser, берёт `ReadyPost`, отдаёт его gateway'ю и репортит результат обратно. **Никакой бизнес-логики.** | — |

Контракт между ними — общий объект `ReadyPost`. План связки и точки
соединения подробно описаны в
[`docs/integration_plan.md`](./docs/integration_plan.md).

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
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── README.md
│   └── .env.example
├── maxapi/                         <- MAX userbot SDK + REST gateway
│   ├── api/                        <- backends, routers, models, storage
│   ├── tests/
│   ├── openapi.yml                 <- авторитетный OpenAPI gateway'я
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── README.md
│   └── images.jpg                  <- маленькая фикстура для SDK quickstart
├── bridge/                         <- WB → MAX bridge worker
│   ├── bridge/                     <- config, clients, translator, worker, CLI
│   ├── tests/                      <- unit + e2e (parser+maxapi через ASGI)
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── README.md
│   └── .env.example
├── docker-compose.yml              <- parser + maxapi + bridge одной командой
├── Procfile.dev                    <- альтернатива для honcho/foreman
└── .env.example                    <- переменные docker-compose
```

Корневые `configs/`, `data/`, `docs/` — это **общие** ресурсы экосистемы.
Любой другой код парсера должен жить только внутри `parser/`, а код
гейтвея — только внутри `maxapi/` (см.
[`docs/integration_plan.md`](./docs/integration_plan.md) § 5).

---

## Быстрый старт через docker-compose

Единая команда для локальной разработки — все три сервиса вместе:

```bash
cp .env.example .env       # отредактируй MAXAPI_*_ID при необходимости
docker compose up --build
```

По умолчанию compose использует `MAXAPI_BACKEND=memory` (стаб MAX),
seed-данные `acc_DEMO0000000000000000000000` / `-1001111111111` и
`MAXAPI_TOKEN=dev-token`. Этого достаточно, чтобы протестировать
полный цикл `parser → bridge → maxapi → parser` без выхода в сеть
MAX.

Для запуска без Docker есть [`Procfile.dev`](./Procfile.dev) (нужны
уже подготовленные venv'ы под каждым каталогом):

```bash
pip install honcho
honcho start -f Procfile.dev
```

## Установка по пакетам

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

### Bridge worker

```bash
cd bridge
python -m venv .venv
. .venv/bin/activate
pip install -e "../parser[dev]" -e "../maxapi[dev,pymax]" -e ".[dev]"
cp .env.example .env       # пропиши WBBRIDGE_MAXAPI_ACCOUNT_ID/CHANNEL_ID
wb-bridge ping             # проверить связность с обоими сервисами
wb-bridge run-loop
```

Полный список переменных — [`bridge/README.md`](./bridge/README.md).

---

## Тесты

```bash
# WB Parser
cd parser && . .venv/bin/activate && pytest -q

# MAX Gateway
cd maxapi && . .venv/bin/activate && pytest -q

# Bridge (включает e2e-тест parser+maxapi через ASGI без сети)
cd bridge && . .venv/bin/activate && pytest -q
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

Шаги 1–6 [`docs/integration_plan.md`](./docs/integration_plan.md)
закрыты: bridge, webhook-приёмник в parser, общий docker-compose —
на месте. Дальнейшие задачи (полноценный `pymax` backend в проде,
web-UI, schedule из YAML, продакшен-БД для idempotency-store) — там
же в § 8.

---

## Локальный боевой запуск на Windows

Для текущего локального деплоя есть supervisor-скрипты в `scripts/`. Они
поднимают и контролируют весь стек:

- WB Parser: `http://127.0.0.1:8000`
- MAX Gateway: `http://127.0.0.1:8080`
- Bridge worker: `wb-bridge run-loop`
- daily-cycle: запуск планирования в `04:00` по Москве

Supervisor читает `.env`, хранит PID-файлы в `data/run/`, пишет логи в
`data/logs/`.

### Запустить или перезапустить

Из корня репозитория:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_local_stack.ps1
```

Для фонового скрытого запуска:

```powershell
Start-Process powershell.exe `
  -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','E:\wbchannel\scripts\run_local_stack.ps1') `
  -WorkingDirectory 'E:\wbchannel' `
  -WindowStyle Hidden
```

Если старый стек уже работает, `run_local_stack.ps1` останавливает процессы из
PID-файлов и запускает свежий стек.

### Остановить

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_local_stack.ps1
```

### Проверить, что всё живо

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8080/health
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'uvicorn|wb-bridge|run_local_stack' } |
  Select-Object ProcessId,ParentProcessId,CommandLine
```

Ожидаемый ответ health: `{"status":"ok", ...}` у обоих сервисов.

### Логи

```powershell
Get-Content .\data\logs\local-stack-supervisor.log -Tail 50
Get-Content .\data\logs\scheduler.log -Tail 50
Get-ChildItem .\data\logs -Filter 'bridge-*.err.log' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  ForEach-Object { Get-Content $_.FullName -Tail 100 }
```

### Запустить планирование вручную

Supervisor сам вызывает daily-cycle в `04:00` по Москве. Вручную:

```powershell
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8000/api/v1/admin/daily-cycle -TimeoutSec 900 -UseBasicParsing
```

### Посмотреть очередь публикаций

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/admin/status | ConvertTo-Json -Depth 8
```
