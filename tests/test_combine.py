#!/usr/bin/env python3
# filename: test_combine.py
# version: 0.1.0
# timestamp: 2026-11-06 15:37:00
"""Real FFmpeg combinations with immutable source hashes and fresh transcript timing."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import innermaze_combine as combine


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class CombineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / 'Cabinet'
        (self.vault / '.obsidian').mkdir(parents=True)
        self.scratch = self.root / 'Scratch'
        self.items = []
        for n, uid in enumerate(('01234567-1234-4234-8234-1234567890AB',
                                 '11234567-1234-4234-8234-1234567890AB',
                                 '21234567-1234-4234-8234-1234567890AB')):
            recording_id = f'vm-{uid}'
            audio = self.vault / 'Voice Archive/Audio' / (f'source-{n}.m4a')
            audio.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi',
                            '-i',f'sine=frequency={440+n*150}:duration=0.9',
                            '-c:a','aac','-y',str(audio)],check=True)
            note = self.vault / 'Voice Archive' / f'Part {n+1}.md'
            note.parent.mkdir(parents=True,exist_ok=True)
            note.write_text('\n'.join(['---','voice_archive: true',
                f'recording_id: "{recording_id}"','recording_source: "apple-voice-memo"',
                f'display_title: "Part {n+1}"',
                f'voice_audio: "{audio.relative_to(self.vault).as_posix()}"',
                f'voice_sync: "Voice Archive/Sync/{recording_id}.sync.json"',
                f'source_sha256: "{sha(audio)}"',f'playback_sha256: "{sha(audio)}"',
                'transcription_status: "ready"','tags:','  - voice-archive','---','',
                'Human-edited transcript never changes.','']),encoding='utf-8')
            self.items.append({'note':note.relative_to(self.vault).as_posix(),
                               'trim_start':0,'trim_end':0})
        self.immutable = {str(path):sha(path) for path in self.vault.rglob('*') if path.is_file()}

    def run_combine(self, *, items=None, apply=True, title='Two stopped takes'):
        return combine.run(self.vault, items or self.items[:2], title, apply=apply,
            scratch=self.scratch)

    def assert_sources_unchanged(self):
        self.assertEqual(self.immutable,
            {str(Path(key)):sha(Path(key)) for key in self.immutable})

    def test_dry_run_is_read_only(self):
        planned = self.run_combine(apply=False)
        self.assertEqual(planned['status'],'planned')
        self.assertFalse((self.vault/planned['note']).exists())
        self.assertFalse(self.scratch.exists())
        self.assert_sources_unchanged()

    def test_join_playable_one_note_provenance_pending_and_reuse(self):
        first = self.run_combine()
        self.assertEqual(first['status'],'combined')
        combined = self.vault / first['note']
        audio = self.vault / first['audio']
        raw = combined.read_text('utf-8')
        self.assertIn('transcription_status: "pending"', raw)
        self.assertNotIn('Human-edited transcript', raw)
        self.assertEqual(combine.note_fields(raw)['recording_id'], first['recording_id'])
        self.assertTrue(audio.is_file())
        self.assertNotIn('vm-', first['recording_id'])
        self.assertAlmostEqual(float(combine.duration_seconds(audio, shutil.which('ffprobe'))),1.8,delta=.12)
        meta = json.loads((self.vault / first['provenance']).read_text('utf-8'))
        self.assertEqual([p['recording_id'] for p in meta['sources']],
            ['vm-01234567-1234-4234-8234-1234567890AB',
             'vm-11234567-1234-4234-8234-1234567890AB'])
        self.assertEqual(len(meta['estimated_join_boundaries_seconds']),1)
        self.assertAlmostEqual(meta['estimated_join_boundaries_seconds'][0],.9,places=1)
        contents = combined.read_bytes()
        audio_hash = sha(audio)
        second = self.run_combine(title='Different display title should not rename')
        self.assertEqual(second['status'],'reused')
        self.assertEqual(second['recording_id'],first['recording_id'])
        self.assertEqual(combined.read_bytes(),contents)
        self.assertEqual(sha(audio),audio_hash)
        self.assert_sources_unchanged()

    def test_reorder_trim_changes_identity_and_length(self):
        result = self.run_combine(items=[self.items[1],
            dict(self.items[0],trim_start=.2,trim_end=.15)])
        other = self.run_combine(items=[self.items[0],self.items[1]],title='Another combined memo')
        self.assertNotEqual(result['recording_id'],other['recording_id'])
        self.assertAlmostEqual(result['duration_seconds'],1.45,delta=.15)
        meta=json.loads((self.vault/result['provenance']).read_text('utf-8'))
        self.assertAlmostEqual(meta['sources'][1]['trim_start_seconds'],.2,places=2)
        self.assertAlmostEqual(meta['sources'][1]['trim_end_seconds'],.15,places=2)
        self.assert_sources_unchanged()

    def test_duplicate_or_invalid_input_rejected(self):
        with self.assertRaisesRegex(ValueError,'cannot appear twice'):
            self.run_combine(items=[self.items[0],self.items[0]])
        with self.assertRaisesRegex(ValueError,'remove entire'):
            self.run_combine(items=[self.items[0],dict(self.items[1],trim_start=.8,trim_end=.3)])
        with self.assertRaisesRegex(ValueError,'vault-relative'):
            self.run_combine(items=[self.items[0],{'note':'../../Dropbox/file.md'}])
        self.assert_sources_unchanged()

    def test_unavailable_or_corrupt_playback_refuses_new_output(self):
        note=self.vault/self.items[1]['note']
        raw=note.read_text('utf-8')
        fields=combine.note_fields(raw)
        audio=self.vault/fields['voice_audio']
        audio.unlink()
        with self.assertRaises((OSError,ValueError)):
            self.run_combine()
        self.assertEqual(len(list((self.vault/'Voice Archive').glob('Two stopped takes.md'))),0)
        self.assertEqual(note.read_text('utf-8'),raw)

    def test_existing_human_note_edit_is_preserved(self):
        first=self.run_combine()
        note=self.vault/first['note']
        renamed=note.with_name('Human-renamed combined memo.md')
        note.rename(renamed)
        renamed.write_text(renamed.read_text('utf-8')+'\nHuman-written text in combined note.\n','utf-8')
        second=self.run_combine()
        self.assertEqual(second['status'],'reused')
        self.assertEqual(second['note'],renamed.relative_to(self.vault).as_posix())
        self.assertIn('Human-written text',renamed.read_text('utf-8'))


def main() -> None:
    unittest.main()

if __name__ == '__main__':
    main()
