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

