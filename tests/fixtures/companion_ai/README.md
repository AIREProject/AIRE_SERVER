# Companion AI evaluation fixtures

These files are synthetic-only inputs for CAI-P0-T01/T02. A fixture ID uses
`p0.<domain>.<case>.<number>`, and the loader executes files in filename order.

- `request` contains one evaluated turn and optional setup turns for the same conversation.
- `script.responses` is the exact ordered `LLMProvider` call sequence. Missing, extra, or reordered
  calls fail the test.
- `expect` describes the target semantic contract. A mismatch is allowed only when the field appears
  in `known_gaps` with its owning follow-up Task.
- Normal-interaction `query_mode` values are observed from request-scoped response provenance.
  Fact provenance gaps and provider-fault modes remain explicitly registered until their owning
  production seams are implemented.
- Text, IDs, and conversations must remain synthetic. Do not add transcripts, credentials, prompts,
  tokens, actor paths, or production identifiers.

`db_side_effect: "none"` means that the evaluated request does not add or change an episodic memory or
offline task. Authentication and pre-seeded save-slot rows are fixture setup and are not evaluated.
