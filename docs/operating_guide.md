# Operating guide — WB → MAX auto-poster (MVP)

Этот документ — пошаговый runbook для админа канала. Всё, что нужно держать в голове на каждый день, описано здесь.

## 0. Карта системы

```
┌──────────┐  collect/score/build/plan-day  ┌────────┐  /api/v1/posts/next  ┌────────┐
│  WB API  │ ─────────────────────────────► │ parser │ ───────────────────► │ bridge │
└──────────┘                                └────────┘                      └────┬───┘
                                                  ▲                              │
                                                  │ webhooks (published/metrics) │
                                                  │                              ▼
                                       ┌─────────────────┐    ┌──────────────────────┐
                                       │ wbpost (CLI)    │    │ maxapi (PyMax-based  │
                                       │ admin.yml       │───►│  REST gateway)       │───► MAX
                                       └─────────────────┘    └──────────────────────┘
```

Один контейнер ≠ одна ответственность:

- **parser** — крутит расписание контента (`/api/v1/admin/daily-cycle`, `plan-day`, `post-once`), хранит SQLite и отдаёт `ReadyPost` по слоту.
- **bridge** — тонкий воркер: забирает пост, отдаёт его в maxapi, пишет результат обратно.
- **maxapi** — REST-обёртка над PyMax, фактический клиент MAX (вход по SMS, постинг альбомов).
- **wbpost** — единственный admin-инструмент: логин, расписание, статус, разовая публикация.
- **scheduler** — крошечный sh-цикл, который раз в сутки в 04:00 MSK дёргает `daily-cycle`.

## 1. Первый запуск (на твоей локальной машине)

### 1.1. Подготовь конфиг

```bash
git clone https://github.com/stormmmmm/wbparser
cd wbparser

cp admin.yml.example admin.yml
# Открой admin.yml в редакторе. Минимум, что нужно проверить:
# - max.phone — твой номер MAX (если оставить пустым, возьмётся из env PHONE_NUMBER)
# - max.channel_name — "Поищи на WB" по умолчанию
# - schedule.slots — сетка слотов; редактируй здесь, если хочешь другие часы
```

### 1.2. Подними сервисы

```bash
docker compose up -d --build
docker compose ps   # все три (parser, maxapi, bridge) должны быть healthy
docker compose logs maxapi --tail=20    # убедись, что MAXAPI слушает 8080
```

### 1.3. Установи wbpost CLI на хосте

```bash
python3 -m venv .venv-tools
source .venv-tools/bin/activate
pip install -e ./wbpost

# Сообщаем wbpost, что сервисы доступны на localhost (а не внутри docker):
export WBPOST_DEPLOYMENT__MAXAPI_URL="http://localhost:8080"
export WBPOST_DEPLOYMENT__PARSER_URL="http://localhost:8000"
```

### 1.4. Логин в MAX (один раз)

```bash
wbpost login
# → Введите 6-значный код из SMS, отправленный на +79991234567:
# → ввод кода
# → Если у аккаунта включена двухфакторка — попросит пароль 2FA.
# Готово. wbpost создал ./data/admin_state.json с account_id и channel_id.
```

После этого SMS больше не запрашивается, пока сессия PyMax жива (`./data/maxapi/<account_id>/`).
Сменишь номер или удалишь сессию — запусти `wbpost login --force`, придёт новая SMS.

### 1.5. Боевой первый пост (разово)

```bash
# Один реальный пост в "Поищи на WB" прямо сейчас:
wbpost post-once --type collection
# или
wbpost post-once --type single
```

После этого сетка работает сама: каждый день в `04:00 Europe/Moscow` контейнер `scheduler`
дёргает `parser /api/v1/admin/daily-cycle`, parser собирает свежее, проставляет `planned_at`
по слотам, bridge публикует в нужное время.

### 1.6. Проверь статус

```bash
wbpost status
# Покажет:
#  - текущий admin_state.json (account_id/channel_id);
#  - здоровье parser и maxapi;
#  - upcoming посты на ближайшие 36 часов;
#  - последние опубликованные посты с ссылкой на сообщение.
```

## 2. Деплой на сервер

`SERVER_USER`, `SERVER_IP`, `SEVRER_PASSWORD` — секреты в Devin/CI окружении.
Минимальный set-up:

```bash
ssh ${SERVER_USER}@${SERVER_IP}
git clone https://github.com/stormmmmm/wbparser
cd wbparser
cp admin.yml.example admin.yml         # отредактировать как в 1.1
docker compose up -d --build

# Логин уже на сервере, чтобы PyMax-сессия лежала там:
python3 -m venv .venv-tools
source .venv-tools/bin/activate
pip install -e ./wbpost
export WBPOST_DEPLOYMENT__MAXAPI_URL="http://localhost:8080"
export WBPOST_DEPLOYMENT__PARSER_URL="http://localhost:8000"
wbpost login                            # запросит SMS — введи код вручную
wbpost post-once --type collection      # один разовый боевой пост
```

После этого ничего делать не нужно — `scheduler` погонит цикл каждый день.

## 3. Каждодневная эксплуатация

```bash
# Здоровье + ближайшая сетка:
wbpost status

# Принудительный полный цикл (если хочется обновить пул сегодня):
wbpost daily-cycle

# Перепланировать "ready" посты на конкретную дату:
wbpost plan-day --date 2026-05-15

# Разовая публикация (кроме сетки) — например, для проверки:
wbpost post-once --type collection
```

## 4. Что попадает в `Поищи на WB`

`channel_analysis_report.txt §10` фиксирует тон канала: эмоция + эмоджи + список
артикул-цена + опрос «Да-❤️ Нет-🔥» + подпись «Нашла на Wildberries ❤️». Текст
постов формирует `parser/app/services/post_builder.py` — именно эти шаблоны.

Жёсткие фильтры:
- `configs/stop_words.yml` блокирует категории `БАД`, `витамины`, `медикаменты`,
  `лекарства` и фразы вроде «вылечит», «снимает боль», «иммуномодулятор».
- `parser/app/services/filter_products.py` дополнительно режет 18+, авто-товары и
  крупную мебель.

Если хочется усилить блок — расширь `configs/stop_words.yml` и
перезагрузи parser:

```bash
docker compose restart parser
```

## 5. Что делать, если SMS-сессия отлетела

```bash
wbpost login --force
# вводишь свежий код, admin_state.json перезаписывается, bridge подхватывает
# обновлённые ids на следующем тике (по умолчанию каждые 15 секунд).
```

Если меняется номер — поправь `max.phone` в `admin.yml`, удали
`data/maxapi/<old_account_id>/` и запусти `wbpost login --force`.

## 6. Тестовый/CI-режим

В CI и юнит-тестах никакой реальный SMS, конечно, не приходит:

- `MAXAPI_BACKEND=memory` (в docker-compose можно проставить через `.env`) — maxapi
  отвечает синтетическими `account_id`/`channel_id` без сети.
- `WBPOST_NONINTERACTIVE=1` — `wbpost login` не зовёт `input()`, а берёт код из
  `WBPOST_SMS_CODE`. Используется в тестах.
- Тесты ходят в parser/maxapi через `respx`/`httpx.ASGITransport`, ничего наружу не уходит.

## 7. Что НЕ делает MVP (сознательно)

- Не накладывает артикул/цену на фото (нужны были «продвинутые фичи» — пока без).
- Не рассылает в несколько каналов одновременно.
- Не маркирует посты как рекламу.
- Не делает динамическое перепланирование на основе плохих реакций (всё статично из admin.yml).
- Не подменяет картинки и не делает A/B-тесты заголовков.

Следующая итерация может закрывать любой пункт точечно — точки расширения уже разнесены
(`post_builder` для текста, `daily_planner` для расписания, отдельный сервис для оверлеев).
