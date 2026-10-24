#!/usr/bin/env python3
# filename: test_importer.py
# version: 0.1.0
# timestamp: 2026-10-24 10:53:00
from __future__ import annotations
import hashlib
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
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.vault = self.root / 'Vault'
        (self.vault / '.obsidian').mkdir(parents=True)
        self.sample = self.root / 'example.m4a'
        self.sample.write_bytes(b'Voice Archive fake audio; bytes preserved\x00\x01')
        self.digest = hashlib.sha256(self.sample.read_bytes()).hexdigest()
        self.original_temp = innermaze.TEMP_DIR
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
        if self.old_pythonpath is None:
            os.environ.pop('PYTHONPATH', None)
        else:
            os.environ['PYTHONPATH'] = self.old_pythonpath
        self.directory.cleanup()

    def imported(self):
        return self.vault / 'Voice Archive/Notes' / f'audio-{self.digest}.md'

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

    def test_existing_vault_audio_does_not_duplicate(self):
        internal = self.vault / 'Imported Audio.m4a'
        internal.write_bytes(self.sample.read_bytes())
        innermaze.import_any(internal, self.vault, True, False, Path(sys.executable), innermaze.MODEL)
        self.assertFalse((self.vault / 'Voice Archive/Audio').exists())
        fields, _ = innermaze.parse_note(self.imported().read_text())
        self.assertEqual(fields['voice_audio'], 'Imported Audio.m4a')

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
