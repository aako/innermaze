# Agent E — Integrated import/process candidate status

**Prepared:** 2026-10-27; **candidate only, not released.** Branch `integration/agent-e-import-process-v1` based on `0495bf4d116ad6a2b1f35ed25ea9bb8491233e02`.

## Changes delivered

- Agent B `recording_identity.py` and Apple UUID parser copied unchanged from `c9446ccc6bfd7a047576dacbc67d85f60509d685`. The parser remains standalone/tested, not loaded into iOS until a packaging/device review.
- Agent C proposal adapted as **desktop-only** Import & Process modal and targeted Mango Python invocation, not its original patch-builder. Existing 0.1.9 player/navigation/timing logic is unchanged. Candidate `main.js` requires separate Obsidian-device acceptance before any BRAT release.
- New `tools/innermaze_import_process.py`: B planner before vault write, exact UUID/packet reconciliation, verified alias for one unambiguous legacy generic note, same-note reimport and rename protection, now/later/skip Whisper, optional Qwen after a ready transcript, never runs a global worker.
- `tools/innermaze.py` v0.1.4 adds optional Python-call parameters (`identity_hint`, `reuse_cached`, `source_kind`) to the existing importer. All original command defaults and cache-reuse behavior are unchanged. This helper is separate from BRAT.
- Agent D `innermaze_classify.py`, tests and historical code copied from `55ddd864ad9c9b65a8f1a84e607118031908dd52`. Output uses existing append-only `Voice Archive/Classification/<sha256(recording_id)>/runs`, not the forward-looking AI/jobs draft. No review service started, no human categories accepted automatically.
- Added `tests/test_integration_agent_e.py` synthetic B+C+D integration regression cases.
- Copied Agent 0's coordination docs **as proposal references**. Their future iCloud request/response queue is not part of this desktop MVP.

## Validation gate invoked by publisher

- `node --check main.js`
- `node --check tools/apple_voice_memo_uuid.cjs`
- `node tests/test_alignment.cjs`
- `node tests/test_auto_open.cjs`
- `node tests/test_navigation_guard.cjs`
- `node tests/test_recording_rename.cjs`
- `node tests/test_lossless_ab.cjs`
- `node tests/test_optimized_playback.cjs`
- `node tests/test_apple_voice_memo_uuid.cjs`
- `python3 -m unittest discover -s tests -p test_importer.py`
- `python3 -m unittest discover -s tests -p test_recording_identity.py`
- `python3 -m unittest discover -s tests -p test_classification.py`
- `python3 -m unittest discover -s tests -p test_integration_agent_e.py`

The publisher stops without committing/pushing when any test fails. A completed successful run prints its commit SHA and optional remote branch URL. CI and local synthetic tests are **not** real-device acceptance. The publisher itself does not run any user's live vault, Cashew or model.

## Remaining work / risks

- A user-authorized Mango desktop/Cabinet pilot and Obsidian device acceptance are still required; this package never installs BRAT assets.
- iOS Apple UUID parser (`tools/apple_voice_memo_uuid.cjs`) needs safe bundling into shipped single-file `main.js` and actual iPhone tests. No cross-device iCloud job queue exists in this candidate.
- The narrowly permitted legacy alias binding requires exactly one matching anonymous note, verified identical compressed audio, and no competing UUID-owner notes; otherwise `REVIEW_REQUIRED` aborts before import. CAS against concurrent iCloud writers is best-effort, not a distributed transaction.
- Pending classification is deliberately **not scheduled**: selecting "later" records no durable queue. Qwen errors must be reported and leave transcript/older runs unmodified.
- Mango helper paths and the Obsidian plugin are installed/released separately. No tag, merge, worker or live-vault changes authorized.

## Codex Flight Recorder

Confirm working contents with Git and inspect `codex-audit history`, `codex-audit show --last`, and `codex-audit undo --last` before using those recovery controls. For hidden output paths use `~/.codex/bin/codex-audit protect --output <path> -- <command>`; only Git and protected outputs have strong recovery guarantees. Do not presume every file write can be undone.

## Checkpoint

Objective: one safe Mango Dropbox import with optional Whisper then Qwen. Decision: separate targeted helper rather than replacing legacy importer or enabling a worker. Ruled out: C's unchanged patch, hash-only dedup, double AI stores, BRAT release. Exact action: review successful branch commit/tests, then explicitly authorize a limited device/Cabinet pilot; no main merge or release before Agent 0 review.
