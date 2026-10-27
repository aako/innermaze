# Innermaze

**Innermaze 0.1.6** — an Obsidian plugin for listening to audio alongside editable, line-synchronized Markdown transcripts. This is an evolving pilot; the 0.1.5 lossless A/B engines have been verified by the user on iOS, locked and unlocked. The automatic 0.1.6 behavior still needs device testing.

The Obsidian plugin lives at the repository root: `main.js`, `manifest.json`, and `styles.css`. Its **separate, optional** Mango transcription/import helper is at `tools/innermaze.py`. The helper is never installed or updated by BRAT.

## Architecture

- **Cashew (Intel):** permanent Dropbox-backed Apple Voice Memo and Just Press Record archives, managed by the existing `voice_audio_backup` service. Innermaze does not modify them.
- **Mango (Apple Silicon):** local Whisper (`mlx-community/whisper-large-v3-turbo`) transcripts and import processing. The helper can also import any arbitrary audio file.
- **Obsidian Mac/iOS/iPadOS:** iCloud vault contains a single listening copy per audio SHA-256 where practical, editable timestamp-free Markdown notes, and external word/line timing.

For compatibility, transcript notes still use `voice_archive` frontmatter and `Voice Archive/Audio`, `Voice Archive/Notes`, and `Voice Archive/Sync`. **Do not rename these folders** without a managed migration; they are data paths, not the plugin's displayed name. The Obsidian plugin's ID and display name are both **Innermaze** (`innermaze`).

## Local checkout

This source project is designed to live at `~/Code/Innermaze`. The companion standalone installer `innermaze_setup.py` installs it there safely and initializes a Git repository; it never modifies your Obsidian vault or archives.

## Publish for BRAT

1. Create a **public** empty GitHub repository `aako/innermaze` (the connected GitHub integration cannot create repositories). Once it exists, push this repository's `main` branch to it.
2. When ready to publish the first beta, tag the tested commit **`0.1.0`** and push the tag. The included GitHub Action checks JavaScript and Python tests and creates a GitHub prerelease with the `main.js`, `manifest.json`, and `styles.css` assets BRAT requires.
3. In Obsidian BRAT use **Add Beta Plugin** > `aako/innermaze`, then enable **Innermaze** under Community Plugins.

Do not manually install this plugin by copying it to `.obsidian/plugins`; BRAT should own that folder. Do not publish a tag until you're ready to test the release in Obsidian.

## Player auto-open (0.1.3)

Opening a Voice Archive recording note automatically opens the complete Innermaze player in a regular Obsidian tab, matching the original manual command. The existing player tab is reused. Switching to a player tab does not cancel the pending opening operation, and **Open player for current voice recording** can reopen the last verified recording when the player itself has focus. Ordinary Markdown notes remain unaffected. Disable auto-open in Innermaze settings when preferred.

## Mango transcription helper (optional)

To package the helper into LLMRunner, use your usual **Package New** workflow on `tools/innermaze.py` (complete standalone Python). The stable executable becomes `~/Code/LLMRunner/bin/innermaze`.

Examples (dry-run first):

```fish
innermaze --file "$HOME/Downloads/sample.m4a"
innermaze --file "$HOME/Downloads/sample.m4a" --apply
```

The helper can import existing Dropbox archive recordings and can install an opt-in 10-minute pending-transcription worker on Mango. The separate helper does **not** overwrite BRAT plugin files. All source audio and canonical archives are read-only.

## Listening: skip long pauses (player 0.1.1)

The **Skip long pauses** checkbox in the Innermaze player is on by default and
persisted per Obsidian installation. It seeks over gaps of at least two seconds
between Whisper-timed words (or timed segments when words are unavailable),
retaining approximately 0.4 seconds before and after speech. It does **not** modify the original audio file. In 0.1.6 it generates a temporary
lossless in-memory M4A when supported; otherwise it uses the legacy seek method.
Uncheck it to listen to the original timing.
The system uses transcription gaps, not an acoustic silence detector: music or
other meaningful non-speech within a gap can be skipped. This feature should be
validated on a variety of recordings before public release.

The current transcript highlight remains on the last spoken line throughout
pauses and only advances when the next line starts. It never vanishes merely
because the previous line ended. Both behaviors work in the separate Innermaze
player opened by **Innermaze: Open player for current voice recording**.

## Existing Voice Memos transcripts — automatic reuse (Mango helper 0.1.1)

Importing a file with `--file`, or processing a pending note created by the Obsidian mobile importer, now looks up **the source audio SHA-256** in both existing `identity.tsv` indices under `~/Work/Temp/VoiceMemoSaver/Whisper/`. If matching word-timed `.json` exists, Innermaze creates the normal editable Markdown and external `.sync.json` directly; **it does not run MLX Whisper again**. This also works for renamed archived recordings. Repeated imports preserve existing notes and corrections.

```fish
python3 ~/Code/Innermaze/tools/innermaze.py --file "/Users/ak/Dropbox/1 NOTES/Voice Memos Full DB/2026-09-03 07-52-07 - Dream - film photo shoot.m4a"
python3 ~/Code/Innermaze/tools/innermaze.py --file "/Users/ak/Dropbox/1 NOTES/Voice Memos Full DB/2026-09-03 07-52-07 - Dream - film photo shoot.m4a" --apply
```

Both commands work without modifying the original Dropbox recording. The first is a **dry run**, including cached-transcript availability; the second creates the iCloud playback copy/note as needed. If the original index is not on Mango, supply `--whisper-root PATH`. If the Apple UUID manifest is now held on Cashew, provide a **readable, verified copy** with `--manifest-db PATH`; otherwise Innermaze does not invent an Apple UUID. An unambiguous Apple UUID is used for new archive notes, and added as `archive_recording_id` metadata for mobile notes without changing existing Markdown links.

If two different indexed transcripts claim the same audio hash, Innermaze stops before writing rather than choose potentially wrong timestamps. Audio with no matching transcript uses local MLX Whisper (unless `--no-transcribe` is supplied). **BRAT updates only the Obsidian player**; this change is in the separate Mango helper and does not change the plugin `0.1.0` release.

## Local tests

```fish
node --check main.js
node tests/test_alignment.cjs
python3 -m unittest discover -s tests -p 'test_importer.py'
```

## Limitations

- Existing word/line timestamp data is separate from the editable Markdown; corrections within a paragraph preserve line timing, while paragraph splits/merges can need review.
- MLX Whisper is Apple-Silicon-only. Cashew should not perform this stage.
- For offline playback on iPhone/iPad, iCloud must finish downloading the audio attachment.
- The separate Innermaze player has been opened on Mango. The 0.1.5 lossless A/B files passed iOS locked/unlocked tests; the 0.1.6 automatic-switching behavior still needs real-device testing.
- BRAT installation requires a published GitHub release with three assets.

## Acknowledgments and provenance

Innermaze was independently implemented, with design inspiration from:

- [Interactive Transcripts](https://github.com/andymatuschak/interactive-transcripts) by Andy Matuschak — editable Markdown transcripts with audio timing stored separately.
- [Media One](https://github.com/semsevens/obsidian-media-transcript) by semsevens — synchronized transcript highlighting and click-to-seek playback.

These are conceptual references, not copied source code or bundled dependencies. Both projects are MIT-licensed. If their code is incorporated in the future, the applicable copyright and license notices must be retained.

## Acknowledgments and provenance

Innermaze was independently implemented, with design inspiration from:

- [Interactive Transcripts](https://github.com/andymatuschak/interactive-transcripts) by Andy Matuschak — editable Markdown transcripts with audio timing stored separately.
- [Media One](https://github.com/semsevens/obsidian-media-transcript) by semsevens — synchronized transcript highlighting and click-to-seek playback.

These are conceptual references, not copied source code or bundled dependencies. Both projects are MIT-licensed. If their code is incorporated in the future, the applicable copyright and license notices must be retained.

## Automatic seamless pause skipping (0.1.6)

The existing **Skip long pauses** checkbox (default on) now prepares a temporary
lossless M4A edit-list source automatically once the note's transcript timing
and original audio metadata are available. Its original AAC data is unchanged.
Packet-copy M4A is built **only if** edit-list assembly or media playback fails;
the fallback isn't shown as an interface selector. Other source formats and
unsupported M4A layouts retain the old seek-based skipping behavior.

The final untranscribed gap is also shortened when at least 3 seconds remain
after the last recognized word; 1 second is preserved after the last word.
No permanent audio, transcript, sync, or vault changes occur. Temporary audio
Blob URLs are released on player close or note change. For verification on
iOS: check ordinary playback, rapid note switching, pauses toggle, highlighting
and seeking, resume position, AirPods and locked-screen playback.

## Recording-note organization and naming (0.1.7)

- New notes are created directly in `Voice Archive/`, next to `Audio/` and `Sync/`.
- To migrate the previous notes once, use Obsidian's command palette: **Innermaze: Move recording notes into Voice Archive root**. Existing transcript text, audio, sync files, and recording identities are preserved. File renaming is performed through Obsidian's file manager, which maintains links; any collisions are skipped. If the old Notes folder is empty, it is removed.
- Use **Rename** in the player to set both the human-readable player heading and the Markdown note filename, for example `Dream - Chris Wiley sushi restaurant`. The immutable `recording_id`, `source_title`, and timing sidecar are unchanged.
- The Mango importer finds already-renamed recordings by frontmatter `recording_id` and audio SHA-256, not filename.
- **Skip long pauses** continues to use the current lossless edit-list engine with the hidden packet fallback.

## 0.1.8: Same-tab recording player

Opening a recording shows Innermaze in its original note tab. “Edit transcript” switches that same tab to Markdown; run “Innermaze: Open player for current voice recording” to switch it back. No new player tab is created.
