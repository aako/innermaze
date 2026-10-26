# Innermaze Project State

## Version and release discipline

- Current production BRAT release: `0.1.3` (confirmed installed in Cabinet).
- Proposed fix: `0.1.4`, not published until explicitly approved.
- `0.1.4` adds no user-data migrations and changes no recording or transcription files.

## Confirmed defect

The 2026-10-26 runtime probe shows `getLeavesOfType('innermaze-player')` returns
an existing leaf whose declared type is correct, but whose view lacks
`setRecordingPath`, an audio element, and an initialized note. Calls to
`openPlayerForNote` fail before `revealLeaf`.

Obsidian 1.7.2+ defers background views. An existing leaf can contain a
`DeferredView` rather than `InnermazeView`. Obsidian's developer documentation
prescribes awaiting `workspace.revealLeaf(leaf)` before using the custom view,
or `leaf.loadIfDeferred()` when needed. This is the verified cause of the
`Player view did not initialize` exception, not a BRAT or transcript issue.

## Proposed fix and verification

- Reveal existing tab, optionally loadIfDeferred if still deferred, and only
  then call `InnermazeView.setRecordingPath`.
- Preserve one player tab, no duplicate tabs, manual and auto-open paths,
  and ignoring `file-open(null)` events.
- Regression tests simulate both reveal-driven and fallback deferred hydration.
- JavaScript and Python test suites must pass before promotion.
- Real-device Obsidian testing and Mac/iOS/iPad playback are **not yet verified**
  for this candidate; do not describe them as passed.

## Reliability rules

- No rewriting audio, transcript text, timestamp JSON, or human review decisions.
- Never bulk-import or start Whisper without user intent.
- Separate verification/test builds from publishing release tags.
- A release requires explicit approval after testing in a separate vault.


## 0.1.5 Experimental Lossless A/B

Opt-in `Prepare both` button in existing player; nothing processes merely from opening a recording.
A = multi-entry MP4 edit list selecting speech intervals; original AAC packets left intact.
B = MP4 single-track AAC packet-table rebuild, deleting the skipped packets; no transcoding.
No external library, FFmpeg, server, or generated vault file is needed. Both create temporary
`Blob` URLs and use the unchanged native HTML audio element. Memory released on player close
(and when opening another note). Maximum input size for experiment: 128 MiB.
Unsupported MP4 layouts rejected with status; original seek-based player remains untouched.
Opening iOS files, compressed Blob playback with phone locked, and discontinuity clicks
require real-device validation. No crossfades: packet joins may click independently of WebKit.
Source timeline is mapped to compressed timeline for highlights, click-to-seek and resume.

Testing: ffmpeg/ffprobe verifies produced edited clips; Node unit tests guard source
immutability and time mapping. No transcriber / archive writes or LaunchAgent changes.
