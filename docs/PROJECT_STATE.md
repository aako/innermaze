# Innermaze Project State

## Current release

- Version under publication: **0.1.4** (Obsidian deferred-view playback fix).
- Previous production release: **0.1.3**; Obsidian runtime probe conclusively reproduced a `DeferredView` without `setRecordingPath`.
- User explicitly chose testing in the existing Cabinet vault via the normal GitHub/BRAT release, without a separate test vault.
- Real-device behavior of 0.1.4 remains **unverified until user testing**.

## Confirmed cause and fix

When restoring a background tab, Obsidian may expose a leaf of type `innermaze-player` backed by a `DeferredView`, not yet an `InnermazeView`. Version 0.1.3 called `setRecordingPath` before revealing/hydrating the view, yielding `Player view did not initialize`. Version 0.1.4 reveals the leaf, loads it when deferred, then checks the initialized view and passes the recording path. The player still reuses one tab and ignores synthetic `file-open(null)` events.

## Verified checks before publication

- Node syntax check and paragraph-alignment, pause-skip, continuous-highlight tests.
- Regression tests reproducing deferred view on 0.1.3 and passing on 0.1.4.
- Auto-open, manual command, tab reuse, mobile simulated tests.
- 12 Python transcription/importer tests.
- Actual Obsidian startup/desktop/iOS playback: **requires user test**.

## Safety and release rules

- No audio, timestamp JSON, Markdown transcripts, user edits, Apple Voice Memos or Dropbox archive changes.
- No background transcription activation or bulk processing.
- GitHub release tags require a matching manifest and passing CI.
- Before any future release, record the runtime symptom and add the smallest failing regression test.
