# Agent B — iPhone-to-Dropbox identity consistency

**Status:** source-only contribution ready for Agent E integration; **not published / not installed**.

**Reviewed baseline:** `aako/innermaze`, branch `integration/agent-e-pilot-hardening-v1`, initial commit `8d43681dfa7a736b09f1f6aa6049d50ac520f794`; **rechecked latest head** `95a355ea1e2e19ea2b0e593ecf09194f0a293cfc` on 2026-10-27. Current `main.js` blob `066f23838008d6b82511a38623ae3464365bffc2`. Git comparison from the initial commit shows 1 intervening commit with changes to `docs/agents/ACTIVE.md`, `main.js` and `tests/test_pilot_hardening.cjs`; re-read the mobile function boundaries on the new head and confirmed they remain compatible. The candidate builder preserves the unrelated desktop hardening changes. Reviewed `tools/apple_voice_memo_uuid.cjs`, `tools/recording_identity.py`, `tools/innermaze_import_process.py` and the existing mobile `importPickedAudio()` / `makeNoteAndOpen()` / `importVaultAudio()` implementations. No active integration, GitHub branch, live Cabinet, Dropbox archive, Cashew service, or release was changed.

## Finding

Apple iPhone Share/Finder can preserve the embedded `voice-memo-uuid` while remuxing the MP4 container: file-level SHA-256 differs even when compressed AAC packets remain identical. Current E `main.js` still derives iOS `recording_id` as `audio-${whole_file_sha256}`, and does not invoke the existing Apple UUID parser. E's Mango `recording_identity.py` already knows how to reconcile UUID plus AAC packets; this contribution ensures the new iOS note carries the UUID in the first place.

## Files

- `tools/mobile_recording_identity.cjs`: dependency-free JavaScript with the existing bounded MP4 UUID reader (forked from E's B module), strict existing-note snapshot parser, and pure `planMobileImport()`; safe to inline in BRAT's single `main.js` bundle.
- `tools/mobile_import_methods.cjs`: complete replacement of the original four mobile importer functions plus helpers. Identity planning precedes audio writing, and the plan is rechecked before a note is created. Reuse opens the existing note without touching frontmatter or playback positions.
- `tools/prepare_mobile_candidate.py`: *dry-run by default*, create-only standalone `main.js` stage generator. Refuses source overwrite, existing staged output, diverged E mobile boundaries, or a second application. The generator preserves other E code byte-for-byte.
- `tests/test_mobile_recording_identity.cjs`: 23 synthetic checks for UUID parsing, alias, changed versions, missing UUID, collisions, distinct UUIDs, frontmatter and human edits.
- `tests/test_mobile_import_pipeline.cjs`: mocked Obsidian picker and vault regression checking creation, reimport, rename, edited text, playback position, same-UUID version conflict and different-UUID imports.
- `tests/test_iphone_dropbox_roundtrip.py`: FFmpeg-generated M4As plus actual JavaScript UUID parser and the Python identity planner. Uses the *real* `tools/recording_identity.py` when run in E's worktree; otherwise imports pinned read-only reference `tools/recording_identity_reference.py`.
- `tests/test_candidate_builder.py`: guards and `node --check` for the self-contained candidate builder.

## Agent C / E function contract

```javascript
parseAppleVoiceMemoUuid(arrayBufferOrTypedArray) -> uppercase UUID | null
planMobileImport({fileSha256, appleUuid}, [{path, source}, ...]) -> {
  action: 'create_note' | 'reuse_note' | 'review_required',
  reason, recordingId, notePath?, existingRecordingId?
}
```

For new tagged M4As, write `recording_id: "vm-<APPLE-UUID>"` with original source SHA and verified iCloud playback SHA. **Never change a pre-existing `recording_id` to fit a newly discovered UUID.** Reusing a pre-existing note opens its existing actual path: never rewrite human title/body/frontmatter, sidecar, tags, audio or playback/resume mapping. Distinct Apple UUIDs remain distinct recordings even when compressed audio content is identical. The listening audio asset may still be content-shared by exact SHA-256. A missing/invalid UUID must never be inferred from filename, timestamp or raw audio digest.

Decision rules:

| Mobile candidate | Decision | Why |
| --- | --- | --- |
| UUID unseen; no exact legacy hash note | `create_note` with `vm-UUID` | Enables Mango reconciliation by UUID + packet SHA |
| UUID seen, exact source/playback file SHA | `reuse_note` | Proven exact copy, keeps renamed path and resume position |
| UUID seen, different whole-file SHA | `review_required` | No FFmpeg in iOS; Mango must compare AAC packet identity, not guess |
| UUID unseen, only hash-only legacy match | `review_required` | Don't silently claim ownership or change immutable ID |
| UUID unseen, same file SHA belongs to a different *verified* UUID | `create_note` | Shared audio does not imply shared recording |
| No UUID, exact one anonymous `audio-SHA` note | `reuse_note` | Byte-exact anonymous file reimport |
| No UUID, potentially existing UUID-bound audio | `review_required` | Don't invent a second anonymous note |
| Conflicting aliases/duplicate UUIDs/duplicate IDs | `review_required` or throw | Fail closed |

The mobile pipeline first plans, then writes an audio asset only for `create_note`, then rescans before creating a new note. A pre-existing hash-named asset is checked against its claimed SHA before reuse. Reuse never writes an audio copy. Progress continues through the existing six-step mobile modal, and errors appear in `Notice`.

## Exact staging procedure for Agent E

1. Confirm the target worktree is on `integration/agent-e-pilot-hardening-v1` and inspect the current commit and clean/known changes. **Do not apply anything to `main` or the active checkout automatically**. Do not silently discard any simultaneous edits from Agent C.
2. Unzip this contribution into a *separate staging directory* containing `tools/` and `tests/`, e.g. `~/Code/.staging/Innermaze/agent-b-mobile-source`. Keep `main.js`, `manifest.json`, and BRAT installation paths untouched.
3. From the contribution directory, run the tests:

   ```fish
   node tests/test_mobile_recording_identity.cjs
   node tests/test_mobile_import_pipeline.cjs
   python3 tests/test_iphone_dropbox_roundtrip.py
   python3 tests/test_candidate_builder.py
   ```

4. In Agent E's worktree, run the candidate generator, using the **exact current E worktree** `main.js` path (not `~/Code/Innermaze/main.js` if that is `main`) and a **new** staged output. Dry run first:

   ```fish
   python3 /path/to/agent-b-mobile-source/tools/prepare_mobile_candidate.py --source "$PWD/main.js" --output "$HOME/Code/.staging/Innermaze/agent-b-mobile-candidate/main.js"
   ```

   Then create only the staged candidate:

   ```fish
   python3 /path/to/agent-b-mobile-source/tools/prepare_mobile_candidate.py --source "$PWD/main.js" --output "$HOME/Code/.staging/Innermaze/agent-b-mobile-candidate/main.js" --apply
   node --check "$HOME/Code/.staging/Innermaze/agent-b-mobile-candidate/main.js"
   git diff --no-index -- main.js "$HOME/Code/.staging/Innermaze/agent-b-mobile-candidate/main.js"
   ```

5. **Agent E** reviews that diff and incorporates the mobile-block changes into the active integration worktree under the existing Codex/Git audit safeguards; keep all other integration code and the B/C/D interfaces. Agent C owns UI/knobs; do not independently change C's desktop `innermazeImportProcess` flow or transcription orchestration. E runs all existing JavaScript/Python checks plus this new suite; no tag, BRAT release, or `main` merge without real-device validation.
6. On iPhone: import a directly shared Apple Voice Memo, verify `recording_id` is `vm-UUID`, rename/edit transcript, set a playback position, import same file again, and verify exactly one note and unchanged content/state. On Mango, inspect the same recording from Dropbox with `identity.plan_archive_file(archive_path, cabinet_vault)` and verify `reuse_note`, original note path and immutable ID. With an MP4 remux the file SHA may differ; the AAC packet digest must match. Test a changed-audio/UUID conflict and two different UUIDs with identical audio. Test iCloud audio not yet downloaded and ordinary non-Voice-Memo M4A.

## Known limitations / non-goals

- iOS can verify byte-exact whole files but **does not demux and hash AAC packets** in this contribution. A second iOS remux of the same UUID with different MP4 bytes pauses for Mango packet verification. This is intentional safety; no silent merge or new note on that conflict.
- Legacy anonymous hash-only notes may have been created before metadata parsing was wired in. If the new iOS candidate and legacy note have different container SHA values, mobile-only reconciliation cannot confidently equate them. Agent E's existing Mango bridge reconciles unambiguous AAC packet matches and attaches `archive_recording_id` without modifying the original `recording_id`.
- M4As genuinely lacking a UUID are still content-addressed; without embedded identity there is no trustworthy general way to prove remuxed audio belongs to the same recording on iPhone alone. Do not claim perfect deduplication for UUID-less audio.
- All tests use synthetic M4A fixtures or in-memory Obsidian mocks. No actual iPhone Voice Memo file or Obsidian iOS binary was available here for hardware acceptance. It is not a BRAT release.
- The staged candidate is single-file BRAT-compatible because the parser is inlined. **Do not** insert `require('./tools/mobile_recording_identity.cjs')` into shipped `main.js`; BRAT currently publishes only `main.js`, `manifest.json`, and `styles.css`.

## Agent B status checkpoint

Objective: iPhone UUID import and later Dropbox reconciliation without duplicate notes. Decision: UUID-first recording ID; exact-file proof on mobile; AAC packet proof on Mango; no unsafe notes or source writes. Rejected: SHA-only recording identity, filename matching, automatic merging of changed audio, rewriting renamed notes or introducing FFmpeg into iOS. Tests: 23 JavaScript core checks, one mocked mobile picker/vault integration, four FFmpeg cross-language round-trip tests, four candidate builder tests; all passed in isolated Linux runtime. Risks: actual Obsidian iOS and original sample need real-device verification; concurrent iCloud sync can leave an orphaned listening asset if a separate writer races after the pre-write check (never a duplicate note). **Exact next action (Agent E):** stage the generated standalone `main.js`, review diff, run full test suite, then perform the real iPhone-to-Dropbox pilot. No active branch or archive mutation was performed by Agent B.
