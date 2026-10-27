# Innermaze — Agent E integration contract (proposed v0.1)

**2026-10-27 | Baseline:** `aako/innermaze` `main` at `0495bf4d116ad6a2b1f35ed25ea9bb8491233e02`; plugin `0.1.9`; standalone Mango importer `tools/innermaze.py` v`0.1.3`. **Status:** proposed interfaces for Agents B/C/D, **not implemented or approved for release**. Agents must agree to the schema before modifying shared code.

## Invariants and owners

- **A / player is frozen.** Preserve `main.js` FileView behavior, `voice_archive: true`, `voice_audio`, `voice_sync`, `recording_id`, `voice-archive-sync-v1`, audio timing, and actual-device 0.1.9 playback. Do not edit player internals to implement import, jobs, or classification.
- **B owns** source identity, archive reconciliation, verified playback asset reuse and import provenance. **C owns** Obsidian Import & Process controls, request writer, Mango queue runner and transcription. **D owns** model descriptors, taxonomy versions, review decisions and accepted Obsidian tags/properties. **E owns** shared documentation, cross-component fixture contracts, integration tests and release gate. Each agent edits only owned files; coordinate a shared-file change before editing it.
- Never modify Cashew's archive, its backup service/SQLite manifest, live Cabinet files in tests, or original audio. Never start the disabled Mango worker as a side effect of installation/testing. Existing note titles and paths are mutable; data identities are not.
- Backward compatibility is mandatory: existing notes may be in `Voice Archive/` or `Voice Archive/Notes/`; existing word timing is `Voice Archive/Sync/*.sync.json`; existing `.md` bodies are user-editable; Obsidian BRAT does not update the separately packaged `~/Code/LLMRunner/bin/innermaze` helper.

## Field and identity contract

| Interface | Rule / authority |
|---|---|
| `recording_id` | **Immutable note identity**, unique among marked `voice_archive` notes; never derived from current filename, changed on rename, or silently replaced by an Apple UUID. Existing `vm-<Apple UUID>`, `audio-<source SHA-256>` and `jpr-...` values remain valid. New Apple-backed notes use verified `vm-<UUID>` when unambiguous; otherwise preserve hash-based temporary identity pending reconciliation. |
| `archive_recording_id` | Optional verified external logical identity, e.g. `vm-<UUID>`, attached *without replacing* an existing mobile note's `recording_id`. A missing/unreadable Cashew manifest is **unknown**, not permission to invent an ID. Never let one hash automatically merge two distinct Apple UUIDs. |
| `source_sha256` | Full 64-character lowercase SHA-256 of **original source bytes**. Required for newly imported notes; stable for that source version. Renaming a memo does not alter it. Different source bytes are a different audio version and require explicit reconciliation, not replacement. |
| `playback_sha256` | Full 64-character SHA-256 of **actual vault playback bytes**, which may differ after conversion. Validate before reuse. `voice_audio` is the vault-relative playback path, *not* a recording identifier. Reuse a verified audio file where possible; permit multiple logically distinct notes to refer to the same bytes. |
| `voice_sync` | Existing vault-relative `voice-archive-sync-v1` sidecar path; verify its `recording_id` equals the note before playback/publishing. Preserve prior file and word timestamps on retries. No breaking schema changes without a versioned reader and Agent A review. |
| Note location/title | `Voice Archive/<human title>.md` or legacy `Voice Archive/Notes/*.md`; user can rename/move via existing managed Obsidian operations. Locate by verified note metadata, **never path, stem, or title alone**. Reject two notes with the same `recording_id` and mismatched content. |
| `transcript_sha256` | Version fingerprint: SHA-256 of the currently visible Markdown **body** (exclude YAML; UTF-8, CRLF normalized to LF, otherwise preserve exact characters). Compute at enqueue **and again before publishing**. Do not hash whole note frontmatter, which processing may update. A changed body invalidates queued AI work; it must not overwrite a human edit. |
| `recording_source` / provenance | Retain current `recording_source` for compatibility. Append source observations in a separate, versioned provenance log keyed to `recording_id`/source hash: `source_kind` (`iphone-share`, `dropbox-archive`, `arbitrary-file`), observed-at UTC, source/Whisper index reference, manifest UUID only if attested, source/playback hashes, tool/model versions. Relative references where possible; never assert an unverified UUID or copy confidential paths into AI prompts. |

**Critical ambiguity:** Apple's logical UUID is stronger evidence of *recording identity*, while bytes identify reusable *audio*. A generic `audio-<sha>` note created on iPhone may later correspond to a UUID from the Dropbox archive. Attach the verified external ID; retain the note's original identity/path/links. If multiple UUIDs plausibly claim a single generic note, **block for human reconciliation** rather than merge or rewrite it. B must document the exact policy before shipping.

## Job request/response protocol (proposed, not present in 0.1.9)

Obsidian writes an immutable request as `Voice Archive/Jobs/<job_id>.request.json` through its vault API. **Only Mango** reads the iCloud-synced request and runs local Whisper/Qwen; no Obsidian/iOS shell, SSH, model API, or direct Cashew writes. Mango publishes `Voice Archive/Jobs/<job_id>.state.json` and immutable `<job_id>.result.json` after verified work. One producer writes each artifact; stage locally and publish atomically on Mango, and on both ends treat iCloud placeholders or partially synchronized files as unavailable until readable/validated. Paths in requests are vault-relative and resolved within a configured vault root; do not execute user-supplied commands or arbitrary external paths. Keep a local Mango queue journal/lock to serialize processing; do not rely on synchronized lock files.

**Minimum request JSON interface** (wire shape for B/C/D agreement):

```json
{
  "schema": "innermaze-job-v1",
  "job_id": "job-<UUIDv4>",
  "job_type": "transcribe",
  "recording_id": "audio-<64-hex-sha256>",
  "source_sha256": "<64-hex-sha256>",
  "playback_sha256": "<64-hex-sha256>",
  "transcript_sha256": null,
  "options": { "reuse_cached_whisper": true, "model": "mlx-community/whisper-large-v3-turbo" },
  "requested_at": "<UTC ISO-8601>",
  "idempotency_key": "<64-hex-sha256>"
}
```

For `job_type: "classify"`, require the current `transcript_sha256`; `options` includes **separately versioned** model identifier/revision, prompt SHA-256, taxonomy ID/revision, output schema version and any deterministic controls. A `job_id` is a randomly generated persistent request UUID; **retry retains the job ID and increments `attempt`** in the state, while an explicit new categorization run gets a new job/run ID. Derive the idempotency key from canonical JSON of `{job_type,recording_id,source_sha256,playback_sha256,transcript_sha256,options}` (stable sorted keys, UTF-8, SHA-256), not from title/path/timestamp. Deduplicate concurrent equivalent requests, and return the existing run/result rather than silently rerun. Distinct explicit model/prompt/taxonomy revisions produce distinct results.

**State JSON:** `{schema,job_id,job_type,recording_id,phase,attempt,updated_at,progress:{current,total,label},error:{code,message,retryable}?}` with `phase` one of `queued`, `running`, `blocked`, `failed`, `completed`, `stale`, `canceled`. `blocked` (iCloud download, Mango offline, unavailable manifest) is not a model failure. Use monotonically increasing `attempt` and do not mark `completed` until outputs and integrity proofs exist. UI shows queued/blocked and the latest confirmed Mango heartbeat separately; Mango offline is **unknown/offline**, not a failed transcription. Every retry re-checks note ID, input hashes, audio availability and whether a terminal result already exists; interruption after writing sync but before Markdown update resumes safely from validated sidecar.

**Legacy note compatibility:** `transcription_status` remains `pending` or `ready` until C updates both the helper and its tests: current `process_one()` only recognizes `pending`. Detailed execution state lives in the job state, **not** new values smuggled into the legacy property. An existing `ready` transcript must never be overwritten by a retry, reimport or classification job. The existing pending-note worker remains opt-in/disabled unless explicitly enabled later.

## Transcript, AI, and review separation

- C's first successful transcription can fill a **still-empty, unchanged** pending note and corresponding timed sidecar. If body/identity/input hashes differ at commit time, mark job `stale` and preserve the note. Once user text exists, generate a separate transcript revision/proposal; never overwrite manually edited paragraphs or timing without an explicit user operation.
- D's categorization output is immutable **per run**, under a reserved distinct namespace such as `Voice Archive/AI/<recording_id>/<job_id>.json`; output includes `{schema,job_id,recording_id,transcript_sha256,source_sha256,model,prompt_sha256,taxonomy_id,taxonomy_revision,created_at,description,suggested_recording_type,suggested_categories,suggested_keywords,suggested_tags,confidence,needs_audio_analysis}`. This is a proposed interface, not a mandate to discard existing `voice_memo_describe_v2.1.0.py` schemas. Port/adapt that established descriptor schema with an explicit mapping.
- **`classification_status`:** `not_requested | queued | running | ready | failed | stale` describes *AI availability* per latest requested input fingerprint; `review_status`: `unreviewed | in_review | reviewed` describes *human decision state*. These are separate and must not be inferred from one another. An AI failure leaves prior successful runs intact and never mutates accepted tags.
- Store human accept/reject/override decisions independently (versioned review log with recording ID, reviewed transcript fingerprint, reviewer action/time, suggested run ID). Only explicit acceptance changes native Obsidian categories, tags and note properties; manual tags are never removed on a recategorization run. If the transcript is edited, label prior AI suggestions `stale` and allow a new run; preserve review history and accepted human organization.
- Model unavailable/invalid JSON/timeout: mark only the affected job failed with a bounded diagnostic; leave playable audio, transcript, previous classifications and human decisions untouched. No automatic overwrite or unbounded retry loop.

## Shared-file/release handoff protocol

Each agent records branch, base SHA, exact owned file paths, contract revision, tests and known blockers in its own branch documentation. Never edit `main` concurrently; use distinct branches and `git worktree` on Mango, preserving other agents' uncommitted state. Integration reviews diffs against current `origin/main`, explicitly resolves interface conflicts, and requires authorization before any merge. Do not force-push, cherry-pick another agent's branch, alter Git tags, or republish 0.1.9 while reconciling contracts.

**Source-only Mango changes:** test and package `tools/innermaze.py` separately through LLMRunner; no new BRAT tag. **Obsidian plugin changes:** pass the existing Node/Python test suite, get Mac/iOS/iPad real-device acceptance for the affected UI/player integration, then use the existing `release.yml` tag-equals-manifest workflow to publish `main.js`, `manifest.json`, `styles.css`. Never tag solely for docs/tests or claim device approval from CI. Backups in `~/Dropbox/Apps/Troubleshooting/Innermaze`, scratch in `~/Code/.staging/Innermaze`; no live Cabinet mutation during QA.
