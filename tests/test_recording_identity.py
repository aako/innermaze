#!/usr/bin/env python3
# filename: test_recording_identity.py
# version: 0.1.0
# timestamp: 2026-10-27 10:25:00
"""Synthetic-only tests; requires ffmpeg/ffprobe. No real archive or vault writes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

FILE = Path(__file__).resolve().parents[1] / 'tools' / 'recording_identity.py'
spec = importlib.util.spec_from_file_location('recording_identity', FILE)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
UUID1 = '12345678-1234-4234-8234-1234567890AB'
UUID2 = '22345678-1234-4234-8234-1234567890AB'


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe unavailable')
class RecordingIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.raw = cls.root / 'raw.m4a'
        cls.ios = cls.root / 'ios.m4a'
        cls.archive = cls.root / 'archived-renamed.m4a'
        cls.other = cls.root / 'different-uuid.m4a'
        cls.anonymous = cls.root / 'anonymous.m4a'
        cls.edited = cls.root / 'edited.m4a'
        cls.vault = cls.root / 'Cabinet'
        (cls.vault / '.obsidian').mkdir(parents=True)
        (cls.vault / 'Voice Archive' / 'Audio').mkdir(parents=True)
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
               '-i', 'sine=frequency=440:duration=0.5', '-c:a', 'aac', cls.raw.as_posix()]
        subprocess.run(cmd, check=True)
        cls.remux(cls.raw, cls.ios, UUID1, '2026-10-26T23:21:56Z')
        cls.remux(cls.ios, cls.archive, UUID1, '2026-10-26T23:23:19Z')
        cls.remux(cls.ios, cls.other, UUID2, '2026-10-26T23:23:19Z')
        cls.remux(cls.ios, cls.anonymous, None, '2026-10-26T23:23:19Z')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=880:duration=0.5', '-c:a', 'aac',
                        cls.edited.as_posix()], check=True)
        cls.remux(cls.edited, cls.root / 'edited-tag.m4a', UUID1, '2026-10-26T23:23:19Z')
        cls.edited = cls.root / 'edited-tag.m4a'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def remux(source, destination, apple_uuid, timestamp):
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                '-map', '0:a:0', '-c', 'copy', '-movflags', 'use_metadata_tags',
                '-metadata', f'creation_time={timestamp}', '-metadata', 'title=Renamed']
        if apple_uuid:
            args += ['-metadata', f'voice-memo-uuid={apple_uuid}']
        else:
            args += ['-map_metadata', '-1']
        subprocess.run(args + [str(destination)], check=True)

    def setUp(self):
        for child in (self.vault / 'Voice Archive').glob('*.md'):
            child.unlink()
        for child in (self.vault / 'Voice Archive/Audio').glob('*'):
            child.unlink()

    def note(self, rec_id, source, audio=None, alias=None, name=None):
        fields = ['---', 'voice_archive: true', f'recording_id: "{rec_id}"',
                  f'source_sha256: "{source["file_sha256"]}"',
                  f'playback_sha256: "{source["file_sha256"]}"',
                  'display_title: "Human-edited title"']
        if alias:
            fields.append(f'archive_recording_id: "{alias}"')
        if audio:
            path = self.vault / 'Voice Archive/Audio' / (name or 'listening.m4a')
            shutil.copyfile(audio, path)
            fields.append(f'voice_audio: "Voice Archive/Audio/{path.name}"')
        p = self.vault / 'Voice Archive' / (name or 'renamed-by-human')
        p = p.with_suffix('.md')
        p.write_text('\n'.join(fields + ['---', '', 'A human transcript that must not change.']) + '\n')
        return p

    def test_uuid_and_packet_hash_survive_container_changes(self):
        a = identity.inspect_recording(self.ios)
        b = identity.inspect_recording(self.archive)
        self.assertEqual(a['apple_uuid'], UUID1)
        self.assertEqual(b['apple_uuid'], UUID1)
        self.assertEqual(a['packet_sha256'], b['packet_sha256'])
        self.assertNotEqual(a['file_sha256'], b['file_sha256'])

    def test_reuse_uuid_note_across_remux_without_mutation(self):
        a = identity.inspect_recording(self.ios)
        b = identity.inspect_recording(self.archive)
        p = self.note('vm-' + UUID1, a, self.ios)
        initial = p.read_bytes()
        plan = identity.reconcile_recording(b, self.vault)
        self.assertEqual(plan['action'], 'reuse_note')
        self.assertEqual(plan['note_path'], str(p))
        self.assertEqual(plan['existing_recording_id'], 'vm-' + UUID1)
        self.assertEqual(p.read_bytes(), initial)

    def test_other_uuid_same_audio_is_distinct_recording(self):
        a = identity.inspect_recording(self.ios)
        b = identity.inspect_recording(self.other)
        self.note('vm-' + UUID1, a, self.ios)
        plan = identity.reconcile_recording(b, self.vault)
        self.assertEqual(plan['action'], 'create_note')
        self.assertEqual(plan['recording_id'], 'vm-' + UUID2)

    def test_uuid_same_recording_edited_audio_holds(self):
        a = identity.inspect_recording(self.ios)
        b = identity.inspect_recording(self.edited)
        self.note('vm-' + UUID1, a, self.ios)
        plan = identity.reconcile_recording(b, self.vault)
        self.assertEqual(plan['reason'], 'same_uuid_different_audio_version')
        self.assertEqual(plan['action'], 'review_required')

    def test_legacy_hash_note_packet_match_requires_review(self):
        a = identity.inspect_recording(self.ios)
        b = identity.inspect_recording(self.archive)
        self.note('audio-' + a['file_sha256'], a, self.ios)
        plan = identity.reconcile_recording(b, self.vault)
        self.assertEqual(plan['reason'], 'possible_legacy_hash_note')
        self.assertEqual(plan['action'], 'review_required')

    def test_incomplete_icloud_legacy_audio_blocks_new_uuid_note(self):
        a = identity.inspect_recording(self.anonymous)
        b = identity.inspect_recording(self.archive)
        self.note('audio-' + a['file_sha256'], a)
        plan = identity.reconcile_recording(b, self.vault)
        self.assertEqual(plan['action'], 'review_required')
        self.assertEqual(plan['reason'], 'legacy_note_audio_unavailable')

    def test_missing_uuid_exact_anonymous_repeat_can_reuse(self):
        a = identity.inspect_recording(self.anonymous)
        self.assertIsNone(a['apple_uuid'])
        p = self.note('audio-' + a['file_sha256'], a, self.anonymous)
        plan = identity.reconcile_recording(a, self.vault)
        self.assertEqual(plan['action'], 'reuse_note')
        self.assertEqual(plan['note_path'], str(p))

    def test_same_uuid_two_notes_is_conflict(self):
        a = identity.inspect_recording(self.ios)
        self.note('vm-' + UUID1, a, self.ios, name='one')
        self.note('audio-' + a['file_sha256'], a, self.ios, alias='vm-' + UUID1, name='two')
        plan = identity.reconcile_recording(a, self.vault)
        self.assertEqual(plan['reason'], 'duplicate_apple_uuid_binding')

    def test_invalid_uuid_rejected_without_filename_fallback(self):
        with self.assertRaises(ValueError):
            identity.normalize_uuid('12345678_1234_4234_8234_1234567890AB')

    def test_new_archive_entry_no_writes_and_iterator(self):
        result = identity.plan_archive_file(self.ios, self.vault)
        self.assertEqual(result['reconciliation']['action'], 'create_note')
        self.assertEqual(result['candidate']['apple_uuid'], UUID1)
        self.assertEqual(len(list((self.vault / 'Voice Archive').glob('*.md'))), 0)
        paths = list(identity.iter_archive_paths(self.root))
        self.assertIn(self.ios, paths)

    def test_existing_uuid_alias_reuses_recording_id(self):
        a = identity.inspect_recording(self.ios)
        b = identity.inspect_recording(self.archive)
        p = self.note('audio-' + a['file_sha256'], a, self.ios, alias='vm-' + UUID1)
        plan = identity.reconcile_recording(b, self.vault)
        self.assertEqual(plan['action'], 'reuse_note')
        self.assertEqual(plan['existing_recording_id'], 'audio-' + a['file_sha256'])
        self.assertEqual(plan['note_path'], str(p))


if __name__ == '__main__':
    unittest.main()
