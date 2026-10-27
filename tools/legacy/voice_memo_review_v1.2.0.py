#!/usr/bin/env python3
# filename: voice_memo_review.py
# version: 1.2.0
# timestamp: 2026-10-01 17:56:00

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

VERSION = "1.2.0"
PACKAGE_NAME = "voice_memo_review"

HOME = Path.home()
CLASSIFICATION_ROOT = HOME / "Work/Temp/VoiceMemoSaver/Classification-Qwen"
WHISPER_ROOT = HOME / "Work/Temp/VoiceMemoSaver/Whisper"
VOICE_ARCHIVE = HOME / "Dropbox/1 NOTES/Voice Memos Full DB"
JPR_ARCHIVE = HOME / "Dropbox/1 NOTES/Just Press Record Full DB"
REVIEW_ROOT = HOME / "Work/Temp/VoiceMemoSaver/Human-Review"
BACKUP_MANIFEST = HOME / "Library/Application Support/VoiceAudioBackup/manifest.sqlite3"

SOURCE_DIRS = {
    "voice-memos": "voice-memos",
    "jpr": "just-press-record",
}
AUDIO_EXTENSIONS = {".m4a", ".wav", ".aif", ".aiff", ".qta", ".mp3", ".mp4"}

QUICK_TAGS = [
    "Shazam / identify track",
    "Video reference",
    "Location context",
    "Follow up",
    "Keep",
]

QUICK_CATEGORIES = [
    "Note to self",
    "Dream",
    "Journal",
    "Conversation",
    "Interview",
    "Meeting",
    "Lecture / Talk",
    "Music / Performance",
    "Music / Sound Reference",
    "Ambience / Field",
    "Media excerpt",
    "Creative",
    "Reference",
    "Accidental / Test",
    "Other",
]

PURPOSE_CATEGORY = {
    "note_or_idea_to_self": "Note to self",
    "dream_recollection": "Dream",
    "personal_journal": "Journal",
    "learning_or_reference": "Reference",
    "conversation_documentation": "Conversation",
    "creative_work": "Creative",
    "performance_capture": "Music / Performance",
    "media_excerpt": "Media excerpt",
    "sound_reference": "Ambience / Field",
    "test_or_accidental": "Accidental / Test",
    "other": "Other",
}

FORM_CATEGORY = {
    "self_monologue": "Note to self",
    "single_speaker_other": "Other",
    "lecture_or_presentation": "Lecture / Talk",
    "conversation": "Conversation",
    "interview": "Interview",
    "meeting_or_group_discussion": "Meeting",
    "performance_or_music": "Music / Performance",
    "media_capture": "Media excerpt",
    "ambience_or_field_recording": "Ambience / Field",
    "test_or_accidental": "Accidental / Test",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stable_record_id(source: str, source_audio: str, audio_sha256: str = "") -> str:
    audio_sha256 = str(audio_sha256 or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", audio_sha256):
        return f"{source}-audio-{audio_sha256[:24]}"
    digest = hashlib.sha256(f"{source}\0{source_audio}".encode("utf-8")).hexdigest()[:24]
    return f"{source}-{digest}"


def parse_voice_memo_filename(stem: str) -> tuple[str, str]:
    match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}) - (.*)$", stem)
    if not match:
        return "", stem
    return match.group(1), match.group(2)


def parse_jpr_recorded_at(source_audio: str, output_stem: str) -> tuple[str, str]:
    """Recover JPR's recording time from its YYYY-MM-DD/HH-MM-SS layout."""
    path_text = str(source_audio)
    date_match = re.search(r"(?:^|/)(\d{4}-\d{2}-\d{2})(?:/|$)", path_text)
    time_match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{2})", output_stem.strip())
    if date_match and time_match:
        return (
            f"{date_match.group(1)} {time_match.group(1)}:{time_match.group(2)}:{time_match.group(3)}",
            "JPR date folder + filename",
        )
    if date_match:
        return date_match.group(1), "JPR date folder"
    return "", ""


def mdls_content_creation_date(path: Path) -> str:
    """Return local time from Spotlight media metadata when available."""
    mdls = Path("/usr/bin/mdls")
    if not mdls.is_file() or not path.is_file():
        return ""
    result = subprocess.run(
        [str(mdls), "-raw", "-name", "kMDItemContentCreationDate", str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=4,
    )
    raw = result.stdout.strip().strip('"')
    if not raw or raw in {"(null)", "null"}:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S +0000"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""


def recover_recorded_at(data: dict[str, Any], audio_path: Path | None) -> tuple[str, str]:
    existing = str(data.get("recorded_at", "")).strip()
    if existing:
        return existing, "catalog"

    if str(data.get("source", "")) == "jpr":
        recovered, source = parse_jpr_recorded_at(
            str(data.get("source_audio", "")),
            str(data.get("output_stem", "")),
        )
        if recovered:
            return recovered, source

    if audio_path is not None:
        metadata_date = mdls_content_creation_date(audio_path)
        if metadata_date:
            return metadata_date, "audio metadata"
        try:
            modified = datetime.fromtimestamp(audio_path.stat().st_mtime).astimezone()
            return modified.strftime("%Y-%m-%d %H:%M:%S"), "file timestamp (fallback)"
        except OSError:
            pass
    return "", ""


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def sanitize_csv_value(value: Any) -> str:
    if isinstance(value, list):
        return " | ".join(str(x) for x in value)
    if value is None:
        return ""
    return str(value)


@dataclass
class Record:
    data: dict[str, Any]
    audio_path: Path | None
    transcript_path: Path | None

    @property
    def record_id(self) -> str:
        return str(self.data.get("logical_id") or self.data["record_id"])


class ReviewStore:
    def __init__(
        self,
        classification_root: Path,
        whisper_root: Path,
        review_root: Path,
        voice_archive: Path,
        jpr_archive: Path,
        backup_manifest: Path,
    ) -> None:
        self.classification_root = classification_root
        self.whisper_root = whisper_root
        self.review_root = review_root
        self.voice_archive = voice_archive
        self.jpr_archive = jpr_archive
        self.backup_manifest = backup_manifest
        self.lock = threading.RLock()
        self.records: list[Record] = []
        self.by_id: dict[str, Record] = {}
        self.reviews: dict[str, dict[str, Any]] = {}
        self.audio_indexes: dict[str, dict[str, list[Path]]] = {}
        self.object_logical_ids: dict[tuple[str, str], str] = {}
        self.object_hashes: dict[tuple[str, str], str] = {}
        self.logical_metadata: dict[str, dict[str, Any]] = {}
        self.reload()

    def _build_audio_index(self, root: Path) -> dict[str, list[Path]]:
        index: dict[str, list[Path]] = {}
        if not root.is_dir():
            return index
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            for key in {path.name, path.stem}:
                index.setdefault(key, []).append(path)
        return index

    def _load_descriptor_records(self) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        catalog = self.classification_root / "catalog.jsonl"

        if catalog.is_file():
            for line_no, line in enumerate(catalog.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"Bad JSON in {catalog}:{line_no}: {exc}") from exc
                record_id = str(data.get("record_id", "")).strip()
                if record_id:
                    records[record_id] = data
            return records

        items = self.classification_root / "items"
        if items.is_dir():
            for path in sorted(items.rglob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                record_id = str(data.get("record_id", "")).strip()
                if record_id:
                    records[record_id] = data
        return records

    def _load_review_metadata(self, path: Path) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        if not path.is_file():
            return result
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                for row in reader:
                    key = str(row.get("source", "")).strip()
                    if key:
                        result[key] = dict(row)
        except OSError:
            return {}
        return result

    def _supplement_from_manifests(self, records: dict[str, dict[str, Any]]) -> None:
        for source, dirname in SOURCE_DIRS.items():
            transcript_dir = self.whisper_root / dirname
            manifest = transcript_dir / "manifest.tsv"
            if not manifest.is_file():
                continue
            review_meta = self._load_review_metadata(transcript_dir / "review.tsv")
            with manifest.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle, delimiter="\t")
                for row in reader:
                    if not row or row[0] == "status" or len(row) not in {4, 5}:
                        continue
                    status, source_audio, output_stem, audio_bytes = row[:4]
                    audio_sha256 = row[4].strip().lower() if len(row) == 5 else ""
                    record_id = stable_record_id(source, source_audio, audio_sha256)
                    if record_id in records:
                        continue
                    if source == "voice-memos":
                        recorded_at, title = parse_voice_memo_filename(output_stem)
                        recorded_at_source = "Voice Memos filename" if recorded_at else ""
                    else:
                        recorded_at, recorded_at_source = parse_jpr_recorded_at(source_audio, output_stem)
                        title = output_stem
                    meta = review_meta.get(source_audio, {})
                    duration = meta.get("duration_seconds", "")
                    try:
                        duration_value: float | None = float(duration) if duration else None
                    except ValueError:
                        duration_value = None
                    records[record_id] = {
                        "record_id": record_id,
                        "source": source,
                        "source_audio": source_audio,
                        "output_stem": output_stem,
                        "recorded_at": recorded_at,
                        "recorded_at_source": recorded_at_source,
                        "original_title": title,
                        "duration_seconds": duration_value,
                        "audio_bytes": int(audio_bytes) if audio_bytes.isdigit() else None,
                        "audio_sha256": audio_sha256,
                        "transcript_status": status,
                        "summary": "",
                        "recording_form": "unclear",
                        "purpose_guess": "unclear",
                        "freeform_type": "",
                        "language_status": "uncertain",
                        "language_primary": "und",
                        "language_secondary": [],
                        "subject_tags": [],
                        "people_entities": [],
                        "content_signals": [],
                        "confidence": 0,
                        "needs_audio_analysis": True,
                        "needs_human_review": True,
                        "review_reasons": ["missing_ai_descriptor"],
                        "reason": "No Qwen descriptor exists for this transcribed recording.",
                        "model": "",
                    }

    def _resolve_audio(self, data: dict[str, Any]) -> Path | None:
        source = str(data.get("source", ""))
        archive = self.voice_archive if source == "voice-memos" else self.jpr_archive
        index = self.audio_indexes.get(source, {})
        output_stem = str(data.get("output_stem", "")).strip()
        source_audio = str(data.get("source_audio", "")).strip()

        candidates: list[Path] = []
        if source_audio:
            source_path = Path(source_audio).expanduser()
            basename = source_path.name
            for key in (basename, Path(basename).stem):
                candidates.extend(index.get(key, []))
            if source_path.is_file():
                candidates.append(source_path)

        if output_stem:
            candidates.extend(index.get(output_stem, []))
            for ext in AUDIO_EXTENSIONS:
                candidate = archive / f"{output_stem}{ext}"
                if candidate.is_file():
                    candidates.append(candidate)

        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return candidate
        return None

    def _resolve_transcript(self, data: dict[str, Any]) -> Path | None:
        source = str(data.get("source", ""))
        dirname = SOURCE_DIRS.get(source)
        stem = str(data.get("output_stem", "")).strip()
        if not dirname or not stem:
            return None
        path = self.whisper_root / dirname / f"{stem}.txt"
        return path if path.is_file() else None

    def _load_backup_identity_maps(self) -> None:
        self.object_logical_ids = {}
        self.object_hashes = {}
        self.logical_metadata = {}
        if not self.backup_manifest.is_file():
            return

        try:
            db = sqlite3.connect(f"file:{self.backup_manifest}?mode=ro", uri=True)
        except sqlite3.Error:
            return
        try:
            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "objects" in tables:
                for archive, dest_rel, digest in db.execute(
                    "SELECT archive, dest_rel, sha256 FROM objects"
                ):
                    self.object_hashes[(str(archive), str(dest_rel))] = str(digest)

            if "object_recordings" in tables:
                for archive, dest_rel, logical_id in db.execute(
                    "SELECT archive, dest_rel, logical_id FROM object_recordings"
                ):
                    self.object_logical_ids[(str(archive), str(dest_rel))] = str(logical_id)

            if "recordings" in tables:
                for row in db.execute(
                    """
                    SELECT logical_id, source, native_id, content_sha256,
                           current_source_rel, current_title, recorded_at
                    FROM recordings
                    """
                ):
                    logical_id = str(row[0])
                    self.logical_metadata[logical_id] = {
                        "source": row[1],
                        "native_id": row[2],
                        "content_sha256": row[3],
                        "current_source_rel": row[4],
                        "current_title": row[5],
                        "recorded_at": row[6],
                    }
        except sqlite3.Error:
            pass
        finally:
            db.close()

    def _archive_key_for_audio(
        self,
        source: str,
        audio_path: Path | None,
    ) -> tuple[str, str] | None:
        if audio_path is None:
            return None
        if source == "voice-memos":
            archive_name, root = "voice_memos", self.voice_archive
        elif source == "jpr":
            archive_name, root = "jpr", self.jpr_archive
        else:
            return None
        try:
            rel = audio_path.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            return None
        return archive_name, rel

    def _logical_identity(
        self,
        data: dict[str, Any],
        audio_path: Path | None,
    ) -> tuple[str, str]:
        source = str(data.get("source", ""))
        key = self._archive_key_for_audio(source, audio_path)
        if key is not None:
            mapped = self.object_logical_ids.get(key)
            if mapped:
                return mapped, "backup manifest"
            digest = self.object_hashes.get(key)
            if digest:
                return f"{source}:sha256:{digest}", "archive SHA-256"

        digest = str(data.get("audio_sha256", "")).strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            return f"{source}:sha256:{digest}", "Whisper SHA-256"

        return str(data.get("record_id", "")), "legacy classifier ID"

    def _review_for_record(
        self,
        logical_id: str,
        classifier_id: str,
        raw_reviews: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if logical_id in raw_reviews:
            return dict(raw_reviews[logical_id])
        if classifier_id in raw_reviews:
            review = dict(raw_reviews[classifier_id])
            review["legacy_record_id"] = classifier_id
            review["record_id"] = logical_id
            return review
        return {}

    def _load_reviews(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        items = self.review_root / "items"
        if not items.is_dir():
            return result
        for path in sorted(items.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            record_id = str(data.get("record_id", "")).strip()
            if record_id:
                result[record_id] = data
        return result

    def reload(self) -> None:
        with self.lock:
            self.audio_indexes = {
                "voice-memos": self._build_audio_index(self.voice_archive),
                "jpr": self._build_audio_index(self.jpr_archive),
            }
            self._load_backup_identity_maps()
            descriptors = self._load_descriptor_records()
            self._supplement_from_manifests(descriptors)
            raw_reviews = self._load_reviews()

            record_map: dict[str, Record] = {}
            canonical_reviews: dict[str, dict[str, Any]] = {}

            for original_data in descriptors.values():
                classifier_id = str(original_data.get("record_id", "")).strip()
                if not classifier_id:
                    continue

                data = dict(original_data)
                audio_path = self._resolve_audio(data)
                logical_id, identity_source = self._logical_identity(data, audio_path)
                if not logical_id:
                    logical_id = classifier_id
                data["classifier_record_id"] = classifier_id
                data["logical_id"] = logical_id
                data["identity_source"] = identity_source

                identity_meta = self.logical_metadata.get(logical_id, {})
                current_title = str(identity_meta.get("current_title") or "").strip()
                if current_title:
                    data["current_source_title"] = current_title
                current_source_rel = str(identity_meta.get("current_source_rel") or "").strip()
                if current_source_rel:
                    data["current_source_rel"] = current_source_rel
                native_id = str(identity_meta.get("native_id") or "").strip()
                if native_id:
                    data["source_native_id"] = native_id
                if not data.get("audio_sha256") and identity_meta.get("content_sha256"):
                    data["audio_sha256"] = identity_meta["content_sha256"]

                recovered_at, recorded_at_source = recover_recorded_at(data, audio_path)
                if not recovered_at and identity_meta.get("recorded_at"):
                    recovered_at = str(identity_meta["recorded_at"])
                    recorded_at_source = "backup identity manifest"
                if recovered_at:
                    data["recorded_at"] = recovered_at
                if recorded_at_source:
                    data["recorded_at_source"] = recorded_at_source

                record = Record(
                    data=data,
                    audio_path=audio_path,
                    transcript_path=self._resolve_transcript(data),
                )

                existing = record_map.get(logical_id)
                if existing is None:
                    record_map[logical_id] = record
                else:
                    # Prefer the descriptor that actually has AI metadata/audio.
                    old_score = int(bool(existing.data.get("model"))) + int(existing.audio_path is not None)
                    new_score = int(bool(record.data.get("model"))) + int(record.audio_path is not None)
                    if new_score > old_score:
                        record_map[logical_id] = record

                review = self._review_for_record(logical_id, classifier_id, raw_reviews)
                if review:
                    canonical_reviews[logical_id] = review

            records = list(record_map.values())
            records.sort(
                key=lambda record: (
                    str(record.data.get("recorded_at", "")),
                    str(record.data.get("source", "")),
                    str(record.data.get("original_title", "")),
                )
            )
            self.records = records
            self.by_id = {record.record_id: record for record in records}
            self.reviews = canonical_reviews

    def ai_suggested_category(self, data: dict[str, Any]) -> str:
        purpose = str(data.get("purpose_guess", ""))
        if purpose in PURPOSE_CATEGORY:
            return PURPOSE_CATEGORY[purpose]
        form = str(data.get("recording_form", ""))
        return FORM_CATEGORY.get(form, "Other")

    def list_payload(self) -> dict[str, Any]:
        with self.lock:
            rows = []
            for record in self.records:
                data = record.data
                review = self.reviews.get(record.record_id, {})
                rows.append(
                    {
                        "record_id": record.record_id,
                        "source": data.get("source", ""),
                        "title": review.get("human_title", "")
                        or data.get("current_source_title", "")
                        or data.get("original_title", "")
                        or data.get("output_stem", ""),
                        "source_title": data.get("current_source_title", "")
                        or data.get("original_title", "")
                        or data.get("output_stem", ""),
                        "human_title": review.get("human_title", ""),
                        "recorded_at": data.get("recorded_at", ""),
                        "duration_seconds": data.get("duration_seconds"),
                        "summary": data.get("summary", ""),
                        "recording_form": data.get("recording_form", ""),
                        "purpose_guess": data.get("purpose_guess", ""),
                        "confidence": data.get("confidence", 0),
                        "needs_human_review": bool(data.get("needs_human_review")),
                        "needs_audio_analysis": bool(data.get("needs_audio_analysis")),
                        "has_ai": bool(str(data.get("model", "")).strip()),
                        "audio_available": record.audio_path is not None,
                        "review_status": review.get("review_status", ""),
                        "human_category": review.get("human_category", ""),
                        "updated_at": review.get("updated_at", ""),
                    }
                )
            counts = {
                "total": len(rows),
                "reviewed": sum(1 for row in rows if row["review_status"] == "reviewed"),
                "later": sum(1 for row in rows if row["review_status"] == "later"),
                "skipped": sum(1 for row in rows if row["review_status"] == "skipped"),
            }
            counts["unreviewed"] = counts["total"] - counts["reviewed"] - counts["later"] - counts["skipped"]
            return {
                "records": rows,
                "counts": counts,
                "quick_categories": QUICK_CATEGORIES,
                "quick_tags": QUICK_TAGS,
            }

    def record_payload(self, record_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.by_id.get(record_id)
            if record is None:
                raise KeyError(record_id)
            data = dict(record.data)
            transcript = ""
            if record.transcript_path is not None:
                try:
                    transcript = record.transcript_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    transcript = ""
            review = dict(self.reviews.get(record_id, {}))
            return {
                "record": data,
                "review": review,
                "audio_available": record.audio_path is not None,
                "transcript": transcript,
                "ai_suggested_category": self.ai_suggested_category(data),
            }

    def save_review(self, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            record = self.by_id.get(record_id)
            if record is None:
                raise KeyError(record_id)

            status = str(payload.get("review_status", "reviewed")).strip()
            if status not in {"reviewed", "later", "skipped"}:
                raise ValueError("review_status must be reviewed, later, or skipped")

            human_title = str(payload.get("human_title", "")).strip()
            category = str(payload.get("human_category", "")).strip()
            description = str(payload.get("human_description", "")).strip()
            location = str(payload.get("human_location", "")).strip()
            raw_tags = payload.get("human_tags", [])
            if isinstance(raw_tags, str):
                tags = [part.strip() for part in raw_tags.split(",") if part.strip()]
            elif isinstance(raw_tags, list):
                tags = [str(part).strip() for part in raw_tags if str(part).strip()]
            else:
                raise ValueError("human_tags must be a list or comma-separated string")
            tags = list(dict.fromkeys(tags))[:50]
            if len(human_title) > 500:
                raise ValueError("human_title is too long")
            if len(category) > 200:
                raise ValueError("human_category is too long")
            if len(description) > 20000:
                raise ValueError("human_description is too long")
            if len(location) > 500:
                raise ValueError("human_location is too long")

            review = {
                "record_id": record_id,
                "classifier_record_id": record.data.get("classifier_record_id", ""),
                "identity_source": record.data.get("identity_source", ""),
                "source": record.data.get("source", ""),
                "output_stem": record.data.get("output_stem", ""),
                "original_title": record.data.get("original_title", ""),
                "source_title": record.data.get("current_source_title", "")
                or record.data.get("original_title", ""),
                "recorded_at": record.data.get("recorded_at", ""),
                "review_status": status,
                "human_title": human_title,
                "human_category": category,
                "human_tags": tags,
                "human_location": location,
                "human_description": description,
                "ai_accepted": bool(payload.get("ai_accepted", False)),
                "updated_at": iso_now(),
            }
            if record.audio_path is not None:
                review["audio_path"] = str(record.audio_path)

            path = self.review_root / "items" / f"{record_id}.json"
            atomic_write_text(path, json.dumps(review, ensure_ascii=False, indent=2) + "\n")
            self.reviews[record_id] = review
            self._write_aggregate_reviews()
            return review

    def _write_aggregate_reviews(self) -> None:
        ordered = sorted(
            self.reviews.values(),
            key=lambda item: (
                str(item.get("recorded_at", "")),
                str(item.get("source", "")),
                str(item.get("original_title", "")),
            ),
        )
        atomic_write_text(
            self.review_root / "reviews.jsonl",
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in ordered),
        )

        csv_path = self.review_root / "reviews.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        temp = csv_path.with_name(f".{csv_path.name}.{os.getpid()}.tmp")
        fields = [
            "record_id",
            "classifier_record_id",
            "identity_source",
            "source",
            "recorded_at",
            "original_title",
            "source_title",
            "human_title",
            "output_stem",
            "review_status",
            "human_category",
            "human_tags",
            "human_location",
            "human_description",
            "ai_accepted",
            "updated_at",
            "audio_path",
        ]
        with temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for item in ordered:
                writer.writerow({field: sanitize_csv_value(item.get(field, "")) for field in fields})
        os.replace(temp, csv_path)

    def audio_path(self, record_id: str) -> Path:
        with self.lock:
            record = self.by_id.get(record_id)
            if record is None or record.audio_path is None:
                raise KeyError(record_id)
            return record.audio_path

    def reveal_audio(self, record_id: str) -> None:
        subprocess.run(["/usr/bin/open", "-R", str(self.audio_path(record_id))], check=False)


HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice Memo Review</title>
<style>
:root{color-scheme:dark;--bg:#101112;--panel:#181a1d;--panel2:#202329;--text:#f3f3f3;--muted:#a6acb5;--line:#343941;--accent:#83a9ff;--good:#76d394;--warn:#e8bc65;--bad:#ef7e7e}
*{box-sizing:border-box} body{margin:0;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);height:100vh;overflow:hidden}
button,input,select,textarea{font:inherit} button{cursor:pointer}.app{display:grid;grid-template-columns:320px 1fr;height:100vh}.sidebar{border-right:1px solid var(--line);display:flex;flex-direction:column;min-width:0;background:#121416}.sidehead{padding:14px;border-bottom:1px solid var(--line);display:grid;gap:10px}.brand{font-weight:700;font-size:17px;display:flex;justify-content:space-between;align-items:center}.counts{color:var(--muted);font-size:12px}.controls{display:grid;grid-template-columns:1fr 1fr;gap:8px}.search{grid-column:1/-1}
input,select,textarea{color:var(--text);background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px;outline:none} input:focus,select:focus,textarea:focus{border-color:var(--accent)}.queue{overflow:auto;flex:1}.row{padding:11px 13px;border-bottom:1px solid #24272c;cursor:pointer;display:grid;gap:4px}.row:hover{background:#191c20}.row.active{background:#252b35;box-shadow:inset 3px 0 var(--accent)}.rowtitle{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}.rowmeta{display:flex;gap:7px;color:var(--muted);font-size:11px;align-items:center}.dot{width:7px;height:7px;border-radius:50%;background:#666;display:inline-block}.dot.reviewed{background:var(--good)}.dot.later{background:var(--warn)}.dot.skipped{background:#8c8c8c}.dot.flag{background:var(--bad)}
.main{overflow:auto;padding:20px 24px 90px}.recorded{font-size:16px;font-weight:650;margin-top:7px}.recorded-source{font-size:11px;color:var(--muted);font-weight:400;margin-left:8px}.top{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:14px}h1{margin:0 0 4px;font-size:25px;line-height:1.15}.subtitle{color:var(--muted)}.nav{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.btn{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:8px;padding:8px 11px}.btn:hover{border-color:#626a76}.btn.primary{background:#2d5fca;border-color:#3d72df;color:white}.btn.good{background:#245c39;border-color:#34784c;color:white}.btn.warn{background:#65501f;border-color:#80672d;color:white}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px;margin:12px 0}.audio-row{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center}audio{width:100%}.pills{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}.pill{border:1px solid var(--line);background:#22262b;border-radius:999px;padding:4px 8px;color:#c9ced6;font-size:12px}.pill.alert{border-color:#71494b;color:#ffb9bc}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px}.summary{font-size:16px;line-height:1.45}.meta-table{display:grid;grid-template-columns:140px 1fr;gap:6px 12px;font-size:13px}.meta-table div:nth-child(odd){color:var(--muted)}
.review{border-color:#4b5361}.category-buttons{display:flex;flex-wrap:wrap;gap:7px;margin:8px 0 12px}.cat{border:1px solid var(--line);background:#23262b;color:var(--text);padding:7px 9px;border-radius:999px}.cat.selected,.tagbtn.selected{background:#31568e;border-color:#5681c4}.tagbtn{border:1px solid var(--line);background:#23262b;color:var(--text);padding:6px 9px;border-radius:999px}textarea{width:100%;min-height:130px;resize:vertical;line-height:1.4}.review-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}.hint{margin-left:auto;color:var(--muted);font-size:11px}details summary{cursor:pointer;font-weight:600}.transcript{white-space:pre-wrap;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;max-height:420px;overflow:auto;margin-top:12px;background:#111315;border-radius:8px;padding:12px}.empty{color:var(--muted);padding:40px;text-align:center}.toast{position:fixed;bottom:18px;right:18px;background:#2a3038;border:1px solid #586170;padding:10px 13px;border-radius:9px;opacity:0;transform:translateY(8px);transition:.18s;pointer-events:none}.toast.show{opacity:1;transform:none}
@media(max-width:850px){.app{grid-template-columns:1fr}.sidebar{display:none}.main{padding:14px 14px 100px}.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="app">
<aside class="sidebar">
<div class="sidehead">
<div class="brand"><span>Voice Memo Review</span><button class="btn" id="refreshBtn">↻</button></div>
<div class="counts" id="counts">Loading…</div>
<div class="controls">
<select id="filter"><option value="unreviewed">Unreviewed</option><option value="flagged">AI flagged</option><option value="later">Needs another listen</option><option value="reviewed">Reviewed</option><option value="skipped">Skipped</option><option value="missing-ai">Missing AI description</option><option value="all">All</option></select>
<select id="sourceFilter"><option value="all">Both sources</option><option value="voice-memos">Voice Memos</option><option value="jpr">Just Press Record</option></select>
<select id="sortOrder" style="grid-column:1/-1"><option value="newest" selected>Newest first</option><option value="oldest">Oldest first</option></select>
<input class="search" id="search" placeholder="Search title or summary">
</div></div>
<div class="queue" id="queue"></div>
</aside>
<main class="main"><div id="content" class="empty">Loading catalog…</div></main>
</div>
<div class="toast" id="toast"></div>
<script>
const TOKEN=__TOKEN__;
let catalog=[],counts={},quickCategories=[],quickTags=[],currentId=null,currentDetail=null,filtered=[];
function api(path,options={}){options.headers=Object.assign({},options.headers||{},{"X-VMR-Token":TOKEN});return fetch(path,options).then(async r=>{if(!r.ok)throw new Error((await r.text())||`${r.status}`);const ct=r.headers.get("content-type")||"";return ct.includes("application/json")?r.json():r.text()})}
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]))}
function fmtDuration(v){const n=Number(v);if(!Number.isFinite(n))return"";const h=Math.floor(n/3600),m=Math.floor((n%3600)/60),s=Math.round(n%60);return h?`${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`:`${m}:${String(s).padStart(2,"0")}`}
function showToast(text){const t=document.getElementById("toast");t.textContent=text;t.classList.add("show");clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>t.classList.remove("show"),1600)}
function statusDot(r){if(r.review_status)return r.review_status;if(r.needs_human_review||r.needs_audio_analysis)return"flag";return""}
function applyFilter(){const f=document.getElementById("filter").value,sf=document.getElementById("sourceFilter").value,sort=document.getElementById("sortOrder").value,q=document.getElementById("search").value.trim().toLowerCase();filtered=catalog.filter(r=>{if(sf!=="all"&&r.source!==sf)return false;if(q&&!(`${r.title} ${r.source_title||""} ${r.summary}`.toLowerCase().includes(q)))return false;if(f==="unreviewed")return!r.review_status;if(f==="flagged")return!r.review_status&&(r.needs_human_review||r.needs_audio_analysis);if(f==="later")return r.review_status==="later";if(f==="reviewed")return r.review_status==="reviewed";if(f==="skipped")return r.review_status==="skipped";if(f==="missing-ai")return!r.has_ai;return true});filtered.sort((a,b)=>{const ad=String(a.recorded_at||""),bd=String(b.recorded_at||"");if(!ad&&!bd)return String(a.title||"").localeCompare(String(b.title||""));if(!ad)return 1;if(!bd)return-1;const cmp=ad.localeCompare(bd);return sort==="newest"?-cmp:cmp});renderQueue();if(!currentId||!filtered.some(r=>r.record_id===currentId)){if(filtered.length)loadRecord(filtered[0].record_id);else renderEmpty()}}
function renderQueue(){const q=document.getElementById("queue");q.innerHTML=filtered.map(r=>`<div class="row ${r.record_id===currentId?"active":""}" data-id="${esc(r.record_id)}"><div class="rowtitle">${esc(r.title||"(Untitled)")}</div><div class="rowmeta"><span class="dot ${statusDot(r)}"></span><span>${esc(r.recorded_at||r.source)}</span>${r.duration_seconds?`<span>• ${fmtDuration(r.duration_seconds)}</span>`:""}</div></div>`).join("");q.querySelectorAll(".row").forEach(el=>el.onclick=()=>loadRecord(el.dataset.id))}
function updateCounts(){document.getElementById("counts").textContent=`${counts.reviewed||0} reviewed · ${counts.unreviewed||0} unreviewed · ${counts.later||0} later · ${counts.total||0} total`}
async function loadCatalog(reselect=true){const data=await api("/api/catalog");catalog=data.records;counts=data.counts;quickCategories=data.quick_categories;quickTags=data.quick_tags||[];updateCounts();if(!reselect)currentId=null;applyFilter()}
function renderEmpty(){currentId=null;document.getElementById("content").innerHTML=`<div class="empty">No recordings in this queue.</div>`;renderQueue()}
async function loadRecord(id){currentId=id;renderQueue();currentDetail=await api(`/api/record?id=${encodeURIComponent(id)}`);renderRecord()}
function renderRecord(){const d=currentDetail.record,rv=currentDetail.review||{},tags=d.subject_tags||[],people=d.people_entities||[],reasons=d.review_reasons||[],selectedCat=rv.human_category||"",selectedTags=Array.isArray(rv.human_tags)?rv.human_tags:[],aiCat=currentDetail.ai_suggested_category||"Other",displayTitle=rv.human_title||d.current_source_title||d.original_title||d.output_stem||"(Untitled)",sourceTitle=d.current_source_title||d.original_title||d.output_stem||"";const audio=currentDetail.audio_available?`<audio id="player" controls preload="metadata" src="/api/audio?id=${encodeURIComponent(d.logical_id||d.record_id)}&token=${encodeURIComponent(TOKEN)}"></audio>`:`<div style="color:var(--bad)">Audio file not found in current archives.</div>`;const pill=(text,alert=false)=>`<span class="pill ${alert?"alert":""}">${esc(text)}</span>`;const cats=quickCategories.map(c=>`<button class="cat ${c===selectedCat?"selected":""}" data-cat="${esc(c)}">${esc(c)}</button>`).join("");const tagButtons=quickTags.map(t=>`<button class="tagbtn ${selectedTags.includes(t)?"selected":""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("");
document.getElementById("content").innerHTML=`
<div class="top"><div><h1>${esc(displayTitle)}</h1>${rv.human_title&&rv.human_title!==sourceTitle?`<div class="subtitle">Source title: ${esc(sourceTitle)}</div>`:""}<div class="recorded">${d.recorded_at?esc(d.recorded_at):"Date unknown"}${d.recorded_at_source?`<span class="recorded-source">${esc(d.recorded_at_source)}</span>`:""}</div><div class="subtitle">${esc(d.source)}${d.duration_seconds?" · "+fmtDuration(d.duration_seconds):""}</div></div><div class="nav"><button class="btn" id="prevBtn">← Previous</button><button class="btn" id="copyDateBtn">Copy Date</button><button class="btn" id="photosBtn">Open Photos</button><button class="btn" id="revealBtn">Reveal Audio</button><button class="btn" id="nextBtn">Next →</button></div></div>
<div class="card"><div class="audio-row"><button class="btn" id="back10">−10s</button>${audio}<button class="btn" id="fwd10">+10s</button></div><div class="pills">${d.needs_human_review?pill("AI flagged for review",true):""}${d.needs_audio_analysis?pill("Audio review requested",true):""}${pill(`Confidence ${Math.round(Number(d.confidence||0)*100)}%`)}${rv.review_status?pill(`Human: ${rv.review_status}`):""}</div></div>
<div class="card"><div class="label">AI summary</div><div class="summary">${esc(d.summary||"No AI description exists for this recording.")}</div><div class="pills">${pill(d.recording_form||"unclear")}${pill(d.purpose_guess||"unclear")}${d.freeform_type?pill(d.freeform_type):""}${tags.map(x=>pill(x)).join("")}</div><div class="grid2" style="margin-top:14px"><div><div class="label">People / entities</div><div>${people.length?people.map(x=>pill(x)).join(" "):`<span class="subtitle">None named</span>`}</div></div><div><div class="label">Why AI wanted review</div><div>${reasons.length?reasons.map(x=>pill(x,true)).join(" "):`<span class="subtitle">No special review reason</span>`}</div></div></div></div>
<div class="card review"><div class="label">Human title</div><input id="humanTitle" style="width:100%;font-size:16px;font-weight:600" placeholder="Give this recording a useful title" value="${esc(rv.human_title||"")}"><div class="subtitle" style="margin-top:6px">Saved separately from the source app title. Source writeback can use this later without changing identity.</div><div class="label" style="margin-top:14px">Human category</div><div class="category-buttons">${cats}</div><input id="customCategory" style="width:100%" placeholder="Or type a custom category" value="${esc(selectedCat)}"><div class="label" style="margin-top:14px">Intent / use tags</div><div class="category-buttons">${tagButtons}</div><input id="humanTags" style="width:100%" placeholder="Comma-separated tags" value="${esc(selectedTags.join(", "))}"><div class="grid2" style="margin-top:14px"><div><div class="label">Location / context</div><input id="humanLocation" style="width:100%" placeholder="e.g. bar in Brooklyn, friend's party" value="${esc(rv.human_location||"")}"></div><div><div class="label">Recording date/time</div><input style="width:100%" readonly value="${esc(d.recorded_at||"Unknown")}"></div></div><div class="label" style="margin-top:14px">Aaron's description</div><textarea id="humanDescription" placeholder="What is this recording?">${esc(rv.human_description||"")}</textarea><div class="review-actions"><button class="btn" id="dictateBtn">🎙 Pause & Dictate</button><button class="btn" id="copyAiBtn">Use AI Summary</button><button class="btn good" id="saveBtn">Save + Next ⌘↵</button><button class="btn primary" id="acceptBtn">Accept AI + Next A</button><button class="btn warn" id="laterBtn">Another Listen</button><button class="btn" id="skipBtn">Skip</button><span class="hint">Space play/pause · ←/→ 10s · A accept AI</span></div><div class="subtitle" style="margin-top:9px">“Pause & Dictate” pauses playback and focuses this field. Then use your normal macOS Dictation shortcut.</div><div class="subtitle" style="margin-top:6px">AI suggestion: <strong>${esc(aiCat)}</strong></div></div>
<details class="card"><summary>Transcript ${currentDetail.transcript?`(${currentDetail.transcript.length.toLocaleString()} chars)`:"(none)"}</summary><div class="transcript">${esc(currentDetail.transcript||"No transcript is available.")}</div></details>
<details class="card"><summary>Technical / classifier metadata</summary><div class="meta-table" style="margin-top:12px"><div>Logical ID</div><div>${esc(d.logical_id||d.record_id)}</div><div>Identity source</div><div>${esc(d.identity_source||"")}</div><div>Classifier ID</div><div>${esc(d.classifier_record_id||d.record_id)}</div><div>Source native ID</div><div>${esc(d.source_native_id||"")}</div><div>Audio SHA-256</div><div>${esc(d.audio_sha256||"")}</div><div>Date source</div><div>${esc(d.recorded_at_source||"")}</div><div>Source audio</div><div>${esc(d.source_audio||"")}</div><div>Output stem</div><div>${esc(d.output_stem||"")}</div><div>Language</div><div>${esc(d.language_primary||d.language||"")}</div><div>Transcript</div><div>${esc(d.transcript_status||"")}</div><div>Model</div><div>${esc(d.model||"No AI descriptor")}</div><div>Reason</div><div>${esc(d.reason||"")}</div></div></details>`;
wireRecordControls()}
function currentFilteredIndex(){return filtered.findIndex(r=>r.record_id===currentId)}
function navigate(delta){const i=currentFilteredIndex();if(i<0||!filtered.length)return;const n=Math.max(0,Math.min(filtered.length-1,i+delta));loadRecord(filtered[n].record_id)}
async function save(status="reviewed",aiAccepted=false,advance=true){if(!currentDetail)return;const id=currentDetail.record.logical_id||currentDetail.record.record_id,humanTitle=document.getElementById("humanTitle").value.trim(),category=document.getElementById("customCategory").value.trim(),humanTags=document.getElementById("humanTags").value.split(",").map(x=>x.trim()).filter(Boolean),location=document.getElementById("humanLocation").value.trim(),description=document.getElementById("humanDescription").value.trim();await api("/api/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({record_id:id,review_status:status,human_title:humanTitle,human_category:category,human_tags:humanTags,human_location:location,human_description:description,ai_accepted:aiAccepted})});const row=catalog.find(r=>r.record_id===id);if(row){row.review_status=status;row.human_title=humanTitle;row.title=humanTitle||row.source_title;row.human_category=category;row.updated_at=new Date().toISOString()}counts.reviewed=catalog.filter(r=>r.review_status==="reviewed").length;counts.later=catalog.filter(r=>r.review_status==="later").length;counts.skipped=catalog.filter(r=>r.review_status==="skipped").length;counts.unreviewed=catalog.length-counts.reviewed-counts.later-counts.skipped;updateCounts();showToast(status==="reviewed"?"Saved":status==="later"?"Marked for another listen":"Skipped");if(advance){const oldFiltered=filtered.slice(),oldIndex=oldFiltered.findIndex(r=>r.record_id===id);applyFilter();if(filtered.length){const still=filtered.findIndex(r=>r.record_id===id);if(still>=0)navigate(1);else loadRecord(filtered[Math.min(Math.max(oldIndex,0),filtered.length-1)].record_id)}}}
function wireRecordControls(){const player=document.getElementById("player");document.getElementById("prevBtn").onclick=()=>navigate(-1);document.getElementById("nextBtn").onclick=()=>navigate(1);document.getElementById("copyDateBtn").onclick=()=>{const value=currentDetail.record.recorded_at||"";if(value&&navigator.clipboard)navigator.clipboard.writeText(value).then(()=>showToast("Date copied"));else showToast(value?"Copy unavailable":"Date unknown")};document.getElementById("photosBtn").onclick=()=>api("/api/open-photos",{method:"POST"}).then(()=>showToast("Photos opened"));document.getElementById("back10").onclick=()=>{if(player)player.currentTime=Math.max(0,player.currentTime-10)};document.getElementById("fwd10").onclick=()=>{if(player)player.currentTime=Math.min(player.duration||Infinity,player.currentTime+10)};document.getElementById("revealBtn").onclick=()=>api("/api/reveal",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({record_id:currentId})}).then(()=>showToast("Revealed in Finder"));document.querySelectorAll(".cat").forEach(b=>b.onclick=()=>{document.querySelectorAll(".cat").forEach(x=>x.classList.remove("selected"));b.classList.add("selected");document.getElementById("customCategory").value=b.dataset.cat});document.querySelectorAll(".tagbtn").forEach(b=>b.onclick=()=>{b.classList.toggle("selected");document.getElementById("humanTags").value=[...document.querySelectorAll(".tagbtn.selected")].map(x=>x.dataset.tag).join(", ")});document.getElementById("dictateBtn").onclick=()=>{if(player)player.pause();const ta=document.getElementById("humanDescription");ta.focus();ta.setSelectionRange(ta.value.length,ta.value.length);showToast("Ready for macOS Dictation")};document.getElementById("copyAiBtn").onclick=()=>{document.getElementById("humanDescription").value=currentDetail.record.summary||"";if(!document.getElementById("customCategory").value.trim())document.getElementById("customCategory").value=currentDetail.ai_suggested_category||""};document.getElementById("saveBtn").onclick=()=>save("reviewed",false,true);document.getElementById("acceptBtn").onclick=()=>{document.getElementById("humanDescription").value=currentDetail.record.summary||"";document.getElementById("customCategory").value=currentDetail.ai_suggested_category||"Other";save("reviewed",true,true)};document.getElementById("laterBtn").onclick=()=>save("later",false,true);document.getElementById("skipBtn").onclick=()=>save("skipped",false,true)}
document.getElementById("filter").onchange=applyFilter;document.getElementById("sourceFilter").onchange=applyFilter;document.getElementById("sortOrder").onchange=applyFilter;document.getElementById("search").oninput=applyFilter;document.getElementById("refreshBtn").onclick=()=>api("/api/refresh",{method:"POST"}).then(()=>loadCatalog()).then(()=>showToast("Catalog refreshed"));
document.addEventListener("keydown",e=>{const tag=(e.target.tagName||"").toLowerCase(),typing=tag==="textarea"||tag==="input"||tag==="select";if((e.metaKey||e.ctrlKey)&&e.key==="Enter"){e.preventDefault();save("reviewed",false,true);return}if(typing)return;const p=document.getElementById("player");if(e.code==="Space"){e.preventDefault();if(p)p.paused?p.play():p.pause()}else if(e.key==="ArrowLeft"){e.preventDefault();if(p)p.currentTime=Math.max(0,p.currentTime-10)}else if(e.key==="ArrowRight"){e.preventDefault();if(p)p.currentTime=Math.min(p.duration||Infinity,p.currentTime+10)}else if(e.key.toLowerCase()==="a"&&currentDetail){e.preventDefault();document.getElementById("humanDescription").value=currentDetail.record.summary||"";document.getElementById("customCategory").value=currentDetail.ai_suggested_category||"Other";save("reviewed",true,true)}});
loadCatalog(false).catch(err=>{document.getElementById("content").innerHTML=`<div class="card"><strong>Could not load catalog</strong><pre>${esc(err.message)}</pre></div>`});
</script>
</body>
</html>'''


class ReviewerHandler(BaseHTTPRequestHandler):
    server_version = "VoiceMemoReview/1.0"

    @property
    def app(self) -> "ReviewerServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.app.verbose:
            super().log_message(fmt, *args)

    def _token_ok(self) -> bool:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        supplied = self.headers.get("X-VMR-Token") or query.get("token", [""])[0]
        return secrets.compare_digest(supplied, self.app.token)

    def _json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 1_000_000:
            raise ValueError("request body too large")
        data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._text(
                HTML.replace("__TOKEN__", json.dumps(self.app.token)),
                content_type="text/html; charset=utf-8",
            )
            return

        if not self._token_ok():
            self._text("Forbidden", HTTPStatus.FORBIDDEN)
            return

        try:
            if parsed.path == "/api/catalog":
                self._json(self.app.store.list_payload())
                return
            if parsed.path == "/api/record":
                record_id = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
                self._json(self.app.store.record_payload(record_id))
                return
            if parsed.path == "/api/audio":
                record_id = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
                self._serve_audio(self.app.store.audio_path(record_id))
                return
            self._text("Not found", HTTPStatus.NOT_FOUND)
        except KeyError:
            self._text("Record not found", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._text(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not self._token_ok():
            self._text("Forbidden", HTTPStatus.FORBIDDEN)
            return
        try:
            if parsed.path == "/api/review":
                data = self._body_json()
                self._json(self.app.store.save_review(str(data.get("record_id", "")), data))
                return
            if parsed.path == "/api/reveal":
                data = self._body_json()
                self.app.store.reveal_audio(str(data.get("record_id", "")))
                self._json({"ok": True})
                return
            if parsed.path == "/api/refresh":
                self.app.store.reload()
                self._json({"ok": True})
                return
            if parsed.path == "/api/open-photos":
                subprocess.run(["/usr/bin/open", "-a", "Photos"], check=False)
                self._json({"ok": True})
                return
            self._text("Not found", HTTPStatus.NOT_FOUND)
        except KeyError:
            self._text("Record not found", HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self._text(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._text(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_audio(self, path: Path) -> None:
        size = path.stat().st_size
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        range_header = self.headers.get("Range", "")
        start, end = 0, size - 1
        status = HTTPStatus.OK

        if range_header.startswith("bytes="):
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:
                    suffix = int(last)
                    start = max(0, size - suffix)
                    end = size - 1
                start = max(0, min(start, size - 1))
                end = max(start, min(end, size - 1))
                status = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=0")
        self.end_headers()

        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


class ReviewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        store: ReviewStore,
        token: str,
        verbose: bool,
    ) -> None:
        super().__init__(address, handler)
        self.store = store
        self.token = token
        self.verbose = verbose


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local browser GUI for reviewing Voice Memos and JPR classifications."
    )
    parser.add_argument("--port", type=int, default=0, help="Local TCP port; default chooses a free port.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    parser.add_argument("--verbose", action="store_true", help="Print HTTP request logs.")
    parser.add_argument("--status", action="store_true", help="Print corpus/review counts and exit.")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument("--classification-root", type=Path, default=CLASSIFICATION_ROOT)
    parser.add_argument("--whisper-root", type=Path, default=WHISPER_ROOT)
    parser.add_argument("--review-root", type=Path, default=REVIEW_ROOT)
    parser.add_argument("--backup-manifest", type=Path, default=BACKUP_MANIFEST)
    parser.add_argument("--voice-archive", type=Path, default=VOICE_ARCHIVE)
    parser.add_argument("--jpr-archive", type=Path, default=JPR_ARCHIVE)
    return parser.parse_args()


def make_store(args: argparse.Namespace) -> ReviewStore:
    return ReviewStore(
        classification_root=args.classification_root.expanduser(),
        whisper_root=args.whisper_root.expanduser(),
        review_root=args.review_root.expanduser(),
        voice_archive=args.voice_archive.expanduser(),
        jpr_archive=args.jpr_archive.expanduser(),
        backup_manifest=args.backup_manifest.expanduser(),
    )


def print_status(store: ReviewStore) -> None:
    payload = store.list_payload()
    counts = payload["counts"]
    print(f"Voice Memo Review {VERSION}")
    print(f"Classification: {store.classification_root}")
    print(f"Whisper:        {store.whisper_root}")
    print(f"Human reviews:  {store.review_root}")
    print(f"Identity DB:    {store.backup_manifest}")
    print(f"Voice archive:  {store.voice_archive}")
    print(f"JPR archive:    {store.jpr_archive}")
    print()
    print(f"Total records:  {counts['total']}")
    print(f"Reviewed:       {counts['reviewed']}")
    print(f"Unreviewed:     {counts['unreviewed']}")
    print(f"Later:          {counts['later']}")
    print(f"Skipped:        {counts['skipped']}")
    missing_audio = sum(1 for row in payload["records"] if not row["audio_available"])
    missing_ai = sum(1 for row in payload["records"] if not row["has_ai"])
    print(f"Missing audio:  {missing_audio}")
    print(f"Missing AI:     {missing_ai}")


def main() -> None:
    args = parse_args()

    if args.version:
        print(f"{PACKAGE_NAME} {VERSION}")
        return

    store = make_store(args)

    if args.status:
        print_status(store)
        return

    token = secrets.token_urlsafe(24)
    server = ReviewerServer(("127.0.0.1", args.port), ReviewerHandler, store, token, args.verbose)
    host, port = server.server_address
    url = f"http://{host}:{port}/"

    print(f"Voice Memo Review {VERSION}")
    print(f"Loaded:       {len(store.records)} recordings")
    print(f"Reviews:      {store.review_root}")
    print(f"Local URL:    {url}")
    print("Privacy:      bound to 127.0.0.1; this app makes no network requests.")
    print()
    print("Close with Ctrl-C. The browser is only the local front end.")

    if not args.no_open:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\nVoice Memo Review stopped.")


if __name__ == "__main__":
    main()
