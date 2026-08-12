# tests/

```powershell
uv run pytest                                   # full suite
uv run pytest tests/test_companion_chat_api.py  # single file
```

- `pytest-asyncio` runs in `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed.
- Name files `test_<area>.py`, tests `test_<behavior>`. Add a regression test with every behavior change.
- **`conftest.py` blocks `.env` and the environment so `Settings` serves code defaults.** State every value an assertion checks rather than leaning on a default: `Settings` sets `extra="ignore"` and silently swallows a mistyped field name, so without this isolation a typo'd kwarg falls back to a `.env` value and the test passes for the wrong reason.
- **Build settings with `conftest.make_settings(...)`, never `Settings(...)` directly.** It is the other half of that isolation — the fixture stops outside values leaking in, and `make_settings` raises `TypeError` on a field name `extra="ignore"` would have swallowed, so a passed value is guaranteed to have actually applied. `tests/test_settings_isolation.py` holds it to that.
- Live OpenAI tests are opt-in via the `live_llm` marker (`RUN_LIVE_LLM=1` + key); the default mock provider needs no network.
- **`long_term_memory_dir` and `transcript_dir` are the two settings the fixture redirects rather than clears** — both point at `tmp_path`, because their code defaults (`data/memories`, `data/transcripts`) are real directories holding real memories and real conversation text once anyone has run the server. Tests that write either should still take a `tmp_path` explicitly. The mock provider returns an empty result for all three memory methods, so nothing is distilled unless a test supplies a stub provider.
- **Tests of the distillation pipeline never sleep.** Every timing decision lives in `CompanionBrain._drain(now=…)`; call it directly with a clock (`test_long_term_memory.later(seconds)`) instead of waiting for the loop. Build that clock from `datetime.now(UTC)`, not from a fixed literal — the queue's `last_turn_at` is stamped at the real moment the turn happened, and mixing the two flips the sign of the idle check.
