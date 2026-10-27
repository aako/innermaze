## Release 0.1.8: recording note is the player tab

- Innermaze now uses Obsidian `FileView` in the SAME `WorkspaceLeaf` as the opened Markdown recording. No second “Innermaze” tab. Old fileless 0.1.7 player tabs are detached on layout restore (only when state has no file), without touching Markdown tabs. FileView owns the note path, so tab title, file-open navigation and workspace active file resolve to the recording note.
- Edit transcript switches that same leaf back to native Markdown/source mode. The explicit "Open player for current voice recording" command switches back. Opening another recording uses its own tab as usual; no globally reused utility player.
- When a non-recording Markdown note opens in an Innermaze file leaf, it reverts to native Markdown view. A brief intentional suppression avoids immediately bouncing back to the player when Edit transcript is pressed.
- Deferred view hydration retained; 0.1.7 lossless edit-list playback, hidden packet fallback, timed highlights, and renamed notes unchanged. No iCloud vault file changes, scheduled worker changes, or source-audio writes.
- Real Obsidian macOS/iOS behavior must be tested after BRAT install; mock tests are not device acceptance.

## Release 0.1.7: recording titles and root-level notes

New recording notes live directly in `Voice Archive/`. The opt-in Obsidian command "Move recording notes into Voice Archive root" migrates verified legacy `Voice Archive/Notes/*.md` through Obsidian fileManager; never overwrites collisions. The player Rename action synchronizes Markdown filename and `display_title`, preserving `recording_id`, `voice_sync`, audio, original source titles, transcript and resume position. Mango importer scans root and legacy notes by immutable `recording_id` + SHA and reuses renamed notes. No worker activation.

# Innermaze Project State — 2026-10-26

## Production and device acceptance

- `0.1.5` has been published through GitHub/BRAT. User confirmed both temporary, losslessly assembled M4A approaches (MP4 edit lists and AAC packet copying) work without audible interruption in Obsidian on iOS, **both locked and unlocked**. This is real-device acceptance of the A/B mechanisms, not of the next version.
- `0.1.6` is a prepared source candidate **not yet published or device-tested**. This promotes edit-list preparation to the ordinary player and retains packet copying as a hidden automatic fallback; neither appears in the UI.
- The old player with `currentTime` seeks remains as the last-resort fallback and is used when Skip long pauses is disabled (without seeking) or optimization cannot be performed.

## 0.1.6 changes

- Existing Skip long pauses checkbox (default on) now prepares a temporary lossless M4A using MP4 edit lists automatically after both audio metadata and word timings are available. It retains the native HTML audio element, transcript highlighting, playback-speed control, and original-time position mapping.
- If edit-list **construction** fails, automatically attempt the packet-copy builder. If the edit-list **audio source** is rejected by the media element, revert to original and try packet copying once. If that is also rejected, stay on the original without any loops. Fallback mode is not user-facing; diagnostics go to the developer console.
- Original M4A and transcript/timing files are never modified. Temporary Blob URLs are revoked on note change and player close; repeated toggles for the same loaded note reuse available in-memory results.
- End dead air: add a trailing gap only when the last recognized word is at least three seconds before the original end. Retain one second after the last recognized word. Sidecar word/segment timestamps are used, not acoustic silence detection. If no reliable timestamps, no tail trimming.
- Limit optimization to ordinary M4A files not exceeding 128 MiB. Others retain the existing playback behavior. No Cloud/FFmpeg/WebAssembly dependency, no new permanent audio files, no transcription helper or LaunchAgent changes.
- Source replacement during *ongoing* playback attempts to resume at the mapped position. iOS-specific autoplay restrictions and locked-device transition timing still require manual testing; tapping Play remains possible if WebKit blocks auto-resume.

## Verification and release discipline

- `node --check main.js` plus alignment, deferred-view auto-open, A/B MP4, automatic playback, and importer tests must pass.
- The A/B fixtures cover both metadata-first and metadata-last M4A input. Both outputs are valid synthetic M4As; direct iPhone acceptance remains from `0.1.5` only.
- Publisher must require a clean `~/Code/Innermaze` checkout at exact `0.1.5` main commit, matching `origin/main`, and absent existing tag. It must back up source files under `~/Dropbox/Apps/Troubleshooting/Innermaze` and not touch the Obsidian Cabinet vault, original audio, Markdown, timestamps, or disabled worker.
- GitHub release workflow triggers only from an explicitly pushed tag; do not claim the release succeeded until the GitHub Action completes.
