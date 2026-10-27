# Innermaze — integration test matrix (Agent E, 2026-10-27)

**Baseline:** plugin 0.1.9, Mango importer v0.1.3, `main` `0495bf4`. **Matrix is a plan, not an assertion that new integration tests pass.** All automated cases use a temporary synthetic vault, synthetic audio, fake manifests/indexes and mocked model runners; real-device cases use deliberate, reversible fixtures, never existing Cabinet recordings.

| ID | Scenario and injection | Expected invariant | Owner / gate |
|---|---|---|---|
| I01 | Same Apple UUID arrives first via iPhone and later Dropbox with identical bytes | One logical note; verified external ID attached to existing `recording_id`; no body, sidecar or playback duplication | B+C / automated |
| I02 | Two Apple UUIDs produce bit-identical audio | **Two notes**, one verified playback asset; no UUID collision or incorrect merge | B / automated (existing importer test covers archive path) |
| I03 | Unreadable/missing SQLite archive manifest | Preserve generic ID and note; no invented UUID; retriable reconciliation | B / automated |
| I04 | One audio hash maps to conflicting Whisper JSON or multiple potential UUID owners | Fail closed with ambiguity, no wrong transcript or merge | B+C / automated (existing conflict test partially covers) |
| I05 | Dropbox file renamed and Obsidian note renamed while processing | Identity/sidecar resolved by stable metadata, no replacement of user's title or corrected text | B+C / automated + Mac |
| I06 | Original audio bytes change at same archive path | Changed source version detected; reject overwrite, record diagnostic and require deliberate reconciliation | B / automated |
| I07 | Converted playback hash differs from source hash | Validate both; reuse the correct asset; no second conversion or wrong source attribution | B / automated (existing OGG test partially covers) |
| I08 | iCloud audio file is a placeholder/partial download; sync JSON also incomplete | Mark `blocked`, do not launch Whisper or publish corruption; resume after verified local download | C / automated + Mac/iOS |
| I09 | Mango offline/worker not started while Obsidian enqueues a request | UI remains `queued` / Mango unavailable; request durable; no SSH/iOS model execution | C / simulated + iOS |
| I10 | Duplicate clicks, iPhone+Mac submission, repeated iCloud enumeration | Same idempotency fingerprint yields one run/result; retry uses original `job_id`, no duplicate worker or note | B+C / automated |
| I11 | Mango process killed after sync creation, before Markdown body publication | Resume from validated sidecar; publish at most once, retain edits and correct job phase | C / automated fault injection |
| I12 | Model runner timeout, crash, bad output JSON, or unavailable LM Studio | Bounded `failed` state and actionable diagnostic; original audio, transcript, prior AI and human decisions unchanged | C+D / automated |
| I13 | Existing note body is edited during Whisper operation | Fingerprint/CAS detects change; return `stale`/hold; no lost human text | C / automated (existing worker test partially covers) |
| I14 | Edit transcript between Qwen enqueue and AI output | Mark stale result against old transcript hash; never silently apply suggestions | C+D / automated |
| I15 | Re-run Qwen with another prompt/model/taxonomy | Separate immutable run IDs and provenance; old suggestions, decisions and accepted tags remain | D / automated |
| I16 | Accept some tags, reject others, then import and reclassify again | Native tags/properties reflect only explicit review; manually added tags persist; review history unmodified | D / automated + Mac |
| I17 | Two agents change a shared source file from different base commits | Integration gate detects unexpected diff/conflict; no automatic merge or force-push | E / repo diff review |
| I18 | Release plugin while Mango helper is independently changed | BRAT contains tested root plugin assets only; Mango package source version accounted separately | E / release audit |
| I19 | 0.1.8 navigation regression: open Clippings from recording FileView | Ordinary Markdown renders, old audio stops; no stale player tab | A+E / existing Node tests + Mac |
| I20 | Recording opens on iPhone/iPad, locked/AirPods playback, Edit transcript round-trip | 0.1.9 user-accepted player behavior preserved; new UI does not hijack navigation | A+E / real device, release gate |
| I21 | Cached Whisper with exact source hash vs no cached timing | Cache reuses validated word timestamps without MLX; new processing only when explicitly requested | B+C / automated (existing importer tests partially cover) |
| I22 | Malicious/malformed job JSON, path traversal, unsupported model and mismatched IDs | Reject before execution/write; Mango accesses only approved local roots; no arbitrary shell command | C+E / automated |
| I23 | Repeated short network/iCloud interruptions and reorder of state/result updates | UI trusts validated terminal artifacts, tolerates stale/out-of-order copies and resumes polling | C / simulated Mac/iOS |
| I24 | Legacy notes in `Voice Archive/Notes` and root, old `voice_sync` sidecar | Both located; unchanged sidecar format and note identity, no migration-by-surprise | B+E / existing tests + automated |

## Existing gate — do not replace

From `.github/workflows/release.yml`, run these unchanged against a dedicated branch/worktree after integration:

```text
node --check main.js
node tests/test_alignment.cjs
node tests/test_auto_open.cjs
node tests/test_navigation_guard.cjs
node tests/test_recording_rename.cjs
node tests/test_lossless_ab.cjs
node tests/test_optimized_playback.cjs
python3 -m unittest discover -s tests -p 'test_importer.py'
```

Observed on GitHub: the **tagged 0.1.9 release** at `fa2dd2b0e9c4254653898eb0745a03642b00043d` completed successfully in Actions run `40441577038`; the subsequent source-only `main` commit is `0495bf4`. This does **not** establish that proposed B/C/D integrations or new tests have run. Current release job triggers on version-matched tags; neither documentation nor helper-only work should create a tag. Real-device playback and Obsidian UI acceptance remain separate from automated tests.

## Blocking cross-agent decisions, ordered

1. **B ↔ C:** How to reconcile a generic mobile `audio-<sha>` note when the manifest later yields one UUID **or multiple** UUIDs sharing bytes; do not rename the existing note.
2. **B ↔ C:** Which agent owns the single import-time hash/provenance check, and how a Dropbox arrival reuses an iPhone-created note without duplicate notes.
3. **C ↔ D:** Exact stable transcript fingerprint, publish-time compare-and-swap and treatment of a transcript edited after Qwen enqueue.
4. **C ↔ D:** Versioned `job-v1` queue files and the difference between transcription execution state, AI status and human review status.
5. **D ↔ existing scripts:** Explicit mapping from the archived `voice_memo_describe_v2.1.0.py` JSON schema and `voice_memo_review_v1.2.0.py` decisions to new Obsidian properties without destroying old outputs.
6. **C ↔ release:** Adding an Obsidian GUI needs a BRAT build/device test; Mango transcription logic and LM Studio runs do not ship in BRAT.
7. **E ↔ all:** Repository shared files, ownership and branch base must be negotiated before writing; preserve `main.js` FileView/navigation safety and existing 0.1.9 tests.
