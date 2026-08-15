# Companion AI evaluation fixtures

These files are synthetic-only inputs for CAI-P0-T01/T02. A fixture ID uses
`p0.<domain>.<case>.<number>`, and the loader executes files in filename order.

- `request` contains one evaluated turn and optional setup turns for the same conversation.
- `script.responses` is the exact ordered `LLMProvider` call sequence. Missing, extra, or reordered
  calls fail the test.
- `expect` describes the target semantic contract. A mismatch is allowed only when the field appears
  in `known_gaps` with its owning follow-up Task.
- `query_mode`, fact provenance, and production fallback reasons remain `not_observed` until their
  production seams are implemented. The target labels stay in the fixture for later reruns.
- Text, IDs, and conversations must remain synthetic. Do not add transcripts, credentials, prompts,
  tokens, actor paths, or production identifiers.

`db_side_effect: "none"` means that the evaluated request does not add or change an episodic memory or
offline task. Authentication and pre-seeded save-slot rows are fixture setup and are not evaluated.
