# Innermaze

**Innermaze 0.1.0** — an Obsidian plugin for listening to audio alongside editable, line-synchronized Markdown transcripts. This is an early pilot; Mac/iOS device playback has not yet been verified.

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

## Mango transcription helper (optional)

To package the helper into LLMRunner, use your usual **Package New** workflow on `tools/innermaze.py` (complete standalone Python). The stable executable becomes `~/Code/LLMRunner/bin/innermaze`.

Examples (dry-run first):

```fish
innermaze --file "$HOME/Downloads/sample.m4a"
innermaze --file "$HOME/Downloads/sample.m4a" --apply
```

The helper can import existing Dropbox archive recordings and can install an opt-in 10-minute pending-transcription worker on Mango. The separate helper does **not** overwrite BRAT plugin files. All source audio and canonical archives are read-only.

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
- No real-device Obsidian playback has been verified yet.
- BRAT installation requires a published GitHub release with three assets.
