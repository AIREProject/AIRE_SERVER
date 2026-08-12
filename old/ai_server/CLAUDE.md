# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## What this is

A server for the Korean-language survival crafting game's companion, **Mako (마코)**. One companion answering through two surfaces — in-game (standing beside the player) and a mobile chat — from **one pipeline**; the surface picks a tone and nothing else (`app/brain/CLAUDE.md`, "One pipeline, two surfaces"). One package, two halves:

- **`app/brain/`** — the intelligence: two-stage LLM routing, fact-grounded Korean dialogue, verified recipe/enemy/lore stores, per-conversation working memory and per-player long-term memory, Mock/OpenAI/Local providers. This is where the value is.
- **`app/gamedata/`** — the pure static game-data leaf: the chat-supplied Item/Recipe/Smelting/Enemies dataset used both by the Alembic seed and the brain's verified recipe store. `Smelting` is a server extension; it is not a table in `docs/AI_RE.sql`.
- **`app/`** (everything else) — a thin HTTP/WebSocket edge: the `POST /api/v1/chat` and `WS /api/v1/chat` contract, authenticated `/api/v1/tasks` Offline_Task flow, request-context middleware, a uniform error envelope, structured logging.

**Ownership: `app/service.py` owns transport, scope and the wire ledger; `app/brain/` owns intent classification, dialogue, candidate actions, and any state it needs across turns.** The companion phrases every player-facing line; code (regexes + repositories) decides all facts.

> [!IMPORTANT]
> **Authentication and a SQLite-backed device registry are back.** `ChatRequest` no longer carries an identity field — a paired device presents a Bearer token (HTTP `Authorization` header, WS a `token` alongside the envelope), verified against `devices` in the DB (`app/db/`, `app/dependencies.py`). Identity scopes both memory keys, now HMACs of `(profile_id, save_slot_id, …)` (`app/service.py`). Pairing (`register-game` → `pairing-codes` → `pair`, `app/routes/devices.py`, `app/pairing_service.py`) caps devices per profile at `MAX_DEVICES_PER_PROFILE` (default 20) and rejects past it. **Still missing on purpose:** request idempotency and an audit trail — the old `ChatRequestModel`/`MessageModel` were not revived; a duplicate `request_id` still calls the LLM twice. This was a deliberate reversal of an earlier "no auth, no DB" scaffold; see `docs/temporary-scaffolds.md` §2 for the history and what remains out of scope.

## Commands

```powershell
uv sync --dev                                                   # install (Python >=3.13,<3.14)
Copy-Item .env.example .env                                     # local config; fill in DEVICE_CREDENTIAL_PEPPER
uv run alembic upgrade head                                     # create/upgrade device, game-data, and task DB
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000          # run API on :8000
uv run pytest                                                   # full test suite
uv run ruff check .                                             # lint
uv run mypy                                                     # type check (files= in pyproject; pass no path)
```

`DEVICE_CREDENTIAL_PEPPER` unset does not stop the server from starting — health checks and non-authenticated routes work regardless — but every authenticated route (chat, devices) fails per-request with 503 `AuthenticationUnavailable` until it's set (`app/dependencies.py:get_credential_protector`). `TRANSCRIPT_DIR` (gitignored) gets a JSONL per conversation from the first turn; long-term memories are stored in the SQLite `episodic_memories` table after `alembic upgrade head` (the old `LONG_TERM_MEMORY_DIR` JSON files are retained as migration backups). The device, memory, and game-data registry (`DATABASE_URL`, default `data/companion.db`) needs the `alembic upgrade head` step above — it is not created on first write. Static game data is seeded from `app/gamedata/dataset.py`; the current brain reads that same immutable dataset, not the DB game-data tables.

## Where the details live

| Area | File |
|---|---|
| **Handoff / onboarding — start here** | [docs/handoff.md](docs/handoff.md) |
| HTTP/WS edge: wire contract, service, errors, logging | [app/CLAUDE.md](app/CLAUDE.md) |
| The companion brain: routing graph, dialogue, the three memory layers | [app/brain/CLAUDE.md](app/brain/CLAUDE.md) |
| Test conventions and fixtures | [tests/CLAUDE.md](tests/CLAUDE.md) |

`app/main.py:create_app` assembles everything. Seven routers: `chat` (`POST /api/v1/chat`), `ws_chat` (`WS /api/v1/chat`, dispatching both `chat` and `situation` envelope types), `system` (`/health`), `devices` (`/api/v1/devices/*` — pairing and device management), `offline_tasks` (`/api/v1/tasks` — mobile task issue and game-client state transitions), `situations` (`POST /api/v1/situations` — client-triggered situation events; the companion speaks first, no routing, no command), and `admin` (`/api/v1/admin/*` — fixed-token-gated full CRUD over all 11 DB tables; see `app/admin_registry.py`).

## Repository-wide conventions

- Style: 4-space indent, type annotations, 100-char lines. Ruff selects `E,F,I,B,UP,ASYNC,C4,PTH,N,T20,RUF`. MyPy is strict. The whole repository must stay ruff- and mypy-clean.
- Comments/docstrings in Korean — match the surrounding module.
- Conventional Commits (`feat:`, `docs:`, etc.).
- Contract version lives in the URL prefix (`/api/v1`). `ChatRequest.schema_version` is the one accepted body-level version marker and only `1` is valid; responses do not carry a per-model version field.
- `app/brain/` may import `app.models`, `app.settings`, and the pure `app.gamedata` dataset leaf, but stays free of FastAPI, Starlette, SQLAlchemy, routes and request-context state. Nothing enforces this since the packages merged; keep it by hand.

## Docs

- `docs/temporary-scaffolds.md` — values the server fills in **only because a client cannot yet send them**, or guarantees dropped **only because internal development is not finished**, each with the event that triggers its removal and a file-by-file checklist. Anything added under that rationale belongs there the same day it is written.
- `docs/current/` and `docs/archive/` describe **earlier** contracts (a standalone `POST /v1/companion/message`, later a device-authenticated AI_RE-derived backend). They are historical — the live contract is what `app/models.py` says.
