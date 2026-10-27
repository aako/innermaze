# Innermaze — Agent 0 coordination hub

**Prepared:** 2026-10-27. **Status:** coordination directive; not a release. **Canonical baseline at audit:** `aako/innermaze` `main` `0495bf4d116ad6a2b1f35ed25ea9bb8491233e02`, plugin `0.1.9`, separate Mango importer v`0.1.3`. Refresh SHA at each checkout.

## Mission and authority

Agent 0 (ChatGPT coordinating chat) reviews cross-agent handoffs, assigns boundaries, resolves disagreements and recommends release gates. Agent E owns implementation of shared integration tests and merge/release preparation. Agent A owns player regressions. All development remains in independent ChatGPT chats (not coordinated Codex sessions); nobody assumes one chat can read another. **GitHub content is the common source of truth**, but ChatGPT GitHub access is currently READ ONLY in practice: branches/issues/commits created through the connector returned HTTP 403. The *user's authenticated Mango Git client* publishes vetted source and handoffs. Never claim something is on GitHub until the branch/commit can be fetched.

## Workstream status at this audit

| Agent | Evidence | GitHub status | Next assignment |
|---|---|---|---|
| A — Player | 0.1.9 actual-device acceptance, release workflow passed | on main | Keep player frozen; provide only regression review |
| B — Identity/archive | Source-only `recording_identity.py`, Apple UUID JS parser, Python/JS synthetic tests. Agent 0 additionally verified both parsers on a supplied real matched Voice Memo pair: different full-file hashes, matching compressed-audio fingerprints and UUID | NOT COMMITTED | Reconcile current importer with identity planner; privacy-clean handoff; real-world edge cases |
| C — Import/Process | Local worktree `feature/import-process-gui-pilot`, prototype `--process-note`, 17 Python tests and local MLX Whisper pilot reported; classifier controls are placeholders | NOT PUSHED | Import GUI on Mango; use B planner before any write; integrate D CLI as optional separate process; no release yet |
| D — Qwen/review | Source-only classifier and legacy code, nine mocked classification tests, immutable run receipts | NOT COMMITTED | Fix and document schema mapping, verify local Qwen service on Mango, agree human-review ownership |
| E — Integration | Proposed shared contract and 24-case test *plan*; no new matrix cases executed | NOT COMMITTED | Reconcile contract with implemented B/C/D before merging, add actual cross-agent tests |

Artifacts from B/C/D/E currently exist in separate ChatGPT Library attachments/downloads or local Mango worktree. They are NOT published merely because the agent wrote a handoff.

## Settled compatibility rules

1. **Freeze the functioning 0.1.9 player.** No changes to player timing, optimized audio, navigation or BRAT release without explicit targeted review. `main.js` may require narrowly scoped new import controls, but Agent E must check against frozen player tests and device behavior.
2. **Cashew owns archival originals and its SQLite manifest.** Archive is read-only to Innermaze. Mango imports/transcribes; iCloud Cabinet stores one safe playback asset when bytes match, mutable Markdown and external timing JSON. Archives are always rebuildable independently of Innermaze.
3. **Recording identity differs from file identity.** Existing `recording_id` and note paths remain immutable. Verified Apple UUID may be `vm-UUID` for a new recording or `archive_recording_id` alias for a preexisting `audio-SHA` note. Never silently merge distinct Apple UUIDs. B's AAC packet fingerprint is evidence for container-remux equivalence, not authority to merge everything by hash. Ambiguities require review.
4. Existing vault paths, `voice_archive`, `voice_audio`, `voice_sync`, and `voice-archive-sync-v1` remain valid; root-level `Voice Archive/*.md` and legacy `Voice Archive/Notes/*.md` must both work. Never overwrite human transcript edits.
5. **MVP runs from Mango desktop Obsidian:** Import & Process controls can launch the dedicated local Mango helper with explicit validated inputs. This is expedient for first usable importer. **A durable iCloud job request/response queue is a future cross-device phase**, necessary for iPhone-created jobs while Mango is offline. Agent E's queue spec is a forward-looking draft, NOT a prerequisite for the Mango-only MVP, and C's prototype is not a completed cross-device queue.
6. **Whisper first, optional Qwen second.** Reuse historical VoiceMemoSaver word timing. D's actual append-only classification storage and receipt are the MVP source interface; E should map it to the proposed `innermaze-job-v1` later rather than force D to rewrite a working pilot. Distinguish AI suggestions from explicit human-approved Obsidian properties/tags. Preserve older review files; do not automatically accept suggestions.
7. The helper installed by LLMRunner and plugin distributed via BRAT are separately released. No worker enablement or bulk processing as a side-effect of source publication. No private recordings/transcripts or personally identifying filenames in this public repository.

## Decision log: conflicts to resolve

- **B -> C critical:** Original iPhone share may create `audio-SHA` and archive may later prove `vm-UUID` with same compressed stream but different whole-file hash. Must bind verified alias to the existing note, preserve edits/links and avoid a duplicate. B's iOS UUID parser isn't bundled into `main.js` yet; C must integrate it without changing player behavior. A legacy generic note without evidence must enter review, not silently merge.
- **C vs E:** C's targeted `/usr/bin/python3 ... --process-note` runs only on Mango desktop. E's iCloud job JSON queue is the next phase. Document both explicitly, test the first, then design cross-device durability; do not claim the queue exists.
- **D vs E:** D writes `Voice Archive/Classification/<sha256(recording_id)>/runs/*.json`, whereas E's document suggests `Voice Archive/AI/<recording_id>/<job_id>.json`; also review-status enums vary. **Do not implement both.** E must adopt D's existing pilot as the current v1 implementation or propose a versioned migration accepted by D and Agent 0. No silent file moves.
- **Public repository privacy:** B's originally generated patch/handoff includes a user-supplied personal recording filename; redact it before any push. D's vendored legacy reviewer includes machine-specific Dropbox paths; review and parameterize them before including in public code. No private prompts, source filenames, audio, transcripts, or logs in commits.
- **Versioning:** C's local pilot proposes manifest `0.1.10` but it has not been integrated or released. Tag/version changes reserved for an E-integrated, user-approved device-tested plugin release.

## Read-first and checkpoint protocol for independent ChatGPT agents

On each new session, inspect GitHub `main` SHA, `docs/agents/AGENT_0_COORDINATION.md` on the published docs branch until merged, `docs/INTEGRATION_CONTRACT.md` (proposed, not all implemented), latest available agent branch/handoff and current Library source. Verify every proposed change against actual Git contents. Assume GitHub write via ChatGPT connector is blocked (403); do not waste cycles retrying. Work product must be a *complete file, patch and test transcript* with SHA-256 checksums and exact base commit, plus an independent branch/worktree if local access actually exists. Label all unpublished artifacts NOT ON GITHUB.

CHECKOUT: agent, base SHA, owned paths, exact planned change, acceptance tests, dependencies, expected effect on shared files.
CHECKIN: head SHA if committed, artifact names/checksums otherwise, tests performed and not performed, actual on-device results vs simulations, remaining risks, exact next action. Keep an own `docs/agents/AGENT_{LETTER}_STATUS.md` ready for a human-controlled publication. Never edit other agents' handoffs as a substitute for coordination. User's local Git can push each feature branch; E gates merges, Agent 0 advises.

## Immediate milestones

1. Publish this hub and E's design documents as a **docs-only branch**, without modifying 0.1.9 or activating processing.
2. Agent B privacy-cleans identity package and confirms integration contract with C and E. Agent C integrates B's `plan_archive_file`/JS UUID resolver into a pilot importer in its isolated worktree, explicitly refusing `review_required`.
3. Agent D delivers a clear stable JSON receipt + classification-run schema and verifies the local Qwen endpoint on Mango. Agent E updates the proposed contract to represent B/C/D real interfaces and writes failing integration regressions before wiring them together.
4. After review, publish independent source feature branches (NOT source snapshots on `main`); integration tests use a synthetic vault. User personally authorizes limited real archive pilot; separate Obsidian/Mango release testing follows.

## Release gate

No merge/release unless: origin-main and baseline verified; synthetic import and ID collisions tested; backups preserved; tests pass; source/vault privacy audit passes; no altered audio/Markdown/sidecar human edits; model failures leave transcript and human metadata untouched; targeted Mac and iOS device acceptance; BRAT and Mango helper updates separately reviewed. Do not overclaim CI as real-device testing.
