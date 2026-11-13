# Innermaze — ACTIVE development state

**Active branch:** `integration/agent-e-pilot-hardening-v1`
**Last verified source checkpoint before live deployment:** `69b226aa2e710eb3ee25277ebf1a986ccaf37fe5`. Check GitHub branch HEAD for the latest coordination/documentation commit.
**Stable main:** `0495bf4d116ad6a2b1f35ed25ea9bb8491233e02` (plugin 0.1.9).
**Milestone:** Real Mango Cabinet import pilot. Code gate: 15/15 passed. Live GUI/audio/Whisper/Qwen/iPhone: awaiting user results. BRAT bypassed for development.

## Responsibilities

- **A / Agent 0:** architecture and coordination; major interface decisions.
- **B:** Apple Voice Memo UUID/packet identity, deduplication, archive provenance.
- **C:** Obsidian desktop/iPhone UI, Import & Process, player, navigation.
- **D:** Mango local Qwen classification and human-review schema.
- **E (integration owner):** assemble candidate, deploy directly to Mango Cabinet, run smoke tests, resolve conflicts, publish checks.

## Current interfaces / invariants

- Candidate plugin: `main.js`, `manifest.json`, `styles.css` copied to Cabinet `.obsidian/plugins/innermaze/`; existing `data.json` settings preserved except `importProcessHelperPath`.
- Desktop plugin invokes absolute worktree `tools/innermaze_import_process.py` with `--file`, `--source-filename`, `--transcribe now|later|skip`, `--classify after|later|skip`, `--vault`, `--apply`. No global worker or Cashew action.
- Dropbox `Voice Memos Full DB` and `Just Press Record Full DB`: **permanent, independent, read-only archival source**. VoiceAudioBackup manifest: SQLite `mode=ro`. Import outputs only to Obsidian `Voice Archive/` and local scratch.
- Recording ID: verified Apple UUID where available, compressed audio identity otherwise; repeated import must reuse matching note. Original filename and known native Dropbox provenance preserved; staged copy provenance arbitrary-file.
- Whisper: targeted Mango MLX run, word timing sidecar. Qwen: append-only `Voice Archive/Classification/<sha256(recording_id)>/runs/`; AI suggestions never overwrite human tags/categories.
- iOS UUID parser not yet bundled; mobile playback should be checked after Mango import. No per-import BRAT release, new queue, or manifest version bump.

## Current blockers and next steps

1. **User:** preflight + deploy candidate to actual Cabinet, reload Obsidian; import selected original Voice Memo via GUI (`Later`, `Skip`), play it, reimport, and verify SHA.
2. **E:** diagnose first real import result; make ordinary fixes on this active branch and publish with authenticated Mango Git.
3. **User / E:** targeted Whisper; actual Qwen through LM Studio; iPhone iCloud/playback acceptance.
4. **A / B / C / D:** read this file and the current active code before editing; return code/tests/short check-in for E's integration.

**No real import, model run, or iPhone acceptance has been claimed at creation of this file.**

## Live Cabinet checkpoint — 2026-10-27

- Cabinet plugin deployed from active candidate; original Dropbox Voice Memo source SHA `dd31001ec7b357939a0ed1143251f1acec440777a55e57fc9dd821a5dae2e50c` verified unchanged **before the first GUI attempt**.
- First real GUI import (`Later` transcription, `Skip` Qwen) reached Python identity inspection but failed before import: Obsidian child process did not inherit Homebrew/MacPorts PATH, so `ffprobe` could not be located (`FileNotFoundError`). No audio import or model run confirmed.
- Agent E fixed only the desktop child-process PATH and added a targeted launcher regression; original source files and independent archiver remain untouched.
- Next: reinstall updated `main.js` directly into Cabinet; reload plugin; retry exact same original via UI; confirm playable note, dedup, original source SHA, then targeted Whisper and Qwen. No BRAT or synthetic-vault test.

## 2026-11-06 Mobile imports and Combine

- **Branch:** `integration/agent-e-pilot-hardening-v1`.
- **Source implementation:** `3e7a498fa707d1c140a557542a6824470810ac95` (subsequent status commit may advance HEAD).
- **Validation:** 21/21 regression commands passed locally on Mango, including Agent B's original UUID/iPhone/Dropbox tests and FFmpeg Combine + desktop command tests.
- **Mobile:** Agent B source-only ZIP integrated *unchanged* into single-file iPhone import. New notes bind embedded Apple UUID; exact reimports reuse notes, ambiguous remuxes block; Mango packet-aware identity remains authoritative for later Dropbox reconciliation.
- **Combine:** Mango-only `Combine recordings` Obsidian command; ordered 2–10 distinct existing notes, trim start/end (0–30s), verified listening hashes, AAC output, deterministic `combined-<recipe_sha>` identity, read-only source notes/audio, standalone JSON provenance, pending full-duration Whisper.
- **Cabinet plugin:** Directly installed to real Cabinet by this publisher; reload Obsidian on Mango and iPhone. iOS runtime/device behavior is **not yet verified**.
- **Source archives:** permanent Dropbox Voice Memos and Just Press Record roots never written; VoiceAudioBackup SQLite is not opened for writing; no worker, main merge, manifest bump or BRAT release.
- **Agent roles:** B owns immutable UUID/reconciliation; C owns future picker/share-sheet UI; E owns this current mobile/Combine integration and real-device debugging; D human-review UI remains deferred; Agent 0 coordinates architectural issues.
- **Blockers / next test:** in actual iPhone Obsidian, use Files to import two interrupted Voice Memos into Cabinet, allow iCloud sync; in Mango Obsidian, use `Combine recordings (Mango)`, confirm ordered audio and one combined note, then `Transcribe current recording on Mango`; verify originals and packet-safe Dropbox reconciliation. Existing recordings and human edits must remain unchanged. Share-sheet direct handoff is not implemented yet; Files picker is supported.

## 2026-11-12 iPhone filenames and Aiko SRT import

- **Implementation commit:** `0a16ff8e25768c4a640f4261594162655e5515fb` on `integration/agent-e-pilot-hardening-v1`.
- **Validation:** 24/24 existing + Aiko/filename regression commands passed on Mango before commit; new module tests were also run offline during preparation.
- **Mobile titles:** New notes use original filename stems with collision-safe suffixes; `recording_id` and `source_title` remain immutable. Reimports/renamed existing notes reuse their original paths; player header reads `display_title` (without extension).
- **Aiko:** `Import Aiko SRT for current recording (iPhone/Mango)` writes `voice-archive-sync-v1` segment blocks, `words: []`, source `aiko`, readable paragraphs and ready status. Audio duration is checked; missing iCloud audio blocks import. No Mango Whisper required; targeted Mango Whisper remains available.
- **Overwrite safety:** Prior human text, ready status or sidecar requires explicit choice. Before replacement previous note and sync are preserved as files under `Voice Archive/Transcription Backups`. Source recordings, Dropbox archives and Cashew manifest are never written by this implementation.
- **Cabinet deployment:** Direct main.js installation completed, prior plugin backed up. Existing manifest, styles and data.json remain unchanged. Real-device Aiko SRT import and note-title acceptance are still PENDING.
- **Next test:** On iPhone import new Voice Memo, confirm human note filename/player header and stable UUID on reimport. In Aiko export SRT, return to Obsidian with that recording open, invoke import command, play timed paragraphs, then test cancellation and optional backed-up replacement. Reconfirm Mango Combine/Whisper and iCloud sync. Agent C owns later share-sheet UX; Agent D's review UI remains deferred.

## 2026-11-12 Aiko SRT final-caption timing compatibility

- **Implementation commit:** `89ef99d2388ec075dfcfde92d9ad4766351d24dc` on `integration/agent-e-pilot-hardening-v1`; stable `main` unchanged.
- **Trigger:** Real iPhone Aiko SRT import rejected final caption at `duration + 0.25s` with a generic error.
- **Fix:** Accept positive-interval SRT captions beginning within the playable audio and ending up to 2.0s beyond it; clamp end to exact audio duration. Larger overruns, captions starting outside the audio, invalid intervals or wrong SRT still block with caption number and measured timestamps. No word timestamps invented.
- **Validation:** 25/25 commands passed on Mango before committing, including standalone/bundled-parser parity and explicit tail/error regressions.
- **Cabinet deployment:** Direct development main.js installed with backup. No manifest change, BRAT, worker or Dropbox/SQLite writes.
- **Device acceptance:** Actual retry with the same Aiko SRT is still pending. If the new error reports more than 2 seconds of mismatch, inspect the SRT against the correct recording instead of weakening the guard.


## 2026-11-12 Aiko first-import Sync repair

- **Implementation commit:** `63d03b7acf960c8ea0549fdded0a02976d9b1786` on `integration/agent-e-pilot-hardening-v1`.
- **Mango tests:** 26/26 development regressions passed, including a strict mock vault showing the original first-import failure and its repair.
- **Root cause:** A fresh mobile import can lack `Voice Archive/Sync`; Aiko previously attempted to create its timing sidecar without ensuring the parent folder exists.
- **Repair:** Before sidecar write, ensure Sync folder; after note+sidecar writes, verify persisted ready/Aiko state and refresh the open player explicitly. Never rewrite audio or UUID.
- **Deployment:** Direct development main.js installed in Cabinet; reload iPhone plugin; existing Cabinet settings, original recordings and Dropbox archives untouched.
- **Acceptance:** Actual retry of Aiko SRT on the previously pending iPhone note is still needed. Expected result: `Aiko: imported N timed captions`, readable timed paragraphs, stable recording identity and intact original audio. Mango Whisper remains optional.
