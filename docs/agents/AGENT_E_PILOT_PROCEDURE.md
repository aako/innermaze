# Agent E — Controlled Mango Import & Process pilot

**State:** source candidate only; not approval to use the live Cabinet, update BRAT, or change the separately packaged `innermaze` command. **Base checkpoint:** `526d5257ffacd7762a2fdad9d32960640a33f94f`; hardening branch `integration/agent-e-pilot-hardening-v1`. Player version `0.1.9` unchanged.

## Create only a disposable test environment

1. On Mango, enter the **candidate worktree** `~/Code/Innermaze-worktrees/agent-e-pilot-hardening-v1`. Run `python3 tools/pilot_smoke_prepare.py` first (dry run). After inspecting the proposed paths, explicitly run `python3 tools/pilot_smoke_prepare.py --apply`. It will create a **new**, timestamped scratch root under `~/Code/.staging/Innermaze/`, not a vault in iCloud.
2. Inside that scratch root are `Synthetic Obsidian Vault`, `.obsidian/plugins/innermaze/{main.js,manifest.json,styles.css,data.json}`, and an original **synthetic** M4A in `Synthetic Voice Memos`. The test plugin's local `data.json` explicitly points to the candidate worktree helper. Inspect that path before running. This is **not** a BRAT installation and does not touch the working plugin installed in Cabinet.
3. Add/open **only the synthetic vault** as a new Obsidian vault on Mango. If community plugins are disabled in this test vault, enable this local Innermaze plugin; inspect its settings and confirm the absolute helper path ends in `agent-e-pilot-hardening-v1/tools/innermaze_import_process.py`. If the path is blank/unavailable, the import must **stop with a helpful error**; it must never fall back to `~/Code/Innermaze/tools/innermaze_import_process.py`.

## Actual desktop acceptance checklist (not simulated by automated tests)

- [ ] In Obsidian's command palette, invoke **Innermaze: Import audio from Files or Finder**. Choose the synthetic M4A. Set **Transcription: Later**, **Qwen: Skip**. Confirm the GUI launches the worktree Python helper (not the working main checkout) and creates one playable note and listening attachment in the synthetic vault.
- [ ] Open and play the recording in Innermaze's 0.1.9 player. Check same-tab note navigation, ordinary Markdown notes, edit-transcript mode and user-renamed note behavior.
- [ ] Reimport that same synthetic recording. Confirm the **same note** is reused, with no change to its human title/text and no second note. Retain note, playback and source audio checksums.
- [ ] Create another synthetic recording with a **different Apple UUID** for a separate immediate-transcription test; choose **Transcribe now**, **Qwen: Skip**. Watch the targeted Mango MLX Whisper execution. If MLX is missing/offline, record the failure and do not claim acceptance. Only the chosen note should change.
- [ ] With a **ready transcript**, select Qwen classification **After** and transcription **Later** on reimport. Confirm **no new Whisper job**, and a new immutable result under `Voice Archive/Classification/<sha256(recording_id)>/runs/` **only if** local Qwen is actually available. Suggested tags are not accepted automatically. Test Qwen unavailable/error leaves transcript, note metadata and existing runs unchanged.
- [ ] Remove/rename the *synthetic vault's listening copy* (not source audio) and reimport the same synthetic recording. Expect a **blocked** receipt for unavailable playback; the note, existing metadata and tags stay unchanged and no substitute playback asset is created. Restore the test audio copy to repeat other cases.
- [ ] In the modal, select **Transcribe later**, **Qwen later**. Verify no worker is activated and the GUI says not scheduled. There is intentionally no durable queue in this candidate.
- [ ] Confirm native Finder-path files preserve `source_title` and known archive source classification. When the platform hides the source path and staging is required, preserve selected filename but classify as `arbitrary-file`; do not claim Dropbox source.

## Model / UI / permission boundaries

Automated Python/Node tests validate the helper's command contract and synthetic vault behavior; they **do not** launch real Electron Obsidian, do not prove real MLX Whisper or Qwen is installed, and do not prove iOS player acceptance. Record those independently. The GUI invokes `/usr/bin/python3` using `spawn(...,{shell:false})`; check Mango macOS permissions, the candidate helper's dependencies, and whether its designated MLX interpreter has been installed. Opening this local candidate plugin in a disposable vault is intentionally separate from BRAT publication.

The fixture generator never imports or classifies by itself. Do not point the candidate test plugin at live Cabinet, and do not enable global pending workers or Cashew archive writers. No merge into `main` or release until Agent 0 reviews actual-device results and the user authorizes next steps.

## Recovery

Before replacing protected output paths, consider `~/.codex/bin/codex-audit protect --output <path> -- <command>`; review `codex-audit history`, `codex-audit show --last`, and `codex-audit undo --last` before using recovery controls. Git worktrees preserve source changes; do not assume arbitrary filesystem modifications are automatically reversible.
