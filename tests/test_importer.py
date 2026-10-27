#!/usr/bin/env python3
# filename: test_importer.py
# version: 0.1.3
# timestamp: 2026-10-24 16:54:00
from __future__ import annotations
import hashlib
import csv
import json
import sqlite3
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('innermaze', ROOT / 'tools/innermaze.py')
innermaze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(innermaze)

class ImporterTests(unittest.TestCase):
    def test_default_migrated_paths_never_reference_work_temp(self):
        self.assertEqual(innermaze.WHISPER, innermaze.HOME / 'Code/VoiceMemoSaver/Whisper')
        self.assertEqual(self.original_temp, innermaze.HOME / 'Code/.staging/Innermaze')
        self.assertNotIn('Work/Temp', str(innermaze.WHISPER))
        self.assertNotIn('Work/Temp', str(self.original_temp))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.vault = self.root / 'Vault'
        (self.vault / '.obsidian').mkdir(parents=True)
        self.sample = self.root / 'example.m4a'
        self.sample.write_bytes(b'Voice Archive fake audio; bytes preserved\x00\x01')
        self.digest = hashlib.sha256(self.sample.read_bytes()).hexdigest()
        self.original_temp = innermaze.TEMP_DIR
        self.original_sources = innermaze.SOURCES
        self.vm = self.root / 'Voice Memos Full DB'
        self.jpr = self.root / 'Just Press Record Full DB'
        self.vm.mkdir()
        self.jpr.mkdir()
        innermaze.SOURCES = {'voice-memos': self.vm, 'just-press-record': self.jpr}
        self.whisper_root = self.root / 'Whisper'
        self.manifest = self.root / 'manifest.sqlite3'
        innermaze.TEMP_DIR = self.root / 'Temp'
        self.mock_python = self.root / 'mock-env'
        self.mock_python.mkdir()
        (self.mock_python / 'mlx_whisper.py').write_text(
            'def transcribe(source, **kwargs):\n'
            '    return {"segments": [{"start": 0.0, "end": 1.7, "text": "Hi from Mango.", '\
            '"words": [{"start":0.0,"end":0.5,"word":"Hi"}, '\
            '{"start":0.6,"end":0.9,"word":" from"}, '\
            '{"start":1.0,"end":1.7,"word":" Mango."}]}]}\n', encoding='utf-8')
        self.old_pythonpath = os.environ.get('PYTHONPATH')
        os.environ['PYTHONPATH'] = str(self.mock_python) + (os.pathsep + self.old_pythonpath if self.old_pythonpath else '')

    def tearDown(self):
        innermaze.TEMP_DIR = self.original_temp
        innermaze.SOURCES = self.original_sources
        if self.old_pythonpath is None:
            os.environ.pop('PYTHONPATH', None)
        else:
            os.environ['PYTHONPATH'] = self.old_pythonpath
        self.directory.cleanup()

    def imported(self):
        return self.vault / 'Voice Archive' / f'audio-{self.digest}.md'

    def test_import_dry_run_creates_nothing(self):
        result = innermaze.import_any(self.sample, self.vault, False, False, Path(sys.executable), innermaze.MODEL)
        self.assertIsNone(result)
        self.assertFalse((self.vault / 'Voice Archive').exists())

    def test_import_reimport_and_notes_never_overwritten(self):
        note = innermaze.import_any(self.sample, self.vault, True, False, Path(sys.executable), innermaze.MODEL)
        self.assertEqual(note, self.imported())
        fields, body = innermaze.parse_note(note.read_text())
        self.assertEqual(body.strip(), '')
        self.assertEqual(fields['source_sha256'], self.digest)
        asset = self.vault / fields['voice_audio']
        self.assertEqual(asset.read_bytes(), self.sample.read_bytes())
        updated = note.read_text() + '\nHuman correction!\n'
        note.write_text(updated)
        again = innermaze.import_any(self.sample, self.vault, True, False, Path(sys.executable), innermaze.MODEL)
        self.assertEqual(again, note)
        self.assertEqual(note.read_text(), updated)
        self.assertEqual(len(list((self.vault / 'Voice Archive/Audio').iterdir())), 1)

    def test_worker_word_timestamps_and_resume(self):
        note = innermaze.import_any(self.sample, self.vault, True, False, Path(sys.executable), innermaze.MODEL)
        innermaze.process_pending(self.vault, Path(sys.executable), innermaze.MODEL, 5)
        fields, body = innermaze.parse_note(note.read_text())
        self.assertEqual(fields['transcription_status'], 'ready')
        self.assertEqual(body.strip(), 'Hi from Mango.')
        sync = self.vault / fields['voice_sync']
        data = __import__('json').loads(sync.read_text())
        self.assertEqual(data['recording_id'], f'audio-{self.digest}')
        self.assertEqual(len(data['words']), 3)
        self.assertEqual(data['blocks'][0]['start'], 0.0)
        edited = note.read_text().replace('Hi from Mango.', 'Hi, corrected on my iPhone!')
        note.write_text(edited)
        innermaze.process_pending(self.vault, Path(sys.executable), innermaze.MODEL, 5)
        self.assertEqual(note.read_text(), edited)
        self.assertEqual(sync.read_text(), sync.read_text())

    def test_edited_pending_notes_never_overwritten(self):
        note = innermaze.import_any(self.sample, self.vault, True, False, Path(sys.executable), innermaze.MODEL)
        note.write_text(note.read_text() + 'A human has already edited this note.\n')
        innermaze.process_pending(self.vault, Path(sys.executable), innermaze.MODEL, 5)
        fields, body = innermaze.parse_note(note.read_text())
        self.assertEqual(fields['transcription_status'], 'pending')
        self.assertIn('A human has already edited', body)
        self.assertFalse((self.vault / fields['voice_sync']).exists())

    def test_reimport_renamed_root_note_uses_original_recording_id(self):
        note = innermaze.import_any(self.sample, self.vault, True, False,
                                    Path(sys.executable), innermaze.MODEL)
        renamed = note.with_name('Dream - Chris Wiley sushi restaurant.md')
        note.rename(renamed)
        original = renamed.read_text()
        renamed.write_text(original.replace('display_title: "example"',
                                            'display_title: "Dream - Chris Wiley sushi restaurant"'))
        result = innermaze.import_any(self.sample, self.vault, True, False,
                                      Path(sys.executable), innermaze.MODEL)
        self.assertEqual(result, renamed)
        self.assertFalse(note.exists())
        self.assertEqual(len(innermaze.recording_notes(self.vault)), 1)
        innermaze.process_pending(self.vault, Path(sys.executable), innermaze.MODEL, 2)
        fields, body = innermaze.parse_note(renamed.read_text())
        self.assertEqual(fields['recording_id'], f'audio-{self.digest}')
        self.assertEqual(fields['transcription_status'], 'ready')
        self.assertEqual(body.strip(), 'Hi from Mango.')

    def test_legacy_note_folder_reused_after_rename(self):
        note = innermaze.import_any(self.sample, self.vault, True, False,
                                    Path(sys.executable), innermaze.MODEL)
        legacy = self.vault / 'Voice Archive/Notes'
        legacy.mkdir()
        moved = legacy / 'A human title.md'
        note.rename(moved)
        again = innermaze.import_any(self.sample, self.vault, True, False,
                                     Path(sys.executable), innermaze.MODEL)
        self.assertEqual(again, moved)
        self.assertFalse(note.exists())

    def test_unrelated_markdown_in_archive_root_is_not_a_recording(self):
        root = self.vault / 'Voice Archive'
        root.mkdir()
        (root / 'Some unrelated note.md').write_text('# No YAML\nJust notes.\n')
        note = innermaze.import_any(self.sample, self.vault, True, False,
                                    Path(sys.executable), innermaze.MODEL)
        self.assertEqual(note, self.imported())
        self.assertEqual(len(innermaze.recording_notes(self.vault)), 1)

    def test_existing_vault_audio_does_not_duplicate(self):
        internal = self.vault / 'Imported Audio.m4a'
        internal.write_bytes(self.sample.read_bytes())
        innermaze.import_any(internal, self.vault, True, False, Path(sys.executable), innermaze.MODEL)
        self.assertFalse((self.vault / 'Voice Archive/Audio').exists())
        fields, _ = innermaze.parse_note(self.imported().read_text())
        self.assertEqual(fields['voice_audio'], 'Imported Audio.m4a')

    def indexed(self, kind='voice-memos', stem='old-title', source_path=None, words=None):
        folder = self.whisper_root / kind
        folder.mkdir(parents=True, exist_ok=True)
        index = folder / 'identity.tsv'
        row = {'source_path': str(source_path or self.sample), 'source_size': str(self.sample.stat().st_size),
               'source_mtime_ns': '0', 'sha256': self.digest, 'output_stem': stem, 'observed_at': '2026-10-01'}
        with index.open('a', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(row), delimiter='\t')
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(row)
        segments = [{'start': 2.0, 'end': 3.4, 'text': ' Existing transcript.',
                     'words': words or [{'start': 2.0, 'end': 2.7, 'word': ' Existing'},
                                        {'start': 2.8, 'end': 3.4, 'word': ' transcript.'}]}]
        (folder / (stem + '.json')).write_text(json.dumps({'segments': segments}), encoding='utf-8')
        return row

    def apple_manifest(self, entries):
        with sqlite3.connect(self.manifest) as db:
            db.executescript("""
            CREATE TABLE objects(archive TEXT, dest_rel TEXT, sha256 TEXT);
            CREATE TABLE object_recordings(archive TEXT, dest_rel TEXT, logical_id TEXT);
            """)
            for rel, uid in entries:
                db.execute('INSERT INTO objects VALUES (?,?,?)', ('voice_memos', rel, self.digest))
                db.execute('INSERT INTO object_recordings VALUES (?,?,?)',
                           ('voice_memos', rel, f'voice-memos:uuid:{uid}'))

    def test_archive_file_uses_cached_word_timestamps_without_running_mlx(self):
        renamed = self.vm / '2026-09-03 07-52-07 - Dream - film photo shoot.m4a'
        renamed.write_bytes(self.sample.read_bytes())
        self.indexed(source_path=self.vm / 'original-memo-name.m4a')
        self.apple_manifest([(renamed.name, 'UUID-ALPHA')])
        missing_mlx = self.root / 'does-not-exist'
        note = innermaze.import_any(renamed, self.vault, True, True, missing_mlx,
                                    innermaze.MODEL, self.whisper_root, self.manifest)
        self.assertEqual(note.name, 'vm-UUID-ALPHA.md')
        fields, body = innermaze.parse_note(note.read_text())
        self.assertEqual(fields['transcription_status'], 'ready')
        self.assertEqual(body.strip(), 'Existing transcript.')
        sidecar = json.loads((self.vault / fields['voice_sync']).read_text())
        self.assertEqual([w['start'] for w in sidecar['words']], [2.0, 2.8])
        self.assertEqual(sidecar['source_whisper_stem'], 'old-title')
        self.assertEqual(sidecar['recording_id'], 'vm-UUID-ALPHA')
        reread = note.read_text().replace('Existing transcript.', 'Existing corrected transcript.')
        note.write_text(reread)
        again = innermaze.import_any(renamed, self.vault, True, True, missing_mlx,
                                     innermaze.MODEL, self.whisper_root, self.manifest)
        self.assertEqual(note, again)
        self.assertEqual(note.read_text(), reread)
        self.assertEqual(len(list((self.vault / 'Voice Archive/Audio').iterdir())), 1)

    def test_mobile_pending_import_reuses_cached_transcript_on_mango(self):
        vault_source = self.vault / 'recording.m4a'
        vault_source.write_bytes(self.sample.read_bytes())
        note = innermaze.import_any(vault_source, self.vault, True, False, Path('/no-mlx'),
                                    innermaze.MODEL, self.whisper_root, self.manifest)
        fields, body = innermaze.parse_note(note.read_text())
        self.assertEqual(fields['transcription_status'], 'pending')
        self.indexed(kind='just-press-record', stem='phone-capture')
        innermaze.process_pending(self.vault, Path('/no-mlx'), innermaze.MODEL, 2,
                                  self.whisper_root, self.manifest)
        fields, body = innermaze.parse_note(note.read_text())
        self.assertEqual(fields['transcription_status'], 'ready')
        self.assertIn('Existing transcript', body)

    def test_mobile_note_keeps_path_and_adds_verified_archive_identity(self):
        vault_source = self.vault / 'recording.m4a'
        vault_source.write_bytes(self.sample.read_bytes())
        note = innermaze.import_any(vault_source, self.vault, True, False, Path('/no-mlx'),
                                    innermaze.MODEL, self.whisper_root, self.manifest)
        self.assertEqual(note, self.imported())
        archived = self.vm / 'recording-renamed.m4a'
        archived.write_bytes(self.sample.read_bytes())
        self.indexed(source_path=self.vm / 'old-location.m4a')
        self.apple_manifest([(archived.name, 'UUID-MOBILE')])
        innermaze.process_pending(self.vault, Path('/no-mlx'), innermaze.MODEL, 2,
                                  self.whisper_root, self.manifest)
        fields, _ = innermaze.parse_note(note.read_text())
        self.assertEqual(note, self.imported())
        self.assertEqual(fields['archive_recording_id'], 'vm-UUID-MOBILE')
        self.assertEqual(fields['transcription_status'], 'ready')

    def test_ambiguous_different_transcripts_fail_before_writing(self):
        self.indexed(kind='voice-memos', stem='first')
        self.indexed(kind='just-press-record', stem='second',
                     words=[{'start': 9.0, 'end': 10.0, 'word': ' Different transcript.'}])
        with self.assertRaisesRegex(ValueError, 'More than one different cached transcript'):
            innermaze.import_any(self.sample, self.vault, True, True, Path('/no-mlx'),
                                 innermaze.MODEL, self.whisper_root, self.manifest)
        self.assertFalse((self.vault / 'Voice Archive').exists())

    def test_legacy_import_recognizes_previous_mobile_note(self):
        original = self.vm / 'actual-title.m4a'
        original.write_bytes(self.sample.read_bytes())
        self.indexed(source_path=original)
        note = innermaze.import_any(self.sample, self.vault, True, False, Path('/no-mlx'),
                                    innermaze.MODEL, self.root / 'NoWhisper', self.manifest)
        self.assertEqual(note, self.imported())
        self.assertIn('pending', note.read_text())
        self.apple_manifest([(original.name, 'UUID-ALPHA')])
        innermaze.import_legacy(self.vault, 'voice-memos', 'old-title', False,
                                self.whisper_root, self.vm, self.manifest, True)
        self.assertFalse((self.vault / 'Voice Archive/vm-UUID-ALPHA.md').exists())
        self.assertEqual(len(list((self.vault / 'Voice Archive').glob('*.md'))), 1)
        fields, body = innermaze.parse_note(note.read_text())
        self.assertEqual(fields['transcription_status'], 'ready')
        self.assertEqual(body.strip(), 'Existing transcript.')
        self.assertEqual(len(list((self.vault / 'Voice Archive/Audio').iterdir())), 1)

    def test_two_apple_uuids_identical_audio_keep_distinct_notes(self):
        a = self.vm / 'memo-a.m4a'
        b = self.vm / 'memo-b.m4a'
        a.write_bytes(self.sample.read_bytes())
        b.write_bytes(self.sample.read_bytes())
        self.apple_manifest([(a.name, 'UUID-A'), (b.name, 'UUID-B')])
        note_a = innermaze.import_any(a, self.vault, True, False, Path('/no-mlx'),
                                      innermaze.MODEL, self.whisper_root, self.manifest)
        note_b = innermaze.import_any(b, self.vault, True, False, Path('/no-mlx'),
                                      innermaze.MODEL, self.whisper_root, self.manifest)
        self.assertNotEqual(note_a, note_b)
        self.assertEqual({note_a.name, note_b.name}, {'vm-UUID-A.md', 'vm-UUID-B.md'})
        self.assertEqual(len(list((self.vault / 'Voice Archive/Audio').iterdir())), 1)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg not available')
    def test_ogg_converted_once_and_reused(self):
        import wave
        import struct
        import math
        wav = self.root / 'tone.wav'
        with wave.open(str(wav), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
            audio.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(2 * math.pi * 220 * i / 8000))) for i in range(4000)))
        ogg = self.root / 'tone.ogg'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(wav), str(ogg)], check=True)
        note = innermaze.import_any(ogg, self.vault, True, False, Path(sys.executable), innermaze.MODEL)
        fields, _ = innermaze.parse_note(note.read_text())
        self.assertTrue(fields['voice_audio'].endswith('.m4a'))
        audio_path = self.vault / fields['voice_audio']
        self.assertTrue(audio_path.is_file())
        old_bytes = audio_path.read_bytes()
        innermaze.import_any(ogg, self.vault, True, False, Path(sys.executable), innermaze.MODEL)
        self.assertEqual(audio_path.read_bytes(), old_bytes)
        self.assertEqual(len(list((self.vault / 'Voice Archive/Audio').iterdir())), 1)

def main():
    unittest.main(exit=False)

if __name__ == '__main__':
    main()
