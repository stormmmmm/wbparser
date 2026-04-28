# MAX Userbot Posting API

FastAPI implementation of the contract defined in [`openapi.yml`](../openapi.yml).
The service exposes a bearer-token gated REST API used by the WB parser to
publish ReadyPost payloads to MAX channels through an authorized user
account session.

## Layout

```
api/
├── main.py            FastAPI factory + entrypoint (`maxapi` console script)
├── config.py          Settings loaded from env vars (MAXAPI_*)
├── security.py        Bearer-token auth dependency
├── errors.py          API error hierarchy + handlers (ErrorResponse / ValidationErrorResponse)
├── deps.py            Shared FastAPI dependencies (auth, pagination, idempotency, backend)
├── pagination.py      Cursor encode/decode + slicing helper
├── ids.py             ULID-style identifier generators
├── storage.py         Thread-safe in-memory state (accounts, posts, jobs, idempotency)
├── backends/          Pluggable upstream-MAX adapters (the actual userbot)
│   ├── protocol.py    MaxBackend abstract base + UpstreamChannel/Media/Message
│   ├── memory.py      InMemoryBackend (default; no network, used by tests)
│   ├── pymax_backend.py  PyMaxBackend (real MAX userbot via maxapi-python)
│   └── __init__.py    build_backend(settings) factory
├── models/            Pydantic models that mirror the OpenAPI components
└── routers/           One router per OpenAPI tag (health, accounts, channels,
                       media, posts, schedules, jobs, webhooks)
```

## Architecture

The service is a **gateway** in front of an interchangeable backend:

* The HTTP layer (`api/routers/*`) is identical for every backend and
  enforces the OpenAPI contract, auth, validation, idempotency, and
  pagination.
* `Storage` keeps gateway-side state only — accounts, posts, schedules,
  jobs, webhooks, and the idempotency cache.
* A `MaxBackend` performs every operation that touches the real MAX
  network (`start_login`, `verify_login`, `list_channels`,
  `publish_message`, `edit_message`, `delete_message`, `pin_message`,
  `upload_media`, ...).

Two backends ship in this repo:

* `memory` (default): a deterministic, dependency-free stub used for
  development, the test suite, and CI. `verify_login` accepts the code
  `000000`. No external traffic.
* `pymax`: a **real userbot** wired through
  [maxapi-python](https://github.com/MaxApiTeam/PyMax). Authenticates by
  phone + SMS code (DESKTOP profile) and publishes messages from the
  authorized user's account.

> **Heads-up.** PyMax reverse-engineers an undocumented MAX protocol.
> It is not endorsed by the platform; aggressive automation may get the
> account rate-limited or banned.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `MAXAPI_HOST` | `0.0.0.0` | Bind address |
| `MAXAPI_PORT` | `8080` | TCP port |
| `MAXAPI_TOKEN` | `dev-token` | Bearer token expected on `Authorization` |
| `MAXAPI_MAX_UPLOAD_BYTES` | `52428800` | Max media upload size |
| `MAXAPI_BACKEND` | `memory` | Upstream backend: `memory` or `pymax` |
| `MAXAPI_PYMAX_WORK_DIR` | `/var/lib/maxapi` | Per-account session cache (PyMax) |
| `MAXAPI_PYMAX_DEVICE_TYPE` | `DESKTOP` | PyMax device profile (`DESKTOP` for phone login, `WEB` for QR) |
| `MAXAPI_PYMAX_APP_VERSION` | `25.12.13` | PyMax app version string |
| `MAXAPI_UPSTREAM_URL` | _unset_ | Reserved for alternative upstreams |
| `MAXAPI_UPSTREAM_TOKEN` | _unset_ | Reserved for alternative upstreams |

To enable the real userbot:

```bash
pip install -e ".[pymax]"
export MAXAPI_BACKEND=pymax
maxapi
```

Then drive login through the standard endpoints — the gateway will
relay the SMS challenge to the real account:

```bash
curl -X POST -H "Authorization: Bearer $MAXAPI_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"phone":"+79991112233","device_name":"laptop"}' \
     http://localhost:8080/v1/accounts/login/start
# ...wait for SMS, then verify with the 6-digit code:
curl -X POST -H "Authorization: Bearer $MAXAPI_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"challenge_id":"chg_...","code":"123456"}' \
     http://localhost:8080/v1/accounts/login/verify
```

## Running

```bash
pip install -e .
maxapi               # uvicorn api.main:app on 0.0.0.0:8080
# or
uvicorn api.main:app --reload
```

Health check (no auth required):

```bash
curl http://localhost:8080/health
```

Authenticated example:

```bash
curl -H "Authorization: Bearer dev-token" \
     http://localhost:8080/v1/accounts
```

The default in-memory backend seeds a demo account
(`acc_DEMO0000000000000000000000`) with a single owned channel
(`-1001111111111`) so smoke tests work out of the box. Set
`MAXAPI_BACKEND=pymax` to switch to the real MAX userbot.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
