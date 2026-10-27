# Agent C — Import & Process GUI candidate

**Source-only pilot: DO NOT EXECUTE THIS SCRIPT OR APPLY IT TO THE WORKING PLAYER YET.**

Agent C produced this complete local patch-builder targeting the exact 0.1.9 main commit. It would alter `main.js`, `tools/innermaze.py`, `tests/test_importer.py`, `manifest.json` and `docs/PROJECT_STATE.md` in a separate worktree, creating an unreleased 0.1.10 candidate. Because Agent B's new identity reconciler was not yet integrated, applying C's pilot as-is could duplicate a Voice Memo received through different routes.

This branch stores the original complete proposal for Agent E to inspect and reconcile with B and D. The only archival cleanup here changes `raise SystemExit` to `raise RuntimeError`, without altering the core patch intent. Agent E should produce the integrated replacement and run full regression tests before any device testing or BRAT release.

Agent C originally reported synthetic importer tests, not device acceptance. No code in this branch modifies production runtime files.
