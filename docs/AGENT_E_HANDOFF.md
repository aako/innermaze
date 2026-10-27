# Innermaze — Agent E handoff (2026-10-27)

## Verified source baseline

- Repository: `https://github.com/aako/innermaze`, default `main`, head `0495bf4d116ad6a2b1f35ed25ea9bb8491233e02` (Mango source-path migration). Tagged plugin `0.1.9` commit: `fa2dd2b0e9c4254653898eb0745a03642b00043d`. `manifest.json` is still `0.1.9`.
- Inspected `docs/PROJECT_STATE.md`, `README.md`, `tools/innermaze.py`, `main.js` import interface, `tests/test_importer.py` and Node navigation tests, `.github/workflows/release.yml`, repository directory tree, recent commits and GitHub Actions. Searched prior ChatGPT planning context and Library files including `innermaze_publish.py`, `innermaze_migrate_paths.py`, `voice_memo_describe_v2.1.0.py`, and `voice_memo_review_v1.2.0.py`.
- Existing repo tests include six Node `.cjs` test files and Python importer tests (16 `test_*` methods). The GitHub `0.1.9` tag release CI succeeded: `https://github.com/aako/innermaze/actions/runs/40441577038`. No branch CI, live-vault tests or new B/C/D integration tests executed here. User reports 0.1.9 actual-device player works; older repository notes predate that acceptance.
- Existing importer supports SHA-256 deduplication, source/playback hash distinctions, verified Apple UUIDs, renamed notes, cached Whisper reuse, and guarded pending-note processing. Current note `transcription_status` only supports `pending`/`ready`. New job protocol, classification status and human-review persistence are proposals, not implementation.

## GitHub write blocker / no unsafe fallback

- Attempted **dedicated branch** `docs/agent-e-integration-contract-20261027` from exact current main `0495bf4d...` via connected GitHub write integration; **GitHub API returned 403 `Resource not accessible by integration`**. A fallback GitHub issue creation was also rejected with the same 403; **no branch, issue or commit exists**. Container cannot access `github.com` directly (DNS failure), so local Git push cannot bypass that safely.
- The three complete docs in this deliverable are local downloadable artifacts, **not yet on GitHub**. Do not claim that another agent can read them from GitHub. No writes to `main`, tags, BRAT, player, live vault, Mango helpers, or Cashew archive. Intended eventual branch name remains reserved by convention, not actually present.

## Intended branch content and next verified step

- `docs/INTEGRATION_CONTRACT.md`: shared B/C/D field, identity, queue, retry, provenance and human-review boundaries.
- `docs/INTEGRATION_TEST_MATRIX.md`: 24 targeted cases, existing release test commands, cross-agent blockers.
- `docs/AGENT_E_HANDOFF.md`: source baseline, scope, release separation and truthful publication status.

After GitHub write permissions are enabled, create a dedicated branch from a freshly verified `origin/main`, add these exact files to it, run the existing test gate in a worktree, and open a **draft documentation PR**. Do not merge without authorization. If `origin/main` has advanced, review the intervening commits first and reconcile with the B/C/D owners. Release procedures stay unchanged.

## Checkpoint

**Objective:** protect the stable 0.1.9 player while B/C/D build identity/import, Mango processing and review. **Decisions:** immutable `recording_id`; separate Apple UUID and source/playback hashes; per-job state with idempotent retries; classification and reviewed human choices kept separate; no iOS-local model. **Ruled out:** filename-as-ID, changing existing note identity, writing main to bypass permissions, toggling worker, BRAT release for Mango-only work. **Risks:** cross-device iCloud sync races, ambiguous duplicate UUIDs, edited transcripts, shared-file conflicts, GitHub integration 403. **Exact next action:** publish the documentation on a dedicated branch once connector write access is available, then check B/C/D agreement against the contract and execute proposed integration tests before merging anything.
