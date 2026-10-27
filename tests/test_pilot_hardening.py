#!/usr/bin/env python3
# filename: test_pilot_hardening.py
# version: 0.1.0
# timestamp: 2026-10-27 12:20:00
"""Narrow synthetic Mango hardening regressions (no live vault, no model)."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import innermaze
import innermaze_import_process as runner
import test_integration_agent_e as base


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg required')
class PilotHardeningTests(base.IntegrationTests):
    def test_pilot_reused_uuid_missing_playback_is_blocked_and_preserved(self):
        created = self.run_file()
        note = self.vault / created['note']
        fields, _ = innermaze.parse_note(note.read_text('utf-8'))
        audio = self.vault / fields['voice_audio']
        audio.unlink()
        before = note.read_bytes()
        with mock.patch.object(runner, 'attach_uuid_alias', side_effect=AssertionError('must not mutate')):
            response = self.run_file(self.iphone, transcribe='now', classify='after')
        self.assertEqual(response['status'], 'blocked')
        self.assertEqual(response['reason'], 'playback_attachment_unavailable')
        self.assertEqual(note.read_bytes(), before)
        self.assertEqual(len(self.notes()), 1)
        self.assertFalse(audio.exists())

    def test_pilot_reused_playback_hash_mismatch_is_blocked(self):
        created = self.run_file()
        note = self.vault / created['note']
        fields, _ = innermaze.parse_note(note.read_text('utf-8'))
        audio = self.vault / fields['voice_audio']
        audio.write_bytes(b'broken partial iCloud placeholder')
        before = note.read_bytes()
        response = self.run_file(self.iphone)
        self.assertEqual(response['status'], 'blocked')
        self.assertEqual(response['reason'], 'playback_hash_mismatch')
        self.assertEqual(note.read_bytes(), before)

    def test_pilot_staged_filename_and_source_not_claimed_dropbox(self):
        with mock.patch.dict(innermaze.SOURCES, {'voice-memos': self.root}, clear=True):
            response = self.run_file(source_filename='A real dream — recorded.m4a', staged_input=True)
        note = self.vault / response['note']
        fields, _ = innermaze.parse_note(note.read_text('utf-8'))
        self.assertEqual(fields['source_title'], 'A real dream — recorded.m4a')
        self.assertEqual(fields['recording_source'], 'arbitrary-file')

    def test_pilot_native_path_recognizes_verified_source_root(self):
        with mock.patch.dict(innermaze.SOURCES, {'voice-memos': self.root}, clear=True):
            response = self.run_file(source_filename='Voice Memo Original.m4a', staged_input=False)
        fields, _ = innermaze.parse_note((self.vault / response['note']).read_text('utf-8'))
        self.assertEqual(fields['source_title'], 'Voice Memo Original.m4a')
        self.assertEqual(fields['recording_source'], 'voice-memos')

    def test_pilot_source_filename_path_traversal_fails_before_import(self):
        with self.assertRaisesRegex(ValueError, 'safe original file basename'):
            self.run_file(source_filename='../wrong.m4a')
        self.assertEqual(len(self.notes()), 0)

    def test_pilot_existing_ready_can_classify_later_without_whisper(self):
        created = self.run_file()
        note = self.vault / created['note']
        raw = note.read_text('utf-8').replace('transcription_status: "pending"',
                                               'transcription_status: "ready"')
        note.write_text(raw + '\nA completed synthetic transcript.\n', encoding='utf-8')
        before = note.read_bytes()
        with mock.patch.object(runner, 'transcribe_one', side_effect=AssertionError('redundant Whisper')), \
             mock.patch.object(runner, 'classify_one', return_value={'status': 'classified', 'run_id':'synthetic'}) as classifier:
            result = self.run_file(self.archive, transcribe='later', classify='after')
        classifier.assert_called_once()
        self.assertEqual(result['classification']['status'], 'classified')
        self.assertEqual(note.read_bytes(), before)

    def test_pilot_existing_ready_now_skips_process_one(self):
        created = self.run_file()
        note = self.vault / created['note']
        raw = note.read_text('utf-8').replace('transcription_status: "pending"',
                                               'transcription_status: "ready"')
        note.write_text(raw + '\nAlready transcribed.\n', encoding='utf-8')
        with mock.patch.object(innermaze, 'process_one', side_effect=AssertionError('Whisper reran')):
            result = self.run_file(self.archive, transcribe='now', classify='skip')
        self.assertEqual(result['transcription'], 'ready')


def load_tests(loader, suite, pattern):
    # Import the existing fixture setup without repeating its twelve assertions.
    return unittest.TestSuite(PilotHardeningTests(name) for name in
        loader.getTestCaseNames(PilotHardeningTests) if name.startswith('test_pilot_'))

if __name__ == '__main__':
    unittest.main()
