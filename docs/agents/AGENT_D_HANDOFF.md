# Agent D — Qwen categorization submission

**Source submission, not integrated into Obsidian.** These are Agent D's original AI classification modules and synthetic tests, archived intact except for making two legacy path defaults home-relative to avoid publicizing a personal absolute path and to reflect Mango's relocated source tree.

## Contents

- `tools/innermaze_classify.py`: single-note, transcription-first classifier. Dry-run by default. `--apply` requires a local LM Studio/Qwen model and writes an append-only run JSON, not human tags or editable transcript content.
- `tools/legacy/voice_memo_describe_v2.1.0.py`: preserved classifier algorithm and prompt (historical compatibility reference; default paths adapted).
- `tools/legacy/voice_memo_review_v1.2.0.py`: existing standalone human reviewer source, supplied as historical reference only. Do not run alongside an Innermaze importer.
- `tests/test_classification.py`: nine synthetic tests with stub model calls.
- `examples/Dreams.base`: example Obsidian Base requiring human-approved categories, not automatically installed.

## Integration constraints

- `tools/innermaze_classify.py` writes immutable per-run AI outputs into `Voice Archive/Classification/<sha256(recording_id)>/runs/` by design. Agent E must reconcile this with the earlier proposed `Voice Archive/AI/...` path; do not create two active classification stores.
- Human decisions, accepted titles, tags, categories and transcript edits remain separate from AI suggestions. No automatic category acceptance.
- Agent C may invoke classification only *after* verified transcription; Qwen execution is desktop Mango-only, and is not in the current Import & Process pilot.
- Actual Mango LM Studio/Qwen, iCloud and Obsidian review integration have not been tested as part of this submission.

## Tests

`python3 -m unittest discover -v -s tests -p test_classification.py`

*Provenance:* Agent D's original detailed handoff remains available in ChatGPT Library. No BRAT release or live-vault changes were made.
