#!/usr/bin/env python3
# filename: innermaze_gui_backend_pilot.py
# version: 0.1.0
# timestamp: 2026-10-27 09:38:00
"""Apply an isolated first-pass Obsidian/Mango import-processing UI to Innermaze.

Dry-run by default; --apply requires exact clean source baseline. Makes a
separate Git worktree. No publication, no worker activation, and no writes to
Cabinet or archived recordings.
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
from pathlib import Path
import shutil
import subprocess
import sys

HOME = Path.home()
REPO = HOME / 'Code/Innermaze'
DIAG = HOME / 'Dropbox/Apps/Troubleshooting/Innermaze'
EXPECTED = '0495bf4d116ad6a2b1f35ed25ea9bb8491233e02'
BRANCH = 'feature/import-process-gui-pilot'
WORKTREE = HOME / 'Code/Innermaze-worktrees/agent-c-import-process'

PY_INSERT = '''

def process_selected_note(vault: Path, relative_note: Path, model_python: Path, model: str,
                          whisper_root: Path = WHISPER, manifest_db: Path = BACKUP_DB) -> bool:
    """Transcribe exactly one existing Innermaze note; never drain the global queue."""
    if relative_note.is_absolute():
        raise ValueError("--process-note requires a vault-relative Markdown path")
    vault = vault.resolve()
    note = (vault / relative_note).resolve()
    if not note.is_relative_to(vault / "Voice Archive") or note.suffix.lower() != ".md":
        raise ValueError("--process-note must identify a Voice Archive Markdown note")
    if not note.is_file() or note not in {item.resolve() for item in recording_notes(vault)}:
        raise ValueError(f"Not a verified Innermaze recording note: {relative_note}")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with (TEMP_DIR / "transcription.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another local Innermaze transcription job is running") from None
        before = note.read_text("utf-8")
        fields, body = parse_note(before)
        if fields.get("transcription_status") == "skipped":
            if body.strip():
                raise ValueError("Human transcript present; refusing to change skipped status")
            if 'transcription_status: "skipped"' not in before:
                raise ValueError("Skipped marker not in expected form; preserving note")
            atomic_replace_bytes(note, before.replace('transcription_status: "skipped"',
                                                    'transcription_status: "pending"', 1).encode("utf-8"))
        return process_one(note, vault, model_python, model, whisper_root, manifest_db)
'''

JS_INSERT = r'''

// Desktop-only processing. The source archive is read-only; only the selected
// Cabinet recording note and its sync sidecar may be updated by Mango Python.
async function transcribeSelectedRecording(plugin, note) {
  if (!Platform.isDesktopApp) throw new Error('Local Whisper processing runs on Mango, not on iOS.');
  if (!(note instanceof TFile) || note.extension !== 'md') throw new Error('Select an Innermaze note.');
  const source = await plugin.app.vault.read(note);
  if (!isRecordingNote(note, source)) throw new Error('Selected file is not an Innermaze recording.');
  const { spawn } = require('child_process');
  const os = require('os');
  const path = require('path');
  const fs = require('fs');
  const helper = path.join(os.homedir(), 'Code', 'Innermaze', 'tools', 'innermaze.py');
  const vaultRoot = plugin.app.vault.adapter.getBasePath?.();
  if (!vaultRoot) throw new Error('A local vault path is required; open Cabinet on Mango.');
  if (!fs.existsSync(helper)) throw new Error(`Mango importer missing: ${helper}`);
  new Notice(`Innermaze: transcribing ${note.basename} on Mango…`);
  const output = await new Promise((resolve, reject) => {
    const args = [helper, '--vault', vaultRoot, '--process-note', note.path, '--apply'];
    const child = spawn('/usr/bin/python3', args, {shell: false, stdio:['ignore','pipe','pipe']});
    let log = '';
    const collect = data => { log = (log + String(data)).slice(-12000); };
    child.stdout.on('data', collect);
    child.stderr.on('data', collect);
    child.on('error', reject);
    child.on('close', (code, signal) => {
      if (code === 0) resolve(log);
      else reject(new Error(`Mango Whisper exited ${code ?? signal}: ${log.slice(-2000)}`));
    });
  });
  console.info('Innermaze targeted Mango transcription:', output);
  if (output.includes('INNERMAZE_TRANSCRIPTION_RESULT=unchanged')) {
    new Notice(`Innermaze: no pending transcription for ${note.basename}.`);
  } else {
    new Notice(`Innermaze: transcription completed for ${note.basename}.`);
  }
  return output;
}

// The Qwen backend is being ported by the categorization agent. Until then,
// the categorization knob records intent but NEVER claims to run an LLM.
class ImportAndProcessModal extends Modal {
  constructor(app, plugin) { super(app); this.plugin = plugin; this.transcribe = 'now'; this.categorize = 'later'; }
  onOpen() {
    this.setTitle('Innermaze — Import & Process');
    this.contentEl.createEl('p', { text: 'Choose a Dropbox recording on Mango. The original archive will not be modified.' });
    new Setting(this.contentEl).setName('Transcription').setDesc('MLX Whisper runs locally on Mango.')
      .addDropdown(dropdown => dropdown
        .addOption('now', 'Transcribe now')
        .addOption('later', 'Transcribe later')
        .addOption('skip', 'Do not transcribe')
        .setValue(this.transcribe)
        .onChange(value => { this.transcribe = value; }));
    new Setting(this.contentEl).setName('AI categorization').setDesc('The Qwen backend is not yet connected; this stores a future processing request.')
      .addDropdown(dropdown => dropdown
        .addOption('later', 'Queue for later (Qwen pending)')
        .addOption('skip', 'Do not categorize')
        .setValue(this.categorize)
        .onChange(value => { this.categorize = value; }));
    new Setting(this.contentEl).addButton(button => button.setButtonText('Choose recording…').setCta()
      .onClick(() => {
        const choices = {transcribe:this.transcribe, categorize:this.categorize};
        this.close();
        void importPickedAudio(this.plugin, choices);
      }));
    this.contentEl.createEl('p', { text: 'This pilot imports by audio-file hash. UUID-based cross-source reconciliation is being built separately; do not import the same iPhone-exported memo twice yet.' });
  }
  onClose() { this.contentEl.empty(); }
}
'''

PY_TEST = '''
    def test_targeted_processing_does_not_drain_other_pending_notes(self):
        one = innermaze.import_any(self.sample, self.vault, True, False,
                                   Path(sys.executable), innermaze.MODEL)
        other_audio = self.root / 'other.m4a'
        other_audio.write_bytes(b'different recording contents\\x00')
        other = innermaze.import_any(other_audio, self.vault, True, False,
                                     Path(sys.executable), innermaze.MODEL)
        self.assertTrue(innermaze.process_selected_note(
            self.vault, one.relative_to(self.vault), Path(sys.executable),
            innermaze.MODEL, self.whisper_root, self.manifest))
        self.assertEqual(innermaze.parse_note(one.read_text())[0]['transcription_status'], 'ready')
        self.assertEqual(innermaze.parse_note(other.read_text())[0]['transcription_status'], 'pending')
        with self.assertRaisesRegex(ValueError, 'vault-relative'):
            innermaze.process_selected_note(self.vault, one, Path(sys.executable), innermaze.MODEL)
        with self.assertRaisesRegex(ValueError, 'Not a verified'):
            innermaze.process_selected_note(self.vault, Path('Voice Archive/missing.md'),
                                           Path(sys.executable), innermaze.MODEL)

'''


def replace_once(original: str, target: str, replacement: str, where: str) -> str:
    count = original.count(target)
    if count != 1:
        raise ValueError(f'{where}: expected one unmodified anchor, found {count}: {target[:75]!r}')
    return original.replace(target, replacement, 1)


def patched(files: dict[str, str]) -> dict[str, str]:
    py = files['tools/innermaze.py']
    py = replace_once(py, '\ndef process_pending(vault: Path, model_python: Path, model: str, limit: int,', PY_INSERT + '\ndef process_pending(vault: Path, model_python: Path, model: str, limit: int,', 'importer')
    py = replace_once(py,
        '    choice.add_argument("--process-pending", action="store_true", help="Transcribe queued iPhone/Mac imports on Mango")',
        '    choice.add_argument("--process-pending", action="store_true", help="Transcribe queued iPhone/Mac imports on Mango")\n    choice.add_argument("--process-note", type=Path, help="Transcribe exactly one vault-relative recording note")', 'importer')
    py = replace_once(py,
        '        elif args.process_pending:\n            if args.apply:',
        '        elif args.process_note:\n            if args.apply:\n                changed = process_selected_note(vault, args.process_note, model_python, args.model,\n                                                args.whisper_root.expanduser(), args.manifest_db.expanduser())\n                print("INNERMAZE_TRANSCRIPTION_RESULT=" + ("ready" if changed else "unchanged"))\n            else:\n                relative = args.process_note\n                if relative.is_absolute() or not (vault / relative).resolve().is_relative_to(vault.resolve() / "Voice Archive"):\n                    raise ValueError("--process-note must be vault-relative in Voice Archive")\n                print(f"DRY RUN: would transcribe {relative} on Mango; no vault writes")\n        elif args.process_pending:\n            if args.apply:', 'importer')
    py = replace_once(py, '# version: 0.1.3', '# version: 0.1.4', 'importer')

    js = files['main.js']
    js = replace_once(js, "// version: 0.1.9", "// version: 0.1.10", 'main.js')
    js = replace_once(js, 'function makeGenericNote(id, title, audioPath, digest) {',
        'function makeGenericNote(id, title, audioPath, digest, options = {}) {', 'main.js')
    js = replace_once(js, '''    'transcription_status: "pending"',
    'tags:',
''', '''    options.transcribe === 'skip' ? 'transcription_status: "skipped"' : 'transcription_status: "pending"',
    ...(options.categorize === 'later' ? ['classification_status: "pending"'] : []),
    'tags:',
''', 'main.js')
    js = replace_once(js, 'async function makeNoteAndOpen(plugin, title, audioPath, digest, progress) {',
        'async function makeNoteAndOpen(plugin, title, audioPath, digest, progress, options = {}) {', 'main.js')
    js = replace_once(js, 'makeGenericNote(id, safeTitle(title), audioPath, digest));',
        'makeGenericNote(id, safeTitle(title), audioPath, digest, options));', 'main.js')
    js = replace_once(js, "  new Notice('Audio is in your iCloud vault. Transcription will run on Mango.');\n}",
        "  new Notice('Audio imported to Cabinet.');\n  return note;\n}", 'main.js')
    js = replace_once(js, 'async function importPickedAudio(plugin) {',
        'async function importPickedAudio(plugin, options = {}) {', 'main.js')
    js = replace_once(js, '        await makeNoteAndOpen(plugin, file.name, audioPath, digest, progress);',
        "        const note = await makeNoteAndOpen(plugin, file.name, audioPath, digest, progress, options);\n        if (options.transcribe === 'now') await transcribeSelectedRecording(plugin, note);", 'main.js')
    js = replace_once(js, '      } catch (error) { new Notice(`Audio import stopped: ${error.message || error}`, 12000); }',
        '      } catch (error) { new Notice(`Innermaze import/processing stopped (previously imported files preserved): ${error.message || error}`, 12000); }', 'main.js')
    js = replace_once(js, '\nclass InnermazeSettingsTab extends PluginSettingTab {', JS_INSERT + '\nclass InnermazeSettingsTab extends PluginSettingTab {', 'main.js')
    js = replace_once(js, '''      callback: () => { void importPickedAudio(this); }
''',
        '''      callback: () => {
        if (Platform.isDesktopApp) new ImportAndProcessModal(this.app, this).open();
        else void importPickedAudio(this);
      }
''', 'main.js')
    js = replace_once(js, "    this.addCommand({\n      id: 'use-current-vault-audio',",
        """    this.addCommand({
      id: 'transcribe-current-recording',
      name: 'Transcribe current recording on Mango',
      callback: async () => {
        const note = this.app.workspace.getActiveFile() ||
          this.app.vault.getAbstractFileByPath(this.lastRecordingPath);
        try { await transcribeSelectedRecording(this, note); }
        catch (error) { new Notice(`Innermaze transcription: ${error.message || error}`, 12000); }
      }
    });
    this.addCommand({
      id: 'use-current-vault-audio',""", 'main.js')

    tests = files['tests/test_importer.py']
    tests = replace_once(tests, '\n    def test_import_dry_run_creates_nothing(self):',
        '\n' + PY_TEST + '    def test_import_dry_run_creates_nothing(self):', 'test_importer')

    manifest = files['manifest.json']
    manifest = replace_once(manifest, '"version": "0.1.9"', '"version": "0.1.10"', 'manifest')

    note = '''\n## 0.1.10 candidate — Dropbox import & targeted Mango transcription pilot\n\n- **Unreleased, device testing required.** The 0.1.9 working player code is otherwise unchanged.\n- On desktop the existing Import Audio command opens a modal with separate transcription controls (now/later/skip) and Qwen intent (later/skip).\n- `Transcribe now` calls a Mango-only targeted `--process-note` Python operation; it does **not** start the 10-minute worker, drain the pending queue, or require an installed `innermaze` LLM Runner wrapper.\n- `Queue Qwen for later` writes `classification_status: pending` on a **newly created** note; it does not run a classifier or modify existing human decisions. Agent D will own classification implementation and schema reconciliation.\n- First pilot uses whole-file hash identity. **Do not reimport the same Apple UUID from another source** until Agent B integrates its verified UUID reconciliation.\n- The original Dropbox source is read-only; Vault writes are limited to the selected audio copy/note/sync. Existing notes are never overwritten.\n- First release needs device acceptance in Mango/Obsidian, especially permission to spawn Python and iCloud placeholder handling.\n'''
    doc = note + '\n' + files['docs/PROJECT_STATE.md']
    return {'tools/innermaze.py':py, 'main.js':js, 'tests/test_importer.py':tests,
            'manifest.json':manifest, 'docs/PROJECT_STATE.md':doc}


def run(cmd: list[str], cwd: Path, capture: bool = False) -> str:
    p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None, check=True)
    return p.stdout.strip() if capture else ''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Write tested changes to a new feature branch')
    parser.add_argument('--repo', type=Path, default=REPO)
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    if not (repo / '.git').exists():
        raise RuntimeError(f'STOP: Not a Git checkout: {repo}')
    head = run(['git','rev-parse','HEAD'],repo,True)
    if head != EXPECTED:
        raise RuntimeError(f'STOP: Expected tested baseline {EXPECTED[:12]}, found {head[:12]}; no changes made.')
    dirty = run(['git','status','--porcelain'],repo,True)
    if dirty:
        raise RuntimeError('STOP: Checkout is not clean. Preserve other agents\' work and retry on a clean checkout.')
    filenames = ['tools/innermaze.py','main.js','tests/test_importer.py','manifest.json','docs/PROJECT_STATE.md']
    originals = {name:(repo/name).read_text('utf-8') for name in filenames}
    updates = patched(originals)
    for name in filenames:
        print(f'  UPDATE: {name} ({len(updates[name]) - len(originals[name]):+d} characters)')
    if not args.apply:
        print('\nDRY RUN: checked source anchors; no files, Git refs, or Vault data changed.')
        print('Run with --apply to create a SEPARATE feature worktree and test the candidate.')
        return
    exists = subprocess.run(['git','show-ref','--verify','--quiet',f'refs/heads/{BRANCH}'],cwd=repo).returncode == 0
    if exists:
        raise RuntimeError(f'STOP: Branch {BRANCH} already exists. Not overwriting it.')
    backup = DIAG / ('pilot-before-0.1.10-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
    for name in filenames:
        dest = backup/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(originals[name],'utf-8')
    if WORKTREE.exists():
        raise RuntimeError(f'STOP: Worktree path already exists: {WORKTREE}')
    WORKTREE.parent.mkdir(parents=True, exist_ok=True)
    run(['git','worktree','add','-b',BRANCH,str(WORKTREE),EXPECTED],repo)
    try:
        for name in filenames:
            (WORKTREE/name).write_text(updates[name], 'utf-8')
        validations=[['node','--check','main.js'],
                     ['node','tests/test_alignment.cjs'],
                     ['node','tests/test_auto_open.cjs'],
                     ['node','tests/test_navigation_guard.cjs'],
                     ['node','tests/test_recording_rename.cjs'],
                     ['node','tests/test_lossless_ab.cjs'],
                     ['node','tests/test_optimized_playback.cjs'],
                     ['python3','-m','unittest','discover','-s','tests','-p','test_importer.py']]
        for cmd in validations:
            print('TEST:', ' '.join(cmd),flush=True)
            run(cmd,WORKTREE)
    except BaseException:
        print('TEST/WRITE FAILED: restoring original worktree source files; leaving feature branch for inspection.', file=sys.stderr)
        for name in filenames:
            (WORKTREE/name).write_text(originals[name], 'utf-8')
        raise
    print('\nPASS: all checks completed. No commit, push, tag or release was made.')
    print('Branch:',BRANCH)
    print('Worktree:',WORKTREE)
    print('Backup:',backup)
    print('Review: cd '+str(WORKTREE)+'; git diff --stat; git diff')
    print('Hold for integration with Agent B/D before publishing.')

if __name__=='__main__':
    main()
