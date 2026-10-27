#!/usr/bin/env python3
# filename: test_integration_agent_e.py
# version: 0.1.1
# timestamp: 2026-10-27 11:21:00
"""Agent E: synthetic single-recording B+C+D integration acceptance tests."""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import innermaze
import recording_identity as b
import innermaze_import_process as e

UUID1 = '12345678-1234-4234-8234-1234567890AB'
UUID2 = '22345678-1234-4234-8234-1234567890AB'


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe required')
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / 'Cabinet'
        (self.vault / '.obsidian').mkdir(parents=True)
        self.old_temp = innermaze.TEMP_DIR
        innermaze.TEMP_DIR = self.root / 'Scratch'
        self.addCleanup(lambda: setattr(innermaze, 'TEMP_DIR', self.old_temp))
        self.empty_index = self.root / 'MissingWhisper'
        self.missing_manifest = self.root / 'MissingManifest.sqlite'
        self.raw = self.root / 'raw.m4a'
        self.iphone = self.root / 'iphone.m4a'
        self.archive = self.root / 'archive.m4a'
        self.other = self.root / 'other.m4a'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:duration=0.3', '-c:a', 'aac', str(self.raw)], check=True)
        self.remux(self.raw, self.iphone, UUID1, 'iphone')
        self.remux(self.iphone, self.archive, UUID1, 'archive')
        self.remux(self.iphone, self.other, UUID2, 'other')
        self.base_args = dict(vault=self.vault, apply=True, transcribe='later', classify='skip',
            model_python=Path('/nonexistent-mlx'), whisper_model=innermaze.MODEL,
            whisper_root=self.empty_index, manifest_db=self.missing_manifest,
            qwen_model='test-qwen', server='http://127.0.0.1:1234/v1')

    def remux(self, src, dst, uuid, title):
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src),
               '-map', '0:a:0', '-c', 'copy', '-movflags', 'use_metadata_tags',
               '-metadata', f'title={title}', '-metadata', f'voice-memo-uuid={uuid}', str(dst)]
        subprocess.run(cmd, check=True)

    def run_file(self, path=None, **overrides):
        args = dict(self.base_args)
        args.update(overrides)
        return e.run_file(path or self.iphone, **args)

    def notes(self):
        return innermaze.recording_notes(self.vault)

    def test_remux_reuses_one_uuid_note_without_mutation(self):
        one = self.run_file()
        note = self.vault / one['note']
        original = note.read_bytes()
        self.assertNotEqual(b.inspect_recording(self.iphone)['file_sha256'],
                            b.inspect_recording(self.archive)['file_sha256'])
        self.assertEqual(b.inspect_recording(self.iphone)['packet_sha256'],
                         b.inspect_recording(self.archive)['packet_sha256'])
        again = self.run_file(self.archive)
        self.assertEqual(again['action'], 'reuse')
        self.assertEqual(one['note'], again['note'])
        self.assertEqual(note.read_bytes(), original)
        self.assertEqual(len(self.notes()), 1)

    def test_distinct_uuid_same_packet_two_recordings(self):
        one = self.run_file()
        two = self.run_file(self.other)
        self.assertNotEqual(one['recording_id'], two['recording_id'])
        self.assertEqual(len(self.notes()), 2)

    def test_legacy_mobile_note_reconciles_unique_packet_and_retains_body(self):
        original = self.run_file(self.iphone)
        p = self.vault / original['note']
        src = p.read_text()
        source_sha = b.inspect_recording(self.iphone)['file_sha256']
        self.assertIn('vm-' + UUID1, src)
        # Emulate pre-parser iPhone import with a generic content identity.
        original_id = 'audio-' + source_sha
        raw = src.replace('vm-' + UUID1, original_id)
        p.write_text(raw + '\nHuman correction survives this reconciliation.\n')
        generic = p.with_name('human-renamed.md')
        p.rename(generic)
        matched = self.run_file(self.archive)
        self.assertEqual(matched['action'], 'reuse')
        self.assertEqual(matched['note'], generic.relative_to(self.vault).as_posix())
        final = generic.read_text()
        self.assertIn(f'archive_recording_id: "vm-{UUID1}"', final)
        self.assertIn(f'recording_id: "{original_id}"', final)
        self.assertIn('Human correction survives', final)
        self.assertEqual(len(self.notes()), 1)

    def test_rename_keeps_note_and_transcript(self):
        one = self.run_file()
        note = self.vault / one['note']
        changed = note.with_name('human-title.md')
        note.rename(changed)
        edited = changed.read_text() + '\nEdited by human.\n'
        changed.write_text(edited)
        result = self.run_file(self.archive)
        self.assertEqual(result['action'], 'reuse')
        self.assertEqual(changed.read_text(), edited)

    def test_multiple_generic_packet_matches_require_review(self):
        original = self.run_file()
        note = self.vault / original['note']
        src = note.read_text()
        note.write_text(src.replace('vm-' + UUID1, 'audio-' + b.inspect_recording(self.iphone)['file_sha256']))
        duplicate = note.with_name('different-recording.md')
        duplicate.write_text(note.read_text().replace('recording_id: "audio-', 'recording_id: "other-', 1))
        with self.assertRaisesRegex(ValueError, 'REVIEW_REQUIRED'):
            self.run_file(self.archive)
        self.assertEqual(len(self.notes()), 2)

    def test_dry_run_is_read_only(self):
        report = self.run_file(apply=False)
        self.assertEqual(report['status'], 'planned')
        self.assertEqual(self.notes(), [])

    def test_skip_is_not_pending_for_worker(self):
        result = self.run_file(transcribe='skip')
        note = self.vault / result['note']
        fields, _ = innermaze.parse_note(note.read_text())
        self.assertEqual(fields['transcription_status'], 'skipped')
        self.assertFalse(innermaze.process_one(note, self.vault, Path('/missing'), innermaze.MODEL,
                                                self.empty_index, self.missing_manifest))

    def test_no_global_worker_and_pending_only_selected(self):
        first = self.run_file()
        second = self.run_file(self.other)
        first_note = self.vault / first['note']
        second_note = self.vault / second['note']
        self.assertEqual(innermaze.parse_note(first_note.read_text())[0]['transcription_status'], 'pending')
        self.assertEqual(innermaze.parse_note(second_note.read_text())[0]['transcription_status'], 'pending')
        with mock.patch.object(e, 'transcribe_one', return_value='ready') as targeted:
            outcome = e.run_note(first_note.relative_to(self.vault), self.vault, apply=True,
                 model_python=Path('/missing'), whisper_model=innermaze.MODEL,
                 whisper_root=self.empty_index, manifest_db=self.missing_manifest,
                 classify='skip', qwen_model='test', server='http://127.0.0.1:1234/v1')
        targeted.assert_called_once()
        self.assertEqual(outcome['note'], first_note.relative_to(self.vault).as_posix())
        self.assertEqual(innermaze.parse_note(second_note.read_text())[0]['transcription_status'], 'pending')

    def test_classification_after_ready_and_failure_preserves_note(self):
        first = self.run_file()
        note = self.vault / first['note']
        before = note.read_bytes()
        with mock.patch.object(e, 'transcribe_one', return_value='ready'), \
             mock.patch.object(e, 'classify_one', return_value={'status':'failed','error':'model unavailable'}) as classify:
            result = self.run_file(self.archive, transcribe='now', classify='after')
        classify.assert_called_once()
        self.assertEqual(result['classification']['status'], 'failed')
        self.assertEqual(note.read_bytes(), before)

    def test_unready_transcript_never_calls_qwen(self):
        with mock.patch.object(e, 'classify_one') as classifier:
            result = self.run_file(classify='after', transcribe='later')
        classifier.assert_not_called()
        self.assertEqual(result['classification'], 'deferred_not_scheduled_no_ready_transcript')

    def test_vault_alias_paths_normalize_before_result_relativization(self):
        # macOS often exposes /var/... as /private/var/...; exercise equivalent
        # directory aliases without relying on the host's particular /var map.
        alias = self.root / 'Cabinet-symlink'
        alias.symlink_to(self.vault, target_is_directory=True)
        initial = self.run_file(vault=alias)
        again = self.run_file(self.archive, vault=alias)
        self.assertEqual(initial['note'], again['note'])
        self.assertEqual(again['action'], 'reuse')
        self.assertEqual(len(self.notes()), 1)
        existing = e.run_note(Path(initial['note']), alias, apply=False,
            model_python=Path('/missing'), whisper_model=innermaze.MODEL,
            whisper_root=self.empty_index, manifest_db=self.missing_manifest,
            classify='skip', qwen_model='test', server='http://127.0.0.1:1234/v1')
        self.assertEqual(existing['note'], initial['note'])

    def test_path_traversal_denied(self):
        self.run_file()
        with self.assertRaisesRegex(ValueError, 'vault-relative'):
            e.checked_note(self.vault, Path('../escape.md'))
        with self.assertRaisesRegex(ValueError, 'verified'):
            e.checked_note(self.vault, Path('Voice Archive/absent.md'))


def main() -> None:
    unittest.main()


if __name__ == '__main__':
    main()
