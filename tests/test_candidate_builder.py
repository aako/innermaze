#!/usr/bin/env python3
# filename: test_candidate_builder.py
# version: 0.2.0
# timestamp: 2026-10-27 14:38:00
"""Guardrail tests for offline staging of a complete BRAT main.js candidate."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'tools'))
import prepare_mobile_candidate as builder

FIXTURE = '''// Synthetic structure matching reviewed Agent E mobile boundaries
function makeGenericNote() {
  const id = `audio-${digest}`;
}
async function importPickedAudio(plugin) {
  await makeNoteAndOpen(plugin, file.name, audioPath, digest, progress);
}
async function importVaultAudio(plugin, file) {
  await makeNoteAndOpen(plugin, file.name, file.path, digest, progress);
}
// Strictly configured Mango candidate helper:
function innermazeResolveMangoHelper(plugin) { return true; }
'''


class CandidateBuilderChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = (HERE / 'tools/mobile_recording_identity.cjs').read_text('utf-8')
        self.methods = (HERE / 'tools/mobile_import_methods.cjs').read_text('utf-8')

    def test_preserves_every_non_mobile_byte(self):
        produced = builder.prepare(FIXTURE, self.identity, self.methods)
        self.assertTrue(produced.startswith(FIXTURE[:FIXTURE.index(builder.START)]))
        self.assertTrue(produced.endswith(FIXTURE[FIXTURE.index(builder.END):]))
        self.assertIn('parseAppleVoiceMemoUuid(bytes)', produced)
        self.assertIn('same_uuid_unverified_audio_version', produced)
        self.assertNotIn('module.exports = {parseAppleVoiceMemoUuid', produced)
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'main.js'
            file.write_text(produced, 'utf-8')
            subprocess.run(['node', '--check', str(file)], check=True)

    def test_disallows_diverged_functions(self):
        changed = FIXTURE.replace('const id = `audio-${digest}`;', 'const id = `some-new-identity`;')
        with self.assertRaisesRegex(ValueError, 'diverged'):
            builder.prepare(changed, self.identity, self.methods)

    def test_disallows_reapplying(self):
        transformed = builder.prepare(FIXTURE, self.identity, self.methods)
        with self.assertRaises(ValueError):
            builder.prepare(transformed, self.identity, self.methods)

    def test_create_only_and_dry_run(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.js'
            target = Path(temp) / 'staged' / 'main.js'
            source.write_text(FIXTURE, 'utf-8')
            cmd = [sys.executable, str(HERE / 'tools/prepare_mobile_candidate.py'),
                   '--source', str(source), '--output', str(target)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.assertFalse(target.exists())
            subprocess.run([*cmd, '--apply'], check=True, capture_output=True, text=True)
            self.assertTrue(target.is_file())
            self.assertEqual(source.read_text('utf-8'), FIXTURE)
            attempts = subprocess.run([*cmd, '--apply'], capture_output=True, text=True)
            self.assertNotEqual(attempts.returncode, 0)
            self.assertIn('Refusing to overwrite existing output', attempts.stderr)


def main() -> None:
    unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
