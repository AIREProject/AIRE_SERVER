# Repository Guidelines

## Project Structure & Module Organization

One package. `app/` is the server edge — `main.py` (assembly), `settings.py`, `models.py` (the chat wire contract, including `CommandType`), `pairing_models.py` (the device/pairing wire contract), `service.py` (`CompanionService`, the one translation), `pairing_service.py`, `identity.py`, `credentials.py`, `errors.py` + `errors_http.py`, `middleware.py`, `logging.py`, `dependencies.py`, `db/` (SQLAlchemy models + repositories behind the device registry), `routes/{chat,ws_chat,system,devices}.py`. `app/brain/` is the Mako brain — `companion.py` (`CompanionBrain`), `contract.py` (`CompanionTurn`/`CompanionReply`/`CompanionAction`), `companions.py` (`COMPANION_PROFILES` registry, currently `"mako"` only), plus `graph/llm/intent/command_intent/dialogue/store/resources/recipes/lore/facts`. Tests are `tests/test_*.py`. A SQLite device registry (`DATABASE_URL`, migrated via `alembic`) backs authentication; `docs/current/` and `docs/archive/` describe **superseded** contracts; the live one is whatever `app/models.py` says.

`app/brain/` may import `app.models` and `app.settings` (both leaves) but should stay free of FastAPI, Starlette, routes and request-context state — it is intelligence, not transport. Nothing enforces this now that the packages are merged; the old import ban existed to keep the two apart and that reason is gone.

`app/__init__.py` stays empty. Adding `from .main import app` works, but makes `import app.brain.graph` drag in FastAPI and Starlette.

Per-turn values ride on `CompanionTurn`. If the brain seems to be missing a value, add a field there rather than passing the whole `ChatRequest` in — the HTTP contract's shape should not reach the routing code. State across turns lives in `app/brain/store.py`, keyed by the opaque `conversation_key`.

Provenance: the server originated in `AI_RE/Backend` (base commit `421865a`). Historical context only — there is no upstream to re-sync with, and nothing here is exempt from the usual quality rules.

## Build, Test, and Development Commands

- `uv sync --dev` — install (Python >=3.13,<3.14).
- `Copy-Item .env.example .env` — local config; `LLM_PROVIDER=mock` needs no API key.
- `uv run alembic upgrade head` — create/upgrade the device-registry DB (`DATABASE_URL`, default `data/companion.db`). Not created on first write like the memory/transcript directories are.
- `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000` — start the API.
- `uv run pytest` — full suite. `uv run ruff check .` — lint.
- `uv run mypy` — type check. Pass no path: `pyproject.toml`'s `files=` covers the package, and an explicit argument silently overrides it.

## Coding Style & Naming Conventions

Four-space indentation, type annotations, 100-character lines. Ruff enforces pycodestyle, Pyflakes, import sorting, bugbear, and Python-upgrade rules. `snake_case` for modules/functions/variables; `PascalCase` for classes and Pydantic models; `UPPER_SNAKE_CASE` for constants. The whole repository must stay ruff- and mypy-clean. Comments/docstrings in Korean — match the surrounding module. Keep async I/O explicit; never leak credentials or trace IDs into responses or logs.

## Testing Guidelines

pytest with `pytest-asyncio` (`asyncio_mode = "auto"`). Name files `test_<area>.py`, tests `test_<behavior>`. Add regression tests with every behavior change. `tests/conftest.py` blocks `.env` and the environment so `Settings` serves code defaults — **state every value an assertion checks** rather than leaning on a default, because `Settings` sets `extra="ignore"` and silently swallows a mistyped field name. Live OpenAI tests are opt-in via the `live_llm` marker (`RUN_LIVE_LLM=1` + key); the default mock provider needs no network.

## Commit & Pull Request Guidelines

Conventional Commits (`feat:`, `docs:`, etc.); keep commits focused. PRs should explain the behavior change, list verification commands, and call out contract changes (`app/models.py`) or new environment variables. Include request/response examples when endpoints change.

## Security & Configuration

Never commit `.env` or secrets; add safe placeholders to `.env.example`. The current product path uses two unconditional fixed public Bearer values: `AIRE_GAME` maps to `GameClient`, `AIRE_WEB` maps to `WebClient`, and both represent the same single player. The canonical identity is profile `AIRE_OPEN`, save slot `demo-slot-1`, companion `mako`; sharing memories, Offline Tasks, and approved state between UE and Web is intentional. Do not add account, save-slot, or companion selection unless product scope explicitly changes. The fixed Bearers bypass `DEVICE_CREDENTIAL_PEPPER`; the old random device registration and pairing routes remain only for compatibility and still use the SQLite registry and pepper. Identity no longer rides in `ChatRequest`. **Still missing on purpose**: request idempotency and an audit trail — a duplicate `request_id` still calls the LLM twice. `game_context` is screened for secret-looking keys before it can reach a prompt; that guard is about what must not be sent to an LLM and is unrelated to auth. Conversation text is capped working memory for prompts only — never logged, never carried into another conversation. Use the mock provider for routine development and keep live-provider tests gated.
