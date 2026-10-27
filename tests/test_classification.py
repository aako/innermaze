#!/usr/bin/env python3
# filename: test_classification.py
# version: 0.1.0
# timestamp: 2026-10-27 10:19:00
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "tools/innermaze_classify.py"
SPEC = importlib.util.spec_from_file_location("innermaze_classify", FILE)
assert SPEC is not None and SPEC.loader is not None
classify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(classify)


def descriptor() -> dict:
    return {
        "summary": "A remembered dream about a film shoot.",
        "recording_form": "self_monologue",
        "purpose_guess": "dream_recollection",
        "freeform_type": "dream note",
        "language_status": "known", "language_primary": "en", "language_secondary": [],
        "subject_tags": ["dream", "film"], "people_entities": [],
        "content_signals": ["first-person memory"], "confidence": 0.92,
        "needs_audio_analysis": False, "reason": "Speaker recounts a dream.",
    }


class ClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        archive = self.root / "Voice Archive"
        archive.mkdir()
        self.note = archive / "Human renamed.md"
        self.original = (
            '---\nvoice_archive: true\nrecording_id: "stable-123"\n'
            'source_title: "2026-09-03 07-52-07 - Dream - shoot"\n'
            'recording_source: "voice-memos"\nsource_sha256: "0123"\n'
            'tags:\n  - voice-archive\n---\n'
            'A dream about a film shoot that I had last night.\n' * 2
        )
        self.note.write_text(self.original, encoding="utf-8")
        self.output = archive / "Classification"
        self.mock = lambda **kwargs: descriptor()

    def run_one(self, **kwargs):
        return classify.classify(self.note, self.output,
            server="http://127.0.0.1:1234/v1", model="voice-memo-qwen",
            call=self.mock, resolve=lambda _server, _model: "qwen-local-test",
            **kwargs)

    def test_dry_run_makes_no_files_and_calls_no_model(self):
        def fail(**kwargs):
            self.fail("model called during dry run")
        self.mock = fail
        result = self.run_one()
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(self.output.exists())
        self.assertEqual(self.note.read_text(), self.original)

    def test_append_only_dream_suggestion_and_source_provenance(self):
        a = self.run_one(apply=True)
        b = self.run_one(apply=True)
        self.assertNotEqual(a["run_id"], b["run_id"])
        for report in (a, b):
            data = json.loads(Path(report["file"]).read_text())
            self.assertEqual(data["recording_id"], "stable-123")
            self.assertEqual(data["ai"]["categories"], ["Dreams"])
            self.assertEqual(data["ai"]["tags"], ["recordings/dreams"])
            self.assertEqual(data["model_resolved"], "qwen-local-test")
            self.assertIn("dream about a film", data["transcript_snapshot"])
            self.assertEqual(classify.sha(data["transcript_snapshot"]), data["transcript_sha256"])
            self.assertEqual(data["engine"], "voice_memo_describe_v2.1.0")
            self.assertEqual(data["recorded_at_origin"], "source_title")
            self.assertTrue(data["recorded_at"].startswith("2026-09-03"))
        self.assertEqual(len(list(self.output.rglob("*.json"))), 2)
        self.assertEqual(self.note.read_text(), self.original)

    def test_evolving_taxonomy_does_not_rewrite_first_run(self):
        first = self.run_one(apply=True)
        tax = self.root / "taxonomy.json"
        tax.write_text(json.dumps({"purpose_categories": {"dream_recollection": ["Dream Journal", "Writing"]}}))
        second = self.run_one(apply=True, taxonomy_file=tax)
        before = json.loads(Path(first["file"]).read_text())
        after = json.loads(Path(second["file"]).read_text())
        self.assertEqual(before["ai"]["categories"], ["Dreams"])
        self.assertEqual(after["ai"]["categories"], ["Dream Journal", "Writing"])
        self.assertNotEqual(before["taxonomy_sha256"], after["taxonomy_sha256"])
        self.assertEqual(len(list(self.output.rglob("*.json"))), 2)

    def test_sparse_text_requires_review(self):
        prefix = self.original[:self.original.index("---\n", 4)+4]
        self.note.write_text(prefix + "Only a few words.\n")
        result = self.run_one(apply=True)
        item = json.loads(Path(result["file"]).read_text())
        self.assertTrue(item["needs_human_review"])
        self.assertTrue(item["raw_descriptor"]["needs_audio_analysis"])
        self.assertLessEqual(item["raw_descriptor"]["confidence"], 0.65)

    def test_pending_transcription_rejected(self):
        self.note.write_text(self.original.replace("tags:", "transcription_status: pending\ntags:"))
        with self.assertRaisesRegex(ValueError, "pending"):
            self.run_one(apply=True)
        self.assertFalse(self.output.exists())

    def test_unready_transcription_rejected(self):
        self.note.write_text(self.original.replace("tags:", "transcription_status: failed\ntags:"))
        with self.assertRaisesRegex(ValueError, "failed"):
            self.run_one(apply=True)
        self.assertFalse(self.output.exists())

    def test_note_change_during_llm_aborts_without_output(self):
        def change(**kwargs):
            self.note.write_text(self.original + "Human correction!\n")
            return descriptor()
        self.mock = change
        with self.assertRaisesRegex(ValueError, "changed while"):
            self.run_one(apply=True)
        self.assertFalse(self.output.exists())

    def test_non_recording_note_rejected(self):
        self.note.write_text(self.original.replace("voice_archive: true", "voice_archive: false"))
        with self.assertRaisesRegex(ValueError, "Not an Innermaze"):
            self.run_one(apply=True)
        self.assertFalse(self.output.exists())

    def test_remote_llm_endpoint_rejected_even_with_stub(self):
        with self.assertRaisesRegex(RuntimeError, "Refusing non-local"):
            classify.classify(self.note, self.output, server="http://public.example:1234/v1",
                model="voice-memo-qwen", apply=True, call=self.mock,
                resolve=lambda _server, _model: "fake")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
