# maxapi — MAX userbot SDK & posting gateway

`maxapi` is a Python module + REST gateway for automating an authorized
[MAX](https://max.ru) user account. It wraps the unofficial
[`maxapi-python`](https://github.com/MaxApiTeam/PyMax) (PyMax) library
and exposes two equivalent surfaces:

* **`api.MaxUserBot`** — a high-level, async, OOP client à la
  [aiogram](https://github.com/aiogram/aiogram) /
  [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI). Use
  it directly from Python; no HTTP, no extra service.
* **FastAPI gateway** — the same operations served as a bearer-token
  protected REST API, contract: [`openapi.yml`](./openapi.yml). Useful
  when several non-Python services need to publish through the same
  account.

Both share the same `MaxBackend` + `Storage` underneath, so behaviour,
authorization, idempotency, formatting and media handling are identical.

> **Userbot disclaimer.** `maxapi-python` reverse-engineers an
> undocumented MAX wire protocol. It is not endorsed by the platform.
> Aggressive automation can get your account rate-limited or banned —
> use it for your own channels and respect MAX's terms.

---

## Table of contents

1. [Quickstart (SDK)](#quickstart-sdk)
2. [Installation](#installation)
3. [Authorization](#authorization)
4. [Finding a channel](#finding-a-channel)
5. [Text formatting & word-links](#text-formatting--word-links)
6. [Attaching media](#attaching-media)
7. [Inline keyboards](#inline-keyboards)
8. [Idempotency](#idempotency)
9. [Scheduling and publication jobs](#scheduling-and-publication-jobs)
10. [Webhooks](#webhooks)
11. [REST gateway](#rest-gateway)
12. [Configuration (env vars)](#configuration-env-vars)
13. [Architecture](#architecture)
14. [`MaxUserBot` reference](#maxuserbot-reference)
15. [Testing](#testing)
16. [Risks & limitations](#risks--limitations)

---

## Quickstart (SDK)

```python
import asyncio
from api import MaxUserBot

async def main() -> None:
    async with MaxUserBot(
        backend_name="pymax",
        work_dir="./.maxapi-data",
    ) as bot:
        # 1. login (only the first time; the session is persisted under work_dir)
        challenge = await bot.start_login(phone="+79991234567")
        account = await bot.verify_login(challenge.challenge_id, code=input("SMS: "))

        # 2. find the channel by its display title (case-insensitive by default)
        target = await bot.find_channel(account.account_id, title="Test")

        # 3. upload two pictures
        with open("images.jpg", "rb") as fh:
            blob = fh.read()
        a = await bot.upload_media(account.account_id, type="image", file=blob, filename="images.jpg")
        b = await bot.upload_media(account.account_id, type="image", file=blob, filename="images.jpg")

        # 4. publish: emoji + word-link + bold/underline/strike
        post = await bot.publish_post(
            account_id=account.account_id,
            channel_id=target.channel_id,
            text=(
                "🚀 Hello from MaxUserBot!\n"
                "🔗 [Открыть Google](https://google.com)\n"
                "✨ **жирный**, __подчёркнутый__, ~~зачёркнутый~~"
            ),
            format="markdown",
            media=[a, b],
        )
        print(post.message_id)

asyncio.run(main())
```

Subsequent runs of the same process (or any process pointed at the same
`work_dir`) reuse the cached session — no SMS code needed:

```python
async with MaxUserBot(backend_name="pymax", work_dir="./.maxapi-data") as bot:
    accounts = bot.list_accounts()           # resumed from disk
    account = next(a for a in accounts if not a.account_id.startswith("acc_DEMO"))
    await bot.publish_post(account.account_id, "<channel_id>", text="resumed!")
```

---

## Installation

```bash
# 1. clone and create a virtualenv
git clone https://github.com/stormmmmm/maxapi.git
cd maxapi
python -m venv .venv && source .venv/bin/activate

# 2. install with the PyMax extra (real userbot)
pip install -e ".[pymax,dev]"
```

Extras:

* `pymax` — adds [`maxapi-python`](https://pypi.org/project/maxapi-python/)
  required for real upstream connectivity. Without it the SDK still
  works against the in-memory backend (handy for tests).
* `dev` — `pytest`, `pytest-asyncio`, `ruff`, `mypy`.

Python ≥ 3.10. PyMax warns about Python 3.12 SSL quirks in its logs;
3.10/3.11 are the safest bet for production.

---

## Authorization

There are two authorization layers, and they are independent:

### 1. Upstream MAX session (mandatory)

The SDK and the gateway both delegate to a `MaxBackend`. Real upstream
auth happens once per phone number:

```python
challenge = await bot.start_login(phone="+79991234567")
# MAX sends an SMS to that phone with a 6-digit code
account = await bot.verify_login(challenge.challenge_id, code="123456")
# account.account_id ↔ persisted session under
# {work_dir}/{account_id}/{account.json, session.db}
```

Persistence:

* `MAXAPI_PYMAX_WORK_DIR` (default `/var/lib/maxapi`) holds one
  subdirectory per logged-in account. Subsequent runs of `start()` /
  the FastAPI lifespan resume those sessions automatically — no SMS
  code is requested.
* `bot.logout(account_id)` closes the upstream session and removes the
  local record. The SMS would have to be re-issued next time.

### 2. Gateway bearer token (HTTP only)

The FastAPI gateway requires `Authorization: Bearer <MAXAPI_TOKEN>` on
every endpoint except `GET /health`. The token is **operator-level**:
it identifies who is allowed to call the gateway, not which MAX
account is being used (that's selected by `accountId` in the path).

When you embed `MaxUserBot` directly into a Python program, there is
**no bearer token** — process membership is your authentication
boundary, just like with `aiogram.Bot`.

### Verifying that auth works

```python
# After the very first verify_login, restart the process and:
async with MaxUserBot(backend_name="pymax", work_dir="./.maxapi-data") as bot:
    real = [a for a in bot.list_accounts() if not a.account_id.startswith("acc_DEMO")]
    assert real, "session not resumed — auth is broken"
    status = bot.get_account_status(real[0].account_id)
    assert status.can_publish
```

The SDK is covered by `tests/test_client_sdk.py` (login → verify →
post → edit → delete with an in-memory backend) and by the live PyMax
test in `scripts/sdk_live_test.py` that publishes into a real channel.

---

## Finding a channel

When you want to publish a post you need a `channel_id`. The SDK and
the REST gateway offer three complementary ways to obtain one:

| Input you have                          | SDK call                                               | REST endpoint                                                           |
| --------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------- |
| Human-readable title (e.g. `"Test"`)    | `await bot.find_channel(account_id, title=...)`        | `GET /v1/accounts/{accountId}/channels/find?title=...`                  |
| `@username` / public link / numeric id  | `await bot.resolve_channel(account_id, link=...)`      | `GET /v1/accounts/{accountId}/channels/resolve?link=...`                |
| Enumerate + filter client-side          | `await bot.list_channels(account_id)`                  | `GET /v1/accounts/{accountId}/channels[?title=...&title_match=...]`     |

### By display title — `find_channel`

This is usually what you want when the user only knows the channel's
name as it appears in the MAX app:

```python
target = await bot.find_channel(account.account_id, title="Test")
await bot.publish_post(account.account_id, target.channel_id, text="hi!")
```

Full signature:

```python
await bot.find_channel(
    account_id: str,
    *,
    title: str,
    exact: bool = True,              # True → whole title must equal `title`
                                     # False → substring / "contains" match
    case_insensitive: bool = True,   # uses str.casefold() on both sides
    only_writable: bool = False,     # skip channels the account cannot post in
) -> Channel
```

Behaviour:

* Returns **one** `Channel` when exactly one matches.
* Raises `api.errors.NotFoundError` (HTTP 404, `code=channel_not_found`)
  when nothing matches.
* Raises `api.errors.ConflictError` (HTTP 409, `code=channel_title_ambiguous`)
  when more than one channel matches — refine with `exact=True` (default)
  or inspect the full match set via `find_channels(...)`.

Useful variations:

```python
# Case-insensitive substring search — "finds" in any channel title.
matches = await bot.find_channels(
    account.account_id, title="finds", exact=False
)
for ch in matches:
    print(ch.channel_id, ch.title)

# Exact but case-sensitive (rare but supported):
await bot.find_channel(
    account.account_id, title="Test", case_insensitive=False
)

# Only look at channels the account can actually publish to:
await bot.find_channel(
    account.account_id, title="News", only_writable=True
)
```

Both `find_channel` and `find_channels` refresh the channel list from
the upstream on every call, so titles are always current. If you plan
to call them repeatedly, cache the result — each call is an upstream
round-trip.

### By `@username`, public link, or numeric id — `resolve_channel`

Use this when you already know a stable identifier. It goes through
the MAX upstream resolver rather than scanning the account's chat
list:

```python
await bot.resolve_channel(account.account_id, "@wb_finds_demo")
await bot.resolve_channel(account.account_id, "https://max.ru/wb_finds_demo")
await bot.resolve_channel(account.account_id, "-74082320910346")
```

### Enumerate and filter — `list_channels`

`list_channels` returns everything the account can see; pair it with
the optional `title`, `title_match`, and `case_insensitive` filters on
the REST endpoint to narrow the result server-side:

```bash
# Every channel whose title contains "news", case-insensitive:
curl -H "Authorization: Bearer $TOKEN" \
  "$BASE/v1/accounts/$ACCOUNT/channels?title=news&title_match=contains"
```

In the SDK there's no title filter on `list_channels` itself — use
`find_channels(...)` for that.

---

## Text formatting & word-links

Set `format="markdown"` to enable a small Markdown subset; the SDK
parses it once and emits the wire elements MAX expects (`type`, `from`,
`length`, optional `attributes`).

| Markdown source                | MAX wire element  | Effect                              |
| ------------------------------ | ----------------- | ----------------------------------- |
| `[label](https://example.com)` | `LINK`            | **Word-link** — clickable label     |
| `**жирный**`                   | `STRONG`          | **Bold**                            |
| `__подчёркнутый__`             | `UNDERLINE`       | <ins>Underline</ins>                |
| `~~зачёркнутый~~`              | `STRIKETHROUGH`   | ~~Strike-through~~                  |
| `*курсив*`                     | `EMPHASIZED`      | *Italic*                            |
| Bare `https://example.com`     | (auto-link)       | Client auto-detects, no markup needed |

Allowed link schemes: `http`, `https`, `mailto`, `tg`, `max`. Markers
themselves (`**`, `__`, etc.) are stripped before sending — only the
clean text plus a list of element offsets reaches the wire.

Emojis are plain Unicode characters — paste them in directly.

```python
text = (
    "🚀 [Открыть на сайте](https://example.com/product/42)\n"
    "✨ **жирный**, __подчёркнутый__, ~~зачёркнутый~~, *курсивный*"
)
await bot.publish_post(account_id, channel_id, text=text, format="markdown")
```

`format="plain"` (default) sends the text as-is; the MAX client still
auto-links bare URLs.

---

## Attaching media

`MaxUserBot.publish_post(media=[...])` accepts up to 10 attachments, in
any of three forms:

```python
photo = await bot.upload_media(account_id, type="image", file="cover.jpg")
imported = await bot.import_media(account_id, type="image", url="https://cdn.example.com/x.jpg")

await bot.publish_post(
    account_id, channel_id,
    text="📸 пост с галереей",
    format="markdown",
    media=[
        photo,                                       # Media instance
        imported.media_id,                           # plain media_id
        MediaRef(media_id=photo.media_id, position=3),  # explicit position
    ],
)
```

`upload_media` accepts raw `bytes` or a path-like object. `import_media`
fetches an HTTP/HTTPS URL server-side. Both deduplicate via the
`Idempotency-Key` parameter (see below) and return a `Media` object you
can hand to subsequent posts.

Supported `type` values: `image`, `video`, `audio`, `document`. Posts
without text are allowed if at least one attachment is present.

The two-step pipeline (`upload` → `media_id`) lets you reuse the same
file across posts without re-uploading.

---

## Inline keyboards

```python
from api.models.posts import InlineButton, InlineKeyboard

keyboard = InlineKeyboard(rows=[
    [
        InlineButton(text="Открыть товар", payload="https://example.com/p/42"),
        InlineButton(text="Поделиться", payload="share:42"),
    ],
    [InlineButton(text="Подписаться", payload="https://max.ru/wb_finds_demo")],
])

await bot.publish_post(account_id, channel_id, text="...", inline_keyboard=keyboard)
```

Up to 30 rows, each row 1+ buttons; button `text` ≤ 64 chars,
`payload` ≤ 1024.

---

## Idempotency

Pass `idempotency_key=...` (a UUID is conventional but any string ≤ 64
chars works) to `upload_media`, `import_media`, `publish_post`,
`schedule_post`, `create_publication_job`, `publish_scheduled_now`. A
repeat call with the same key returns the cached result without
re-running the action — safe for retries:

```python
import uuid
key = str(uuid.uuid4())
post = await bot.publish_post(account_id, channel_id, text="hi", idempotency_key=key)
again = await bot.publish_post(account_id, channel_id, text="hi", idempotency_key=key)
assert again.message_id == post.message_id
```

The cache is per-process (in-memory) and per-operation. The HTTP
gateway implements the same semantics via the `Idempotency-Key` header.

---

## Scheduling and publication jobs

For "schedule for later":

```python
from datetime import datetime, timedelta, timezone

scheduled = bot.schedule_post(
    account_id, channel_id,
    publish_at=datetime.now(timezone.utc) + timedelta(hours=2),
    post=PublishPostRequest(
        text="📦 Автопост в 14:00",
        format="markdown",
        media=[MediaRef(media_id=photo.media_id)],
    ),
)
```

`update_scheduled`, `cancel_scheduled`, `publish_scheduled_now` are
also exposed.

For the WB-parser pipeline (a richer "ready post" envelope with
freshness windows and version metadata):

```python
job = await bot.create_publication_job(
    account_id,
    channel_id=channel_id,
    ready_post=ready,                                      # ReadyPost from upstream
    mode="auto",                                           # publish_now | schedule | dry_run
    publish_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    options=PublishOptions(disable_notification=True, pin_after_publish=True),
)
```

Status transitions follow the OpenAPI spec
(`accepted → scheduled → published`, plus `failed`, `cancelled`,
`expired`).

---

## Webhooks

Subscribe URLs receive event payloads (`publication.published`,
`publication.failed`, `publication.scheduled`, `metrics.collected`,
…). Out-of-the-box the SDK only **stores** the subscription; you wire
delivery into your own scheduler. Subscriptions look like:

```python
sub = bot.add_webhook(
    url="https://ops.example.com/maxapi/webhooks",
    events=["publication.published", "publication.failed"],
    secret="…",      # used for HMAC-SHA256(signature) headers
)
```

See [`openapi.yml`](./openapi.yml) (`#WebhookEventEnvelope`) for the
event payload shape and HMAC verification details.

---

## REST gateway

The same package ships a FastAPI app — useful when several services
share one MAX account or are written in non-Python languages.

```bash
pip install -e ".[pymax]"
export MAXAPI_BACKEND=pymax
export MAXAPI_PYMAX_WORK_DIR=./.maxapi-data
export MAXAPI_TOKEN="$(openssl rand -hex 32)"
maxapi                       # uvicorn api.main:app on 0.0.0.0:8080
```

Quick sanity:

```bash
curl http://localhost:8080/health
curl -H "Authorization: Bearer $MAXAPI_TOKEN" \
     http://localhost:8080/v1/accounts
```

Login flow over HTTP (mirrors `bot.start_login` / `bot.verify_login`):

```bash
curl -X POST http://localhost:8080/v1/accounts/login/start \
     -H "Authorization: Bearer $MAXAPI_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"phone": "+79991234567", "device_name": "ops"}'
# -> { "challenge_id": "chg_…", "expires_at": "...", "delivery": "sms", ... }

curl -X POST http://localhost:8080/v1/accounts/login/verify \
     -H "Authorization: Bearer $MAXAPI_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"challenge_id": "chg_…", "code": "123456"}'
# -> { "account": { "account_id": "acc_…", "status": "connected", ... } }
```

Publishing from `curl`:

```bash
curl -X POST "http://localhost:8080/v1/accounts/$ACC/channels/$CH/posts" \
     -H "Authorization: Bearer $MAXAPI_TOKEN" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: $(uuidgen)" \
     -d '{
       "text": "🚀 [Открыть Google](https://google.com), а ещё **жирный**.",
       "format": "markdown",
       "media": [{"media_id": "med_…"}]
     }'
```

The full contract — every path, schema, example and error envelope —
lives in [`openapi.yml`](./openapi.yml). Swagger UI is mounted at
`/docs`; ReDoc at `/redoc`.

---

## Configuration (env vars)

| Variable                    | Default               | Used by | Purpose                                                 |
| --------------------------- | --------------------- | ------- | ------------------------------------------------------- |
| `MAXAPI_HOST`               | `0.0.0.0`             | gateway | HTTP bind address                                       |
| `MAXAPI_PORT`               | `8080`                | gateway | HTTP TCP port                                           |
| `MAXAPI_TOKEN`              | `dev-token`           | gateway | Bearer token expected on `Authorization`                |
| `MAXAPI_MAX_UPLOAD_BYTES`   | `52428800`            | both    | Hard cap on uploaded media size                         |
| `MAXAPI_BACKEND`            | `memory`              | both    | `memory` (stub) or `pymax` (real userbot)               |
| `MAXAPI_PYMAX_WORK_DIR`     | `/var/lib/maxapi`     | PyMax   | Per-account session cache (resumed at startup)          |
| `MAXAPI_PYMAX_DEVICE_TYPE`  | `DESKTOP`             | PyMax   | `DESKTOP` for phone+SMS, `WEB` for QR-pairing           |
| `MAXAPI_PYMAX_APP_VERSION`  | `25.12.13`            | PyMax   | Version string sent during the handshake                |
| `MAXAPI_UPSTREAM_URL`       | _unset_               | both    | Reserved for alternative upstream backends              |
| `MAXAPI_UPSTREAM_TOKEN`     | _unset_               | both    | Reserved for alternative upstream backends              |

When you instantiate `MaxUserBot` programmatically the same values can
be supplied via constructor kwargs (`work_dir`, `device_type`,
`app_version`, `backend_name`).

---

## Architecture

```
┌────────────────────────────┐    ┌────────────────────────────┐
│  Python program            │    │  HTTP client (any language)│
│   `from api import         │    │                            │
│        MaxUserBot`         │    │   GET /v1/...              │
│   bot.publish_post(...)    │    │   Authorization: Bearer    │
└──────────────┬─────────────┘    └──────────────┬─────────────┘
               │                                  │
               ▼                                  ▼
        ┌───────────────────────────────────────────────┐
        │   api.client.MaxUserBot                       │
        │   api.routers.* (FastAPI)                     │
        ├───────────────────────────────────────────────┤
        │   api.storage.Storage                         │
        │   (accounts, posts, schedules, jobs,          │
        │    idempotency cache, webhooks)               │
        ├───────────────────────────────────────────────┤
        │   api.backends.MaxBackend                     │
        │   ├── InMemoryBackend (tests)                 │
        │   └── PyMaxBackend → maxapi-python → MAX wire │
        └───────────────────────────────────────────────┘
```

The SDK and the FastAPI app are peers: they both compose the same
`Storage` + `MaxBackend`. Adding a new transport (gRPC, WS, …) is
"another wrapper around `Storage` + `backend`".

---

## `MaxUserBot` reference

All methods are async unless noted. Constructor:

```python
MaxUserBot(
    *,
    backend: MaxBackend | None = None,        # pre-built backend
    storage: Storage | None = None,           # share state with FastAPI app
    backend_name: str | None = None,          # "memory" | "pymax"; defaults to MAXAPI_BACKEND
    work_dir: str | os.PathLike | None = None,  # PyMax session dir
    device_type: str = "DESKTOP",
    app_version: str = "25.12.13",
    settings: Settings | None = None,
)

await bot.start()                  # resume persisted accounts (idempotent)
await bot.close()                  # close upstream sessions
async with MaxUserBot(...) as bot: ...
```

### Accounts

| Method | Description |
| ------ | ----------- |
| `list_accounts()` | All locally-known accounts. **sync** |
| `await start_login(phone, *, device_name="...", callback_url=None)` | Start phone-login; sends an SMS code. Returns `StartLoginResponse`. |
| `await verify_login(challenge_id, code, *, two_factor_password=None)` | Consume the SMS code; returns a connected `Account`. |
| `get_account(account_id)` | **sync** |
| `get_account_status(account_id)` | **sync** — local status snapshot. |
| `await logout(account_id)` | Close upstream session and forget locally. |

### Channels

| Method | Description |
| ------ | ----------- |
| `await list_channels(account_id, *, only_writable=True, role=None)` | Fresh list from upstream; cached locally. |
| `await resolve_channel(account_id, link)` | Resolve `@username`, public link, or numeric id. |
| `await find_channel(account_id, *, title, exact=True, case_insensitive=True, only_writable=False)` | Find **one** channel by its display title. Raises `NotFoundError` if nothing matches, `ConflictError` if multiple match. |
| `await find_channels(account_id, *, title, exact=True, case_insensitive=True, only_writable=False)` | Return **every** channel whose title matches. Use `exact=False` for substring match. |
| `get_channel(account_id, channel_id)` | **sync** |
| `get_channel_permissions(account_id, channel_id)` | **sync** |

**Finding a channel by its name.** When you only know the human-readable
title (e.g. "Test") and need the `channel_id` for publishing:

```python
channel = await bot.find_channel(account.account_id, title="Test")
await bot.publish_post(account.account_id, channel.channel_id, text="Hello!")
```

`find_channel` is case-insensitive by default and requires an exact title
match. Pass `exact=False` for substring matching (`contains`). The REST
equivalent is `GET /v1/accounts/{accountId}/channels/find?title=Test`; if
you want to list every channel matching a query, use
`GET /v1/accounts/{accountId}/channels?title=Test&title_match=contains`.

### Media

| Method | Description |
| ------ | ----------- |
| `await upload_media(account_id, *, type, file, filename=None, mime_type=None, caption=None, idempotency_key=None)` | `file` accepts `bytes` or a path. |
| `await import_media(account_id, *, url, type, filename=None, source_post_id=None, idempotency_key=None)` | Fetch a remote URL server-side. |
| `get_media(media_id)` | **sync** |

### Posts

| Method | Description |
| ------ | ----------- |
| `await publish_post(account_id, channel_id, *, text, format="plain", media=(), title=None, external_id=None, source=None, metadata=None, inline_keyboard=None, disable_notification=False, pin_after_publish=False, remove_previous_pin=False, link_preview=True, dry_run=False, idempotency_key=None)` | Returns a `PublishedPost`. |
| `await publish(...)` | Alias for `publish_post`. |
| `validate_post(account_id, channel_id, request)` | **sync** dry-run. |
| `list_posts(account_id, channel_id, *, since=None)` | **sync** |
| `get_post(account_id, channel_id, message_id)` | **sync** |
| `await edit_post(account_id, channel_id, message_id, *, text=None, format=None, media=None, inline_keyboard=None, metadata=None)` | Pass `media=[]` to clear, omit to keep. |
| `await delete_post(account_id, channel_id, message_id)` |  |
| `await pin_post(account_id, channel_id, message_id, *, notify_subscribers=False)` |  |
| `await unpin_post(account_id, channel_id, message_id)` |  |
| `get_post_metrics(account_id, channel_id, message_id)` | **sync** stub (real metrics depend on PyMax adding the call). |

### Scheduling

| Method | Description |
| ------ | ----------- |
| `list_scheduled(account_id, *, channel_id=None, status=None)` | **sync** |
| `schedule_post(account_id, channel_id, publish_at, post, *, timezone_name="UTC", idempotency_key=None)` | **sync** |
| `get_scheduled(account_id, schedule_id)` | **sync** |
| `update_scheduled(account_id, schedule_id, *, publish_at=None, post=None)` | **sync** |
| `cancel_scheduled(account_id, schedule_id)` | **sync** |
| `await publish_scheduled_now(account_id, schedule_id, *, idempotency_key=None)` |  |

### Publication jobs (WB pipeline)

| Method | Description |
| ------ | ----------- |
| `list_publication_jobs(account_id, *, status=None, source=None)` | **sync** |
| `await create_publication_job(account_id, *, channel_id, ready_post, mode="auto", publish_at=None, options=None, idempotency_key=None)` |  |
| `get_publication_job(account_id, job_id)` | **sync** |
| `cancel_publication_job(account_id, job_id)` | **sync** |

### Webhooks

| Method | Description |
| ------ | ----------- |
| `list_webhooks()` | **sync** |
| `add_webhook(url, events, *, secret=None)` | **sync** |
| `remove_webhook(subscription_id)` | **sync** |

### Errors

The SDK raises:

* `api.errors.NotFoundError` (HTTP 404 in the gateway)
* `api.errors.ConflictError` (HTTP 409)
* `api.errors.ForbiddenError` (HTTP 403)
* `api.errors.ValidationFailedError` (HTTP 422)
* `api.errors.UnauthorizedError` (HTTP 401)
* `pydantic.ValidationError` for malformed input that violates the
  schema (e.g. `text` over 4 000 chars).

---

## Testing

```bash
pip install -e ".[dev]"
ruff check api tests
pytest -q
```

* `tests/test_client_sdk.py` — covers the `MaxUserBot` SDK end-to-end
  (login, channels, upload, publish with markdown word-link, edit,
  delete, pin/unpin, idempotency, channel auto-resolve) against the
  in-memory backend.
* `tests/test_*.py` — TestClient coverage of every FastAPI route from
  the OpenAPI spec (auth, accounts, channels, posts, schedules, jobs,
  media, webhooks).
* `scripts/sdk_live_test.py` — live PyMax test that resumes a real
  session, uploads two photos and publishes a markdown post into an
  actual channel.

OpenAPI validation:

```bash
python -c "
from openapi_spec_validator import validate_spec; import yaml
validate_spec(yaml.safe_load(open('openapi.yml')))
print('valid')
"
```

---

## Risks & limitations

* **PyMax is unofficial.** The MAX wire protocol can (and does) change
  underneath the library. Keep `maxapi-python` updated and pin a
  version that matches your `MAXAPI_PYMAX_APP_VERSION`.
* **Rate-limits are real.** The SDK does no rate-limiting beyond
  forwarding upstream errors; build your own throttling on top if you
  publish in bulk.
* **Persistence is local.** `Storage` is in-memory; for HA deployments
  swap it out for a database-backed implementation (the API surface is
  small enough — see `api/storage.py`).
* **Metrics are placeholder.** `get_post_metrics` returns zeroed
  counters until a real upstream metrics call is wired through PyMax.

---

## License

MIT. The `maxapi-python` dependency is released under its own
license — review it before redistributing binaries.
