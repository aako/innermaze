# Agent B — Recording identity and archive reconciliation

**Source submission, not integrated.** Agent B developed a read-only Python identity/reconciliation planner, a bounded JavaScript MP4 Apple UUID parser, and synthetic regression tests. Agent 0 independently checked the logic against a real two-file Voice Memo comparison but intentionally omitted all private recording names, raw hashes and actual UUID values from this public handoff.

## Contents

- `tools/recording_identity.py`: `inspect_recording`, `scan_vault`, `reconcile_recording`, `plan_archive_file`, and `iter_archive_paths`; requires `ffmpeg` and `ffprobe` on Mango.
- `tools/apple_voice_memo_uuid.cjs`: pure JavaScript bounded parser for the Apple `voice-memo-uuid` MP4 metadata tag; not yet bundled into the Obsidian plugin.
- `tests/test_recording_identity.py` and `tests/test_apple_voice_memo_uuid.cjs`: synthetic-only tests with *fabricated* UUIDs.

## Invariants

- Recording UUID (`vm-UUID`) identifies a logical Voice Memo; full-file SHA-256 identifies source bytes; compressed packet SHA-256 helps identify equivalent AAC in different MP4 containers. This is not a PCM/perceptual identity.
- Existing `recording_id`, Obsidian note path, editable Markdown and sync data remain unchanged. A verified archive UUID can be attached as an alias to an older hash-named note.
- Different Apple UUIDs remain distinct logical recordings even if their audio matches.
- Ambiguous identity, altered audio or a missing iCloud playback file requires explicit review. Do not silently merge or overwrite.
- Inspect Dropbox and Cabinet read-only. No archive writer, launchd service, direct publication, or bulk import is included.

## Integration handoff

Agent C must consume B's reconciliation planner *before creating an import note*, and must integrate the JS UUID reader before iOS import identity selection. Agent E owns cross-agent integration and release review. No changes to `main.js` or `tools/innermaze.py` are made by this submission. Real Mac/iOS integration tests and live iCloud attachment checks remain outstanding.

## Test commands

`python3 -m unittest discover -v -s tests -p test_recording_identity.py`

`node --check tools/apple_voice_memo_uuid.cjs`

`node tests/test_apple_voice_memo_uuid.cjs`

*Provenance:* Original Agent B handoff preserved in ChatGPT Library. Personal sample details and the original example UUID are intentionally omitted from this public repository.
