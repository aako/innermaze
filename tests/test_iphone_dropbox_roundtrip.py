#!/usr/bin/env python3
# filename: test_iphone_dropbox_roundtrip.py
# version: 0.2.0
# timestamp: 2026-10-27 14:38:00
"""Synthetic iPhone->Dropbox identity regression against Agent E's Python planner."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
try:
    import recording_identity as identity  # Agent E's actual module in repository worktree
except ModuleNotFoundError:
    import recording_identity_reference as identity  # Pinned source-only test fixture

UUID1 = '0A25FD21-78E9-4BC1-ABFA-68572D41C59E'
UUID2 = '1A25FD21-78E9-4BC1-ABFA-68572D41C59E'


def run(*args: str) -> None:
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)], check=True)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg and ffprobe required')
class IphoneToDropboxRegression(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix='agent-b-roundtrip-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.vault = self.base / 'Cabinet'
        (self.vault / '.obsidian').mkdir(parents=True)
        self.audio_dir = self.vault / 'Voice Archive' / 'Audio'
        self.audio_dir.mkdir(parents=True)
        self.source = self.base / 'source.m4a'
        self.iphone = self.base / 'iphone.m4a'
        self.archive = self.base / 'archive.m4a'
        self.other = self.base / 'other.m4a'
        self.edited = self.base / 'edited.m4a'
        run('-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.35', '-c:a', 'aac', self.source)
        self.remux(self.source, self.iphone, UUID1, 'Phone Name')
        self.remux(self.iphone, self.archive, UUID1, 'Archive Renamed')
        self.remux(self.iphone, self.other, UUID2, 'Independent same-audio Voice Memo')
        run('-f', 'lavfi', '-i', 'sine=frequency=880:duration=0.35', '-c:a', 'aac',
            '-movflags', 'use_metadata_tags', '-metadata', f'voice-memo-uuid={UUID1}', self.edited)
        self.file_sha = digest(self.iphone)
        self.note = self.vault / 'Voice Archive' / 'Manually renamed by human.md'
        self.playback = self.audio_dir / f'sha256-{self.file_sha}.m4a'
        shutil.copyfile(self.iphone, self.playback)
        self.note.write_text('\n'.join([
            '---', 'voice_archive: true', f'recording_id: "vm-{UUID1}"',
            'display_title: "An edited private title"',
            f'source_sha256: "{self.file_sha}"', f'playback_sha256: "{self.file_sha}"',
            f'voice_audio: "Voice Archive/Audio/{self.playback.name}"',
            f'voice_sync: "Voice Archive/Sync/vm-{UUID1}.sync.json"',
            'transcription_status: "ready"', '---', '', 'Human-edited transcript with preserved words.', ''
        ]), encoding='utf-8')

    @staticmethod
    def remux(src: Path, dst: Path, uid: str, title: str) -> None:
        run('-i', str(src), '-map', '0:a:0', '-c:a', 'copy', '-movflags', 'use_metadata_tags',
            '-metadata', f'voice-memo-uuid={uid}', '-metadata', f'title={title}', str(dst))

    @unittest.skipUnless(shutil.which('node'), 'Node.js required for cross-language metadata test')
    def test_javascript_parser_reads_actual_ffmpeg_m4a_uuid(self):
        js = ('const fs=require("node:fs");'
              'const mod=require(process.argv[1]);'
              'console.log(mod.parseAppleVoiceMemoUuid(fs.readFileSync(process.argv[2])));')
        module = TOOLS / 'mobile_recording_identity.cjs'
        for recording, expected in ((self.iphone, UUID1), (self.archive, UUID1),
                                    (self.other, UUID2)):
            result = subprocess.run(['node', '-e', js, str(module), str(recording)],
                                    check=True, text=True, capture_output=True)
            self.assertEqual(result.stdout.strip(), expected)

    def test_same_apple_uuid_and_aac_different_mp4_reuses_original_edited_note(self):
        first = identity.inspect_recording(self.iphone)
        second = identity.inspect_recording(self.archive)
        self.assertNotEqual(first['file_sha256'], second['file_sha256'])
        self.assertEqual(first['packet_sha256'], second['packet_sha256'])
        before = self.note.read_bytes()
        result = identity.plan_archive_file(self.archive, self.vault)
        self.assertEqual(result['reconciliation']['action'], 'reuse_note')
        self.assertEqual(result['reconciliation']['existing_recording_id'], f'vm-{UUID1}')
        self.assertEqual(result['reconciliation']['note_path'], str(self.note))
        self.assertEqual(self.note.read_bytes(), before)

    def test_distinct_apple_uuids_identical_audio_never_merge(self):
        self.assertEqual(identity.inspect_recording(self.other)['packet_sha256'],
                         identity.inspect_recording(self.iphone)['packet_sha256'])
        result = identity.plan_archive_file(self.other, self.vault)
        self.assertEqual(result['reconciliation']['action'], 'create_note')
        self.assertEqual(result['reconciliation']['recording_id'], f'vm-{UUID2}')

    def test_same_uuid_changed_audio_version_requires_review(self):
        result = identity.plan_archive_file(self.edited, self.vault)
        self.assertEqual(result['reconciliation']['action'], 'review_required')
        self.assertEqual(result['reconciliation']['reason'], 'same_uuid_different_audio_version')


def main() -> None:
    unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
