# app/ — the HTTP/WebSocket edge

A thin transport shell around `app/brain/`. See the root [CLAUDE.md](../CLAUDE.md) for what the project is and the repository-wide rules; see [brain/CLAUDE.md](brain/CLAUDE.md) for the companion itself.

Modules: `main.py` (assembly), `settings.py`, `models.py` (the chat wire contract including `CommandType`), `pairing_models.py` (the device/pairing wire contract), `offline_task_models.py` (the Offline_Task wire contract), `admin_models.py` (the admin-CRUD wire contract, one schema trio per table), `service.py`, `embedding.py` (OpenAI/Local/mock vector providers), `pairing_service.py`, `offline_task_service.py`, `admin_registry.py` + `admin_repository.py` + `admin_service.py` (the table-agnostic admin CRUD engine behind `routes/admin.py` — see "Admin CRUD" below), `identity.py` (`AuthenticatedDevice`), `credentials.py` (`CredentialProtector`, HMAC token/pairing-code hashing), `errors.py` + `errors_http.py`, `middleware.py`, `logging.py`, `dependencies.py`, `db/` (SQLAlchemy models + repositories behind the device, game-data, Offline_Task, and episodic-memory registries; `game_data_loader.py` reads the game-data tables back into a `GameDataSet` for brain startup), `gamedata/` (pure game-data dataset shared by migrations and the brain), `routes/{chat,ws_chat,system,devices,offline_tasks,admin}.py`.

## Admin CRUD (`/api/v1/admin/*`)

A separate, fixed-token-gated surface (`get_admin_token`, `app/dependencies.py`) exposing full create/read/update/delete over all 11 DB tables — distinct from the game/mobile contract above, which stays purpose-built (only what chat/pairing/tasks need). `admin_registry.py:ADMIN_RESOURCES` is the one place all 11 tables are wired to their schemas; `admin_repository.py`/`admin_service.py` are table-agnostic (`AdminCrudService[ResponseT]`), so adding a 12th table means a schema trio in `admin_models.py` plus one `AdminResourceSpec`, not a new repository class. Credential-hash columns (`devices.token_hash`/`token_lookup_id`, `pairing_codes.code_hash`) are never in any admin schema — exposure/write eligibility is allowlist-only, not a filtered blocklist. Deletes reject with 409 if a child row exists, whether or not the reference is a declared `ForeignKey` (`items → recipes/smelting_recipes.result_item_id` is not, so that path gets an explicit pre-delete count check — see `AdminResourceSpec.non_fk_children`).

**Admin writes to game-data tables (items/recipes/smelting_recipes/enemies/locations) change the brain's dialogue only on the next restart, not live.** `app/main.py:create_app` reads those tables once at import time (`_load_startup_game_dataset`) and hands the result into `RecipeRepository`/`EnemyRepository` as a plain `GameDataSet` — those two brain modules still never import SQLAlchemy (`app/brain/CLAUDE.md`'s import rule holds; the DB read happens in `app/db/game_data_loader.py` and `app/main.py`, outside `app/brain/`). An unmigrated or partially-seeded DB (items present but not recipes/enemies — the common shape when a test inserts one `ItemModel` row just to satisfy a foreign key) silently falls back to the static `app/gamedata/dataset.py` `DATASET`, the same way a missing `DEVICE_CREDENTIAL_PEPPER` narrows to a 503 instead of failing app boot. `app/brain/command_intent.py`'s Mock-provider/fallback routing vocabulary (`RECIPE_PATTERN`/`ENEMY_PATTERN`) is a separate, module-import-time constant that stays tied to the static `DATASET` regardless — an admin-added item is only guaranteed reachable through a real LLM provider's classification, not the regex fallback.

## The request contract

`app/models.py` is deliberately minimal — every field is consumed:

| Field | Used for |
|---|---|
| `request_id` | correlation, echoed back, must match `X-Request-ID` if sent |
| `schema_version` | optional request-body version marker; only `1` is accepted |
| `session_id` | one axis of the conversation scope (and so of the transcript) |
| `save_slot_id` | the other axis of the conversation scope, and **all** of the long-term-memory scope, alongside the authenticated `profile_id` |
| `companion_id` | which entry of `app/brain/companions.py`'s `COMPANION_PROFILES` answers; only `"mako"` is registered today |
| `profile_id` / `device_id` | optional identity claims, cross-checked against the Bearer-authenticated `AuthenticatedDevice` (`IdentityScopeMismatchError` on mismatch) — identity itself no longer rides in the body |
| `user_message` | the utterance Mako answers |
| `message_id` | optional client message identifier, echoed in `ChatResponse` |
| `surface` | which window Mako is speaking through (`game` / `mobile`) — **tone only**; defaults to `game` |
| `time_context` | optional turn-scoped game clock, rendered into the dialogue prompt |
| `recent_event_ids` | optional event identifiers; validated at the request boundary, not yet interpreted |
| `game_context` | `location_id` for lore; screened for secret-looking keys |
| `allowed_commands` | the gate on what Mako may emit |

Identity itself is resolved outside the body: HTTP reads `Authorization: Bearer <token>` (`Depends(get_authenticated_device)`); WS reads a `token` field alongside `payload` in the envelope, verified per frame (`authenticate_device_token`, since WS has no per-message header). Both paths call the same `app/dependencies.py:authenticate_device_token`, which looks the token up in `devices` (`app/db/models.py`) and rejects a missing, unknown, or revoked one as `UnauthorizedDeviceError`.

## The situation contract (`POST /api/v1/situations`, and `situation` over WS)

A second, narrower request/response pair (`SituationRequest`/`SituationResponse`) for client-triggered situation events — the companion speaks first, with no player utterance. It reuses `session_id`/`save_slot_id`/`companion_id`/`profile_id`/`device_id`/`surface`/`time_context` from the chat contract verbatim (same identity checks, same scope semantics), but replaces `user_message` with `situation: list[str]` (1–4 free-text lines the client observed) and has **no** `game_context`/`allowed_commands`/`recent_event_ids` — there is no fact lookup and no command to gate, so those fields would be dead weight (`extra="forbid"` rejects them if sent). `SituationResponse` mirrors `ChatResponse` minus `command_candidates`/`offline_task_id`/`message_id`.

`app/routes/situations.py` is a near-duplicate of `routes/chat.py` (same `X-Request-ID` reconciliation), and `routes/ws_chat.py` dispatches both envelope types (`chat` / `situation`) off one small table (`_FRAME_SPECS`) because `CompanionService.create_response` and `create_situation_response` share the exact signature `(request, identity, session, protector) -> response`. See `app/brain/CLAUDE.md`'s "Situation events" section for the brain-side entry point (`CompanionBrain.react`, not `respond`) and why it skips routing entirely.

## One translation, and the service owns it

```
ChatRequest ──map──> CompanionTurn ──> CompanionBrain.respond() ──> CompanionReply ──map──> ChatResponse
```

The route does not do the mapping, because four values are knowable only at assembly time: the brain, the command TTL, the default location, and **`AIMetadata`**. That last one reports the *fallback-aware* provider selection (`LLM_PROVIDER=openai` with no key is really mock), which only `build_llm_provider` can report; rebuilding it from `Settings` makes the metadata lie. `CompanionService` is therefore an app-lifetime singleton on `app.state.companion`, and lifespan teardown calls its `aclose()`.

`get_companion` (`dependencies.py`) is annotated `HTTPConnection`, not `Request` — FastAPI does not inject `Request` into WebSocket routes.

## Invariants

- **Two opaque keys are the only thing the server tells the companion about identity**, both derived in `service.py` and nowhere else. `_conversation_key` is an HMAC (`CredentialProtector.hash_value`, keyed by `DEVICE_CREDENTIAL_PEPPER`) over `json([profile_id, save_slot_id, companion_id, session_id])`, scoping a *conversation*; `_player_key` is the same HMAC over `json([profile_id, save_slot_id])`, scoping a *save slot within a profile* and surviving a new `session_id`/`companion_id`. JSON-serialized, not concatenated, because values may contain `:` and `"a:b"+"c"` must not collide with `"a"+"b:c"`. Unlike the pre-auth scheme, the hash now **does** hide identity — `profile_id` comes from a verified Bearer token, not a self-declared name — and `_player_key` doubles as a path-safe file name for `app/brain/memory.py`. Both functions take their scope as explicit keyword arguments (not a `ChatRequest`) precisely so `create_response` and `create_situation_response` derive identical keys from identical scope values — a chat turn and a situation event with the same four values land in the same conversation. The `CredentialProtector` is resolved per request (`Depends(get_credential_protector)` for HTTP, `build_credential_protector(settings)` per WS frame), not held by the `CompanionService` singleton — holding it there would make a missing pepper fail app *startup* instead of the one request that needed it.
- All DTOs extend `StrictModel` / set `extra="forbid"` (unknown fields → 400/422, not ignored).
- Application-layer errors are `RuntimeError` subclasses mapped to a uniform `ErrorEnvelope` by `errors_http.py`; that map is shared by the HTTP exception handlers and the WebSocket message loop, so a new error needs one entry, not two. AI failures → 503/504 retryable.
- Per-turn values ride on `CompanionTurn`, an internal type free to change without touching the wire contract. If the brain seems to be missing a value, add a field there rather than passing the whole `ChatRequest` in.
- Configuration is `Settings`, read by `brain/llm.py` only.
- The brain must only emit an action in `turn.allowed_actions`, and `CompanionService._assert_within_allowlist` rejects anything else as `AIServiceInvalidOutput`. **That assert is not re-validating untrusted output** — the brain is in the same process and repository. `graph.py` decides and the boundary asserts; two places with different reasons, and the assert catches a graph regression. Keep it: it protects the game, not the server. A bad LLM call must never fail the request — `render` and the providers fall back to deterministic mock output.
- **`surface` remains a presentation axis and nothing else, for chat and for situations.** It picks a tone (and the wording of "I can't do that"), never what Mako is allowed to do — `allowed_commands` remains the single gate for chat, and situations never gate anything (no action is ever possible). Mobile task issuing is a separate authenticated `/api/v1/tasks` contract; do not add a `surface == mobile` branch to the chat graph or to `CompanionBrain.react`.
- **`game_context` is screened.** `FORBIDDEN_AI_CONTEXT_KEYS` rejects secret-looking keys before they reach a prompt; this is about what must not be sent to an LLM, and is unrelated to authentication.
- **Conversation text never enters the structured log stream** — `_log_step` and the request-context middleware carry step and duration only, so nothing a player says reaches a log collector. This is *not* the same as "the server never stores conversation text": `app/brain/transcript.py` keeps a verbatim per-conversation JSONL on purpose (long-term memory is distilled from it), governed by `TRANSCRIPT_RETENTION_DAYS` and `docs/temporary-scaffolds.md` §2. Two separate rules; the logging one is absolute.
