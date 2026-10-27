#!/usr/bin/env python3
# filename: innermaze.py
# version: 0.1.5
# timestamp: 2026-10-26 14:34:00
"""Innermaze Mango-local audio importer and transcription worker; Obsidian plugin installed via BRAT.

Dry-run default for imports/install. Archives and source audio are read-only.
One iCloud vault playback copy per SHA-256, with editable plain Markdown and
external word-timed JSON. Does not change Cashew's archival service.
"""
from __future__ import annotations
import argparse
import csv
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import quote

HOME = Path.home()
WHISPER = HOME / "Code/VoiceMemoSaver/Whisper"
BACKUP_DB = HOME / "Library/Application Support/VoiceAudioBackup/manifest.sqlite3"
SOURCES = {
    "voice-memos": HOME / "Dropbox/1 NOTES/Voice Memos Full DB",
    "just-press-record": HOME / "Dropbox/1 NOTES/Just Press Record Full DB",
}
MLX_PYTHON = HOME / "Code/voxtral-tts-codes-for-audio/.venv/bin/python3"
MODEL = "mlx-community/whisper-large-v3-turbo"
TEMP_DIR = HOME / "Code/.staging/Innermaze"
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aif", ".aiff", ".caf", ".flac", ".ogg", ".opus", ".aac", ".m4b", ".wma", ".amr", ".mp4", ".webm", ".qta"}
IOS_PLAYABLE = {".m4a", ".mp3", ".wav", ".aif", ".aiff", ".aac", ".m4b", ".mp4", ".caf"}
EXTS = AUDIO_EXTS


def step(n: int, total: int, message: str) -> None:
    width = 20
    filled = min(width, width * n // total)
    print(f"[{'#' * filled}{'.' * (width - filled)}] {n}/{total} | {message}", flush=True)

def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def records_for(source: str, whisper_root: Path) -> list[dict[str, str]]:
    path = whisper_root / source / "identity.tsv"
    if not path.is_file():
        raise ValueError(f"Whisper identity index missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        stem = (row.get("output_stem") or "").strip()
        if stem and row.get("source_path"):
            index.setdefault(stem, row)  # earliest identity owns the stem
    return [index[stem] for stem in sorted(index)]


def cached_transcript(digest: str, source: Path | None, whisper_root: Path) -> dict[str, Any] | None:
    """Reuse the legacy Whisper JSON by SOURCE audio hash, never by mutable filename.

    Duplicate indices are harmless only when their transcript content agrees.
    When distinct transcripts disagree, an exact indexed source path may break
    the tie; otherwise fail closed instead of selecting arbitrary text/times.
    """
    candidates: list[tuple[str, dict[str, str], Path]] = []
    normalized = digest.lower()
    for kind in SOURCES:
        index = whisper_root / kind / "identity.tsv"
        if not index.is_file():
            continue
        for row in records_for(kind, whisper_root):
            if (row.get("sha256") or "").lower() != normalized:
                continue
            json_path = whisper_root / kind / (row["output_stem"] + ".json")
            if json_path.is_file():
                candidates.append((kind, row, json_path))
    if not candidates:
        return None
    if source is not None:
        exact = [entry for entry in candidates if
                 Path(entry[1]["source_path"]).expanduser().absolute() == source.expanduser().absolute()]
        if exact:
            candidates = exact
        else:
            # Matching the canonical Dropbox source disambiguates cross-app
            # duplicate audio, but never multiple distinct candidates in one app.
            kinds = [kind for kind, root in SOURCES.items() if source.expanduser().absolute().is_relative_to(root.expanduser().absolute())]
            within = [entry for entry in candidates if entry[0] in kinds]
            if within:
                candidates = within
    parsed = []
    for kind, row, json_path in candidates:
        try:
            data = json.loads(json_path.read_text("utf-8"))
        except (ValueError, OSError) as error:
            raise ValueError(f"Cached Whisper JSON unreadable: {json_path}: {error}") from error
        if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
            raise ValueError(f"Invalid cached Whisper transcript: {json_path}")
        parsed.append((kind, row, json_path, data))
    # All distinct candidates must contain identical transcription/timing.
    # JSON keys/order may differ; compare semantic contents, not serialized bytes.
    representations = {json.dumps(item[3].get("segments"), sort_keys=True, ensure_ascii=False)
                       for item in parsed}
    if len(representations) != 1:
        raise ValueError("More than one different cached transcript has this audio SHA-256; "
                         "refusing to guess timing. Select a specific archive source.")
    kind, row, path, data = parsed[0]
    print(f"  Reusing {kind} Whisper transcript: {row['output_stem']} (no new MLX job)")
    return {"source": kind, "stem": row["output_stem"], "json_path": str(path), "data": data}


def archived_uuid(digest: str, source: Path | None, manifest: Path) -> str | None:
    """Resolve Apple UUID only when the manifest establishes a unique owner.

    An unavailable Cashew manifest is not an excuse to invent an Apple UUID.
    """
    if not manifest.is_file():
        return None
    uri = "file:" + quote(str(manifest.resolve()), safe="/") + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as db:
            matches = db.execute("""
                SELECT o.dest_rel, r.logical_id FROM objects o
                JOIN object_recordings r ON o.archive=r.archive AND o.dest_rel=r.dest_rel
                WHERE o.archive='voice_memos' AND LOWER(o.sha256)=?
            """, (digest.lower(),)).fetchall()
    except sqlite3.Error as error:
        raise ValueError(f"Cannot read Apple UUID manifest: {manifest}: {error}") from error
    if not matches:
        return None
    if source is not None:
        root = SOURCES["voice-memos"].expanduser().absolute()
        source_abs = source.expanduser().absolute()
        if source_abs.is_relative_to(root):
            rel = source_abs.relative_to(root).as_posix()
            exact = [match for match in matches if match[0] == rel]
            if exact:
                matches = exact
    owners = {item[1] for item in matches}
    if len(owners) != 1:
        # Shared identical bytes can belong to multiple distinct Voice Memos.
        # Caller retains a content-based identity instead of claiming a UUID.
        print("  Apple UUID unresolved: identical audio has multiple archive owners")
        return None
    logical_id = owners.pop()
    if not re.fullmatch(r"voice-memos:uuid:[A-Za-z0-9-]+", logical_id):
        raise ValueError(f"Unexpected Apple recording identity in manifest: {logical_id}")
    return "vm-" + logical_id.removeprefix("voice-memos:uuid:")


def recording_notes(vault: Path) -> list[Path]:
    """Find marked recording notes at archive root and in legacy Notes/ only."""
    result = []
    for directory in (vault / "Voice Archive", vault / "Voice Archive/Notes"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                fields, _ = parse_note(path.read_text("utf-8"))
            except ValueError:
                # Ordinary Markdown notes may share the archive root.
                continue
            if str(fields.get("voice_archive")).lower() == "true" and fields.get("recording_id"):
                result.append(path)
    return result


def matching_note(vault: Path, digest: str, preferred_id: str | None = None) -> Path | None:
    """Reuse a human-renamed note by immutable metadata, not filename."""
    notes = []
    for note in recording_notes(vault):
        fields, _ = parse_note(note.read_text("utf-8"))
        identity = fields.get("recording_id")
        hash_value = fields.get("source_sha256")
        if preferred_id and identity in (preferred_id, f"audio-{digest}") and hash_value != digest:
            raise ValueError(f"Recording ID is already associated with another audio file: {note}")
        if hash_value == digest:
            notes.append((note, fields))
    if preferred_id:
        exact = [(note,fields) for note,fields in notes if fields.get("recording_id") == preferred_id]
        if len(exact) > 1:
            raise ValueError("Multiple notes share the preferred recording ID")
        if exact:
            return exact[0][0]
        generic = [(note,fields) for note,fields in notes if fields.get("recording_id") == f"audio-{digest}"]
        if len(generic) > 1:
            raise ValueError("Multiple notes share the source hash")
        if generic:
            return generic[0][0]
        return None  # Distinct Apple UUIDs must never be merged.
    if len(notes) == 1:
        return notes[0][0]
    if len(notes) > 1:
        raise ValueError("Audio matches multiple existing archive notes; UUID required")
    return None


def sync_from_transcript(transcript: dict[str, Any], identity: str, digest: str,
                         playback_digest: str) -> dict[str, Any]:
    data = transcript["data"]
    return {"format": "voice-archive-sync-v1", "recording_id": identity,
            "source": transcript["source"], "source_sha256": digest,
            "source_whisper_stem": transcript["stem"],
            "playback_sha256": playback_digest,
            "blocks": make_blocks(data, identity), "words": valid_words(data)}


def valid_words(data: dict[str, Any]) -> list[dict[str, Any]]:
    words = []
    for segment in data.get("segments", []):
        for word in segment.get("words") or []:
            start, end = word.get("start"), word.get("end")
            if isinstance(start, (float, int)) and isinstance(end, (float, int)) and end >= start:
                value = str(word.get("word") or "")
                if value.strip():
                    words.append({"start": float(start), "end": float(end), "text": value})
    if words:
        return sorted(words, key=lambda word: word["start"])
    # Fallback for Whisper outputs without word timestamps: segment timing.
    for segment in data.get("segments", []):
        start, end = segment.get("start"), segment.get("end")
        if isinstance(start, (float, int)) and isinstance(end, (float, int)) and end >= start:
            value = str(segment.get("text") or "")
            if value.strip():
                words.append({"start": float(start), "end": float(end), "text": value})
    return sorted(words, key=lambda word: word["start"])


def join_words(words: list[dict[str, Any]]) -> str:
    return "".join(word["text"] for word in words).strip()


def make_blocks(data: dict[str, Any], identity: str) -> list[dict[str, Any]]:
    words = valid_words(data)
    if not words:
        return []
    blocks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        text = join_words(current)
        if text:
            start = round(current[0]["start"], 3)
            end = round(current[-1]["end"], 3)
            idx = len(blocks)
            blocks.append({"id": f"{identity}-{idx:05d}", "start": start, "end": end, "text": text})
        current.clear()

    for word in words:
        if current and (word["start"] - current[-1]["end"] > 1.2):
            flush()
        current.append(word)
        text = join_words(current)
        duration = word["end"] - current[0]["start"]
        sentence_end = bool(re.search(r'[.!?。！？][\"”’\']?\s*$', text))
        if (sentence_end and len(text) >= 25) or len(text) >= 105 or duration >= 9:
            flush()
    flush()
    return blocks


def find_recording(row: dict[str, str], source_root: Path) -> Path:
    """Resolve SHA-verified original in canonical Dropbox archive, never live iCloud."""
    expected = (row.get("sha256") or "").lower()
    if not expected:
        raise ValueError("Whisper identity row has no audio SHA-256")
    candidate = Path(row["source_path"]).expanduser()
    if candidate.is_file() and candidate.suffix.lower() in EXTS:
        if candidate.resolve().is_relative_to(source_root.resolve()) and digest_file(candidate) == expected:
            return candidate
    if not source_root.is_dir():
        raise ValueError(f"Audio archive missing: {source_root}")
    matches = []
    for path in sorted(source_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in EXTS and digest_file(path) == expected:
            matches.append(path)
            if len(matches) > 1:
                raise ValueError("Multiple archived audio files share this SHA-256; refusing ambiguous identity")
    if matches:
        return matches[0]
    raise ValueError(f"No archived audio matching Whisper identity: {row['output_stem']}")


def find_apple_recording(row: dict[str, str], archive: Path, manifest: Path) -> tuple[Path, str]:
    """Only Apple ZUNIQUEID is identity; duplicate audio hashes must not merge."""
    if not manifest.is_file():
        raise ValueError(f"Apple UUID manifest missing: {manifest}")
    digest = (row.get("sha256") or "").lower()
    if not digest:
        raise ValueError("Whisper identity row has no audio SHA-256")
    uri = "file:" + quote(str(manifest.resolve()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        matches = db.execute("""
            SELECT o.dest_rel, r.logical_id
            FROM objects o JOIN object_recordings r
              ON o.archive=r.archive AND o.dest_rel=r.dest_rel
            WHERE o.archive='voice_memos' AND LOWER(o.sha256)=?
            ORDER BY o.dest_rel
        """, (digest,)).fetchall()
    if not matches:
        raise ValueError("Archived SHA-256 is missing from UUID manifest (run voice_audio_backup verify)")
    source_path = Path(row["source_path"]).expanduser()
    # If two Apple recordings contain identical bytes, source archive path disambiguates.
    if source_path.is_relative_to(archive):
        source_rel = source_path.relative_to(archive).as_posix()
        specific = [item for item in matches if item[0] == source_rel]
        if specific:
            matches = specific
    owners = {item[1] for item in matches}
    if len(owners) != 1:
        raise ValueError("Same audio is owned by multiple Apple UUIDs; cannot match this Whisper stem safely")
    rel, logical_id = matches[0]
    if not re.fullmatch(r"voice-memos:uuid:[A-Za-z0-9-]+", logical_id):
        raise ValueError(f"Unexpected Apple recording identity: {logical_id}")
    path = archive / rel
    if not path.is_file() or not path.resolve().is_relative_to(archive.resolve()):
        raise ValueError(f"UUID-owned archived audio missing or outside archive: {path}")
    if digest_file(path) != digest:
        raise ValueError(f"SHA-256 mismatch on UUID-owned archived audio: {path}")
    return path, logical_id.removeprefix("voice-memos:uuid:")


def yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_note(identity: str, source: str, stem: str, audio_rel: str, sync_rel: str,
                blocks: list[dict[str, Any]], original_path: Path) -> str:
    nice_title = re.sub(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}[-:]\d{2}[-:]\d{2}\s*-\s*", "", original_path.stem)
    lines = [
        "---", "voice_archive: true", f"recording_id: {yaml_string(identity)}",
        f"recording_source: {yaml_string(source)}",
        f"source_title: {yaml_string(original_path.stem)}",
        f"display_title: {yaml_string(nice_title)}",
        'title_source: "archive"',
        "rename_history: []",
        f"voice_audio: {yaml_string(audio_rel)}",
        f"voice_sync: {yaml_string(sync_rel)}",
        "tags:", "  - voice-archive", "---", "",
    ]
    if not blocks:
        lines.extend(["*No speech detected in this recording.*", ""])
    else:
        lines.extend(["\n\n".join(block["text"] for block in blocks), ""])
    return "\n".join(lines)


def atomic_create(destination: Path, source: Path | bytes, temp_root: Path) -> None:
    """Create only; refuse to replace existing or raced destinations."""
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="voice-archive-", dir=temp_root)
    try:
        with os.fdopen(fd, "wb") as out:
            if isinstance(source, Path):
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, out, length=1024 * 1024)
            else:
                out.write(source)
            out.flush()
            os.fsync(out.fileno())
        # os.link is create-only and atomic; no silent replacement on rerun.
        try:
            os.link(name, destination)
        except FileExistsError:
            return
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            # Across filesystems, hardlinking is unavailable: use create-only copy.
            try:
                handle = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                return
            try:
                with os.fdopen(handle, "wb") as out, open(name, "rb") as stream:
                    shutil.copyfileobj(stream, out, length=1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
    finally:
        Path(name).unlink(missing_ok=True)



def parse_yaml_value(raw: str) -> str:
    raw = raw.strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, str) else str(value)
    except json.JSONDecodeError:
        return raw.strip("\"'")


def parse_note(raw: str) -> tuple[dict[str, str], str]:
    match = re.match(r"\A---\r?\n([\s\S]*?)\r?\n---\r?\n", raw)
    if not match:
        raise ValueError("Missing Markdown frontmatter")
    fields = {}
    for line in match.group(1).splitlines():
        found = re.match(r"^([a-z0-9_]+):\s*(.*?)\s*$", line)
        if found:
            fields[found.group(1)] = parse_yaml_value(found.group(2))
    return fields, raw[match.end():]


def source_in_vault(source: Path, vault: Path) -> str | None:
    resolved = source.resolve(strict=True)
    try:
        return resolved.relative_to(vault.resolve()).as_posix()
    except ValueError:
        return None


def find_managed_audio(vault: Path, digest: str) -> Path | None:
    root = vault / "Voice Archive/Audio"
    if not root.is_dir():
        return None
    candidates = sorted(root.glob(f"sha256-{digest}.*"))
    for path in candidates:
        if path.is_file() and digest_file(path) == digest:
            return path
    if candidates:
        # Audio transcoded to M4A retains the original *source* hash in its
        # filename but has different playback bytes. Only trust an existing
        # matching note which records both hashes explicitly.
        if len(candidates) == 1:
            for proof in recording_notes(vault):
                fields, _ = parse_note(proof.read_text("utf-8"))
                if (fields.get("source_sha256") == digest and
                    fields.get("voice_audio") == candidates[0].relative_to(vault).as_posix() and
                    fields.get("playback_sha256") == digest_file(candidates[0])):
                    return candidates[0]
        raise ValueError("Audio hash-named destination exists without verified original/playback association")
    return None


def import_any(source: Path, vault: Path, apply: bool, auto_transcribe: bool,
               model_python: Path, model: str, whisper_root: Path = WHISPER,
               manifest_db: Path = BACKUP_DB, *, identity_hint: str | None = None,
               reuse_cached: bool = True, source_kind: str = 'arbitrary-file',
               source_title: str | None = None) -> Path | None:
    step(1, 6, "Validate the arbitrary audio source and destination vault")
    source = source.expanduser().absolute()
    if not source.is_file():
        raise ValueError(f"Audio not found: {source}")
    if source.suffix.lower() not in AUDIO_EXTS:
        # Don't guess from extensions. Probe uncommon files, including video
        # containers with an audio track, and create an iOS-ready derivative.
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise ValueError("Unfamiliar audio format: install ffmpeg/ffprobe to inspect it")
        probe = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0",
                                "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                                str(source)], capture_output=True, text=True)
        if probe.returncode != 0 or "audio" not in probe.stdout:
            raise ValueError(f"No readable audio stream detected in {source}")
    if not (vault / ".obsidian").is_dir():
        raise ValueError(f"Selected directory is not an existing Obsidian vault: {vault}")
    step(2, 6, "SHA-256 content identity: one playback asset per source")
    digest = digest_file(source)
    # Preserve Apple identity when uniquely attested by a readable manifest.
    apple_id = identity_hint or archived_uuid(digest, source, manifest_db)
    prior_note = matching_note(vault, digest, apple_id)
    identity = (parse_note(prior_note.read_text("utf-8"))[0]["recording_id"] if prior_note else apple_id or f"audio-{digest}")
    inside = source_in_vault(source, vault)
    existing = find_managed_audio(vault, digest)
    if existing is None and prior_note is not None:
        saved_fields, _ = parse_note(prior_note.read_text("utf-8"))
        old_rel = saved_fields.get("voice_audio", "")
        old_asset = (vault / old_rel).resolve() if old_rel else None
        if old_asset and old_asset.is_file() and old_asset.is_relative_to(vault.resolve()):
            if digest_file(old_asset) == saved_fields.get("playback_sha256", digest):
                existing = old_asset
    # Validate the legacy index before any vault writes (ambiguity fails closed).
    transcript_available = (cached_transcript(digest, source, whisper_root) is not None
                            if auto_transcribe or reuse_cached else False)
    convert = existing is None and source.suffix.lower() not in IOS_PLAYABLE
    if convert:
        inside = None  # Native iOS playback requires an M4A derivative.
    extension = ".m4a" if convert else source.suffix.lower()
    audio_rel = (existing.relative_to(vault).as_posix() if existing else
                 inside if inside is not None else f"Voice Archive/Audio/sha256-{digest}{extension}")
    note_rel = (prior_note.relative_to(vault).as_posix() if prior_note else f"Voice Archive/{identity}.md")
    sync_rel = f"Voice Archive/Sync/{identity}.sync.json"
    note = vault / note_rel
    if prior_note is None and note.exists():
        raise ValueError(f"Destination note already exists but does not match recording identity: {note}")
    asset = vault / audio_rel
    step(3, 6, "Plan the iCloud listening copy and clean Markdown")
    print(f"  Source: {source}")
    print(f"  Identity: sha256:{digest}")
    print(f"  Audio: {asset} ({'REUSE' if inside or existing else 'M4A CONVERT' if convert else 'COPY'})")
    print(f"  Note: {note} ({'KEEP' if note.exists() else 'CREATE'})")
    if not apply:
        step(6, 6, "DRY RUN: source, vault, archive, and notes unchanged")
        return None
    step(4, 6, "Make one vault playback asset; verify integrity")
    if not (inside or existing):
        if convert:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise ValueError("ffmpeg required to convert this audio format to iOS-compatible M4A")
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="convert-", dir=TEMP_DIR) as temp:
                rendered = Path(temp) / "listening.m4a"
                command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                           "-i", str(source), "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "192k", str(rendered)]
                subprocess.run(command, check=True)
                if not rendered.is_file() or rendered.stat().st_size == 0:
                    raise ValueError("ffmpeg produced no playable audio")
                atomic_create(asset, rendered, TEMP_DIR)
                if digest_file(asset) != digest_file(rendered):
                    raise ValueError("Converted audio verification failed")
        else:
            atomic_create(asset, source, TEMP_DIR)
            if digest_file(asset) != digest:
                raise ValueError("Vault playback audio verification failed")
    if not asset.is_file():
        raise ValueError("No vault audio is available")
    step(5, 6, "Create or preserve editable note and external timestamp link")
    playback_digest = digest_file(asset)
    raw_note = make_arbitrary_note(identity, source_title or source.name, audio_rel, sync_rel,
                                   digest, playback_digest, source_kind=source_kind)
    atomic_create(note, raw_note.encode("utf-8"), TEMP_DIR)
    step(6, 6, "Imported and queued: nothing changed in source or archive")
    # Existing Whisper timing is reusable even with --no-transcribe and even
    # when MLX isn't installed. New Whisper jobs alone obey --no-transcribe.
    if auto_transcribe or (transcript_available and reuse_cached):
        process_one(note, vault, model_python, model, whisper_root, manifest_db,
                    allow_new_transcription=auto_transcribe)
    return note


def make_arbitrary_note(identity: str, title: str, audio_rel: str,
                        sync_rel: str, digest: str, playback_digest: str,
                        source_kind: str = 'arbitrary-file') -> str:
    return "\n".join([
        "---", "voice_archive: true", f"recording_id: {yaml_string(identity)}",
        f'recording_source: {yaml_string(source_kind)}', f"source_title: {yaml_string(title)}",
        f"display_title: {yaml_string(Path(title).stem)}",
        f"voice_audio: {yaml_string(audio_rel)}", f"voice_sync: {yaml_string(sync_rel)}",
        f"source_sha256: {yaml_string(digest)}",
        f"playback_sha256: {yaml_string(playback_digest)}", 'transcription_status: "pending"',
        "tags:", "  - voice-archive", "---", "", "",
    ])


# Separate process ensures MLX, CoreML, and model caches cannot destabilize Obsidian.
WHISPER_RUNNER = r'''
import json
from pathlib import Path
import sys
import mlx_whisper

def main():
    source, output, model = sys.argv[1:4]
    result = mlx_whisper.transcribe(source, path_or_hf_repo=model, verbose=None,
                                   word_timestamps=True)
    path = Path(output)
    def native_json(value):
        if hasattr(value, "tolist"):
            return value.tolist()
        if hasattr(value, "item"):
            return value.item()
        raise TypeError(f"Unknown Whisper JSON value: {type(value)}")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, default=native_json)
        handle.flush()

def guard():
    main()

guard()
'''


def atomic_replace_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".vap-write-", suffix=".tmp", dir=path.parent,
                                     delete=False) as stream:
        staged = Path(stream.name)
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def process_one(note: Path, vault: Path, model_python: Path, model: str,
                whisper_root: Path = WHISPER, manifest_db: Path = BACKUP_DB,
                allow_new_transcription: bool = True) -> bool:
    raw_before = note.read_text("utf-8")
    fields, body = parse_note(raw_before)
    if fields.get("transcription_status") != "pending":
        print(f"  SKIP (not pending): {note.name}")
        return False
    if body.strip():
        print(f"  HOLD (human text present; refusing overwrite): {note.name}")
        return False
    audio_rel = fields.get("voice_audio", "")
    sync_rel = fields.get("voice_sync", "")
    identity = fields.get("recording_id", "")
    if not audio_rel or not sync_rel or not identity:
        raise ValueError(f"Missing audio/sync/identity in {note}")
    audio = (vault / audio_rel).resolve()
    sync = (vault / sync_rel).resolve()
    if not audio.is_relative_to(vault.resolve()) or not sync.is_relative_to(vault.resolve()):
        raise ValueError(f"Attempted out-of-vault access in {note}")
    if not audio.is_file():
        print(f"  HOLD (audio hasn't downloaded from iCloud yet): {audio_rel}")
        return False
    digest_expected = fields.get("playback_sha256") or fields.get("source_sha256")
    if digest_expected and digest_file(audio) != digest_expected:
        raise ValueError(f"Vault playback audio SHA-256 mismatch: {audio}")
    if sync.is_file():
        sync_data = json.loads(sync.read_text("utf-8"))
        if sync_data.get("recording_id") != identity or not isinstance(sync_data.get("blocks"), list):
            raise ValueError("Existing sidecar has wrong recording identity or format")
        print("  Existing sync sidecar found: recovering interrupted note publication")
    else:
        source_digest = fields.get("source_sha256", "")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", source_digest):
            raise ValueError(f"Missing/invalid source audio SHA-256: {note}")
        source_path = None
        # Only use the vault file as an archive-path hint when it's actually
        # the original bytes (e.g. audio directly within the vault).
        if digest_file(audio) == source_digest:
            source_path = audio
        transcript = cached_transcript(source_digest, source_path, whisper_root)
        if transcript is not None:
            step(1, 3, "Found original word timestamps; skip Whisper transcription")
            sync_data = sync_from_transcript(transcript, identity, source_digest, digest_file(audio))
            step(2, 3, f"Reuse {len(sync_data['blocks'])} line anchors and {len(sync_data['words'])} timed words")
            atomic_create(sync, (json.dumps(sync_data, ensure_ascii=False, indent=2) + "\n").encode(), TEMP_DIR)
        elif not allow_new_transcription:
            print("  PENDING: no indexed Whisper transcript and new transcription disabled")
            return False
        else:
            step(1, 3, f"Transcribe {note.name} using local MLX Whisper on Mango")
            if not model_python.is_file():
                raise ValueError(f"Installed Mango MLX Python not found: {model_python}")
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="whisper-", dir=TEMP_DIR) as tmp:
                temp_json = Path(tmp) / "whisper.json"
                subprocess.run([str(model_python), "-c", WHISPER_RUNNER, str(audio),
                                str(temp_json), model], check=True)
                data = json.loads(temp_json.read_text("utf-8"))
                blocks = make_blocks(data, identity)
                words = valid_words(data)
                sync_data = {"format": "voice-archive-sync-v1", "recording_id": identity,
                             "source": "mlx-whisper", "model": model,
                             "playback_sha256": digest_file(audio),
                             "blocks": blocks, "words": words}
                step(2, 3, f"Publish {len(blocks)} line anchors and {len(words)} timed words")
                atomic_create(sync, (json.dumps(sync_data, ensure_ascii=False, indent=2) + "\n").encode(), TEMP_DIR)
    if note.read_text("utf-8") != raw_before:
        print("  HOLD: note changed during transcription; never overwriting edits")
        return False
    new_body = "\n\n".join(str(block["text"]) for block in sync_data["blocks"])
    updated_header = raw_before.replace('transcription_status: "pending"',
                                        'transcription_status: "ready"', 1)
    # Mobile created a hash-named note before Mango could see Apple's manifest.
    # Keep its path/links stable and attach the verified UUID as metadata.
    if identity.startswith("audio-"):
        archive_id = archived_uuid(fields.get("source_sha256", ""), None, manifest_db)
        if archive_id and not fields.get("archive_recording_id"):
            updated_header = updated_header.replace('transcription_status: "ready"',
                                                    f'archive_recording_id: {yaml_string(archive_id)}\n'
                                                    'transcription_status: "ready"', 1)
    if updated_header == raw_before:
        raise ValueError("Pending note status marker missing; refusing overwrite")
    atomic_replace_bytes(note, (updated_header.rstrip() + "\n\n" + new_body + "\n").encode("utf-8"))
    step(3, 3, "Completed local transcription; readable Markdown and alignment synchronized")
    return True


def process_pending(vault: Path, model_python: Path, model: str, limit: int,
                    whisper_root: Path = WHISPER, manifest_db: Path = BACKUP_DB) -> None:
    if not (vault / "Voice Archive").is_dir():
        print("No imported recordings pending; Voice Archive does not exist.")
        return
    # Cached transcription reuse works even when MLX is not installed.
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with (TEMP_DIR / "transcription.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another local transcription worker is running; avoiding overlap.")
            return
        candidates = []
        for note in recording_notes(vault):
            fields, _ = parse_note(note.read_text("utf-8"))
            if fields.get("transcription_status") == "pending":
                candidates.append(note)
        print(f"[{'#' * 20}] 1/1 | Pending: {len(candidates)}; limit: {limit}")
        for index, note in enumerate(candidates[:limit], 1):
            print(f"[{index}/{min(len(candidates), limit)}] {note.name}")
            process_one(note, vault, model_python, model, whisper_root, manifest_db)


def import_legacy(vault: Path, source_type: str, stem: str | None, pilot_first: bool,
                  whisper_root: Path, audio_root: Path | None,
                  manifest_db: Path, apply: bool) -> None:
    step(1, 5, "Resolve archived Voice Memo or JPR transcription by existing identity")
    rows = records_for(source_type, whisper_root)
    complete = [row for row in rows if (whisper_root / source_type /
                (row["output_stem"] + ".json")).is_file()]
    if not complete:
        raise ValueError("No complete Whisper transcription found in existing output")
    if stem:
        selected = [row for row in complete if row["output_stem"] == stem]
        if len(selected) != 1:
            raise ValueError("Requested Whisper stem missing or ambiguous")
        row = selected[0]
    elif pilot_first:
        row = complete[0]
    else:
        raise ValueError("Specify --stem or --pilot-first for an archived recording")
    source_root = (audio_root or SOURCES[source_type]).expanduser()
    if source_type == "voice-memos":
        audio, apple_uuid = find_apple_recording(row, source_root, manifest_db)
    else:
        audio, apple_uuid = find_recording(row, source_root), None
    step(2, 5, "Reuse existing Whisper word-timed transcription")
    data = json.loads((whisper_root / source_type / (row["output_stem"] + ".json")).read_text("utf-8"))
    digest = digest_file(audio)
    archive_identity = f"vm-{apple_uuid}" if apple_uuid else f"jpr-{digest[:24]}"
    prior_note = matching_note(vault, digest, archive_identity)
    identity = (parse_note(prior_note.read_text("utf-8"))[0]["recording_id"] if prior_note else archive_identity)
    blocks = make_blocks(data, identity)
    step(3, 5, "Plan archival recording note and managed listening asset")
    # Keep previous pilot paths and stable logical IDs; audio de-duplicate when possible.
    existing = find_managed_audio(vault, digest)
    previous_note = prior_note if prior_note is not None else vault / f"Voice Archive/{identity}.md"
    # Version 1.0 stored Apple UUID-named assets. Reuse those exact verified
    # originals rather than create an orphaned second listening copy.
    if not existing and previous_note.is_file():
        previous_fields, _ = parse_note(previous_note.read_text("utf-8"))
        old_rel = previous_fields.get("voice_audio", "")
        if old_rel:
            old_asset = (vault / old_rel).resolve()
            if old_asset.is_file() and old_asset.is_relative_to(vault.resolve()) and digest_file(old_asset) == digest:
                existing = old_asset
    playback_ext = ".m4a" if audio.suffix.lower() not in IOS_PLAYABLE else audio.suffix.lower()
    audio_rel = (existing.relative_to(vault).as_posix() if existing else
                 f"Voice Archive/Audio/sha256-{digest}{playback_ext}")
    note_rel = (prior_note.relative_to(vault).as_posix() if prior_note else f"Voice Archive/{identity}.md")
    sync_rel = f"Voice Archive/Sync/{identity}.sync.json"
    if prior_note is not None:
        saved_fields, _ = parse_note(prior_note.read_text("utf-8"))
        audio_rel = saved_fields["voice_audio"]
        sync_rel = saved_fields["voice_sync"]
    if prior_note is None and (vault / note_rel).exists():
        raise ValueError(f"Destination note already exists with an unrelated identity: {note_rel}")
    for rel in [audio_rel, sync_rel, note_rel]:
        print(f"  {'KEEP' if (vault / rel).is_file() else 'CREATE'} {vault / rel}")
    if not apply:
        step(5, 5, "DRY RUN: preserved all originals and vault contents")
        return
    step(4, 5, "Verified copy and independent transcript sidecar")
    asset = vault / audio_rel
    if not asset.is_file():
        if audio.suffix.lower() not in IOS_PLAYABLE:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise ValueError("ffmpeg is required for an iOS-compatible archive playback copy")
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="archive-m4a-", dir=TEMP_DIR) as tmp:
                converted = Path(tmp) / "listening.m4a"
                subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                                "-y", "-i", str(audio), "-map", "0:a:0", "-vn",
                                "-c:a", "aac", "-b:a", "192k", str(converted)], check=True)
                atomic_create(asset, converted, TEMP_DIR)
                if digest_file(asset) != digest_file(converted):
                    raise ValueError("Archive playback copy verification failed")
        else:
            atomic_create(asset, audio, TEMP_DIR)
    if audio.suffix.lower() in IOS_PLAYABLE and digest_file(asset) != digest:
        raise ValueError("Vault audio differs from the original archive")
    sidecar = {"format": "voice-archive-sync-v1", "recording_id": identity,
               "source": source_type, "source_sha256": digest,
               "source_whisper_stem": row["output_stem"], "blocks": blocks,
               "words": valid_words(data)}
    atomic_create(vault / sync_rel, (json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n").encode(), TEMP_DIR)
    note_data = render_note(identity, source_type, row["output_stem"], audio_rel,
                            sync_rel, blocks, audio)
    note_data = note_data.replace("tags:\n", f"source_sha256: {yaml_string(digest)}\n"
                                  f"playback_sha256: {yaml_string(digest_file(asset))}\n"
                                  "tags:\n", 1)
    atomic_create(vault / note_rel, note_data.encode(), TEMP_DIR)
    if prior_note is not None:
        fields, body = parse_note(prior_note.read_text("utf-8"))
        if fields.get("transcription_status") == "pending" and not body.strip():
            process_one(prior_note, vault, Path("/nonexistent-mlx-python"), MODEL, whisper_root,
                        manifest_db, allow_new_transcription=False)
    step(5, 5, "Archive identity and existing transcript preserved without retranscription")



def resolve_vault(provided: Path | None) -> Path:
    if provided:
        return provided.expanduser().absolute()
    root = HOME / "Library/Mobile Documents/iCloud~md~obsidian/Documents"
    matches = sorted(p for p in root.iterdir() if p.is_dir() and (p / ".obsidian").is_dir()) if root.is_dir() else []
    if len(matches) == 1:
        print(f"Selected sole iCloud Obsidian vault: {matches[0]}")
        return matches[0]
    if matches:
        raise ValueError("Multiple iCloud Obsidian vaults; specify --vault: " + "; ".join(str(p) for p in matches))
    raise ValueError("No single iCloud Obsidian vault found. Specify --vault with its full path")


def install_worker(vault: Path, apply: bool) -> None:
    """Optional Mango-only transcription queue; never touches Cashew archive."""
    label = "com.ak.innermaze-transcription"
    program = HOME / "Code/LLMRunner/bin/innermaze"
    plist = HOME / "Library/LaunchAgents" / (label + ".plist")
    args = [str(program), "--vault", str(vault), "--process-pending", "--apply", "--limit", "2"]
    info = {"Label": label, "ProgramArguments": args, "RunAtLoad": True,
            "StartInterval": 600,
            "EnvironmentVariables": {"PATH": "/Users/ak/Code/LLMRunner/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
            "StandardOutPath": str(HOME / "Library/Logs/innermaze.log"),
            "StandardErrorPath": str(HOME / "Library/Logs/innermaze.error.log")}
    content = plistlib.dumps(info, sort_keys=True)
    step(1, 4, "Validate stable LLMRunner package and Mango MLX backend")
    if not program.is_file() or not os.access(program, os.X_OK):
        raise ValueError(f"Package the standalone file first, then verify executable: {program}")
    if not MLX_PYTHON.is_file():
        raise ValueError(f"MLX Whisper Python is not installed here: {MLX_PYTHON} (Mango only)")
    step(2, 4, "Plan background processing every ten minutes")
    print("  " + " ".join(args))
    print(f"  LaunchAgent: {plist}")
    if plist.exists() and plist.read_bytes() != content:
        raise ValueError(f"Different LaunchAgent already installed; preserving it: {plist}")
    if not apply:
        step(4, 4, "DRY RUN: no LaunchAgent written or loaded")
        return
    step(3, 4, "Create non-destructive LaunchAgent and register it")
    atomic_create(plist, content, TEMP_DIR)
    target = f"gui/{os.getuid()}/{label}"
    status = subprocess.run(["/bin/launchctl", "print", target], capture_output=True, text=True)
    if status.returncode != 0:
        subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], check=True)
    step(4, 4, "Ready: Mango processes iCloud-queued transcripts without touching Cashew")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, help="Existing iCloud Drive Obsidian vault")
    choice = parser.add_mutually_exclusive_group(required=False)
    choice.add_argument("--file", type=Path, help="Arbitrary audio file anywhere on Mango")
    choice.add_argument("--source", choices=tuple(SOURCES), help="Legacy archived Voice Memos/JPR index")
    choice.add_argument("--process-pending", action="store_true", help="Transcribe queued iPhone/Mac imports on Mango")
    choice.add_argument("--install-worker", action="store_true", help="Install Mango 10-minute local Whisper queue worker")
    parser.add_argument("--stem", help="Whisper output stem for --source")
    parser.add_argument("--pilot-first", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Make changes (default is dry-run)")
    parser.add_argument("--no-transcribe", action="store_true", help="Import only; transcribe on Mango later")
    parser.add_argument("--limit", type=int, default=5, help="Maximum pending recordings processed")
    parser.add_argument("--mlx-python", type=Path, default=MLX_PYTHON)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--whisper-root", type=Path, default=WHISPER)
    parser.add_argument("--audio-root", type=Path)
    parser.add_argument("--manifest-db", type=Path, default=BACKUP_DB)
    args = parser.parse_args()
    try:
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        if args.source and args.list:
            rows = records_for(args.source, args.whisper_root.expanduser())
            for row in rows[:30]:
                print(row["output_stem"])
            return
        vault = resolve_vault(args.vault)
        if not (vault / ".obsidian").is_dir():
            raise ValueError(f"Expected existing Obsidian vault at {vault}")
        model_python = args.mlx_python.expanduser()
        if args.install_worker:
            install_worker(vault, args.apply)
        elif args.file:
            import_any(args.file, vault, args.apply, not args.no_transcribe, model_python,
                       args.model, args.whisper_root.expanduser(), args.manifest_db.expanduser())
        elif args.source:
            import_legacy(vault, args.source, args.stem, args.pilot_first,
                          args.whisper_root.expanduser(), args.audio_root,
                          args.manifest_db.expanduser(), args.apply)
        elif args.process_pending:
            if args.apply:
                process_pending(vault, model_python, args.model, args.limit,
                                args.whisper_root.expanduser(), args.manifest_db.expanduser())
            else:
                candidates = recording_notes(vault)
                pending = sum(parse_note(n.read_text("utf-8"))[0].get("transcription_status") == "pending"
                              for n in candidates)
                step(1, 1, f"DRY RUN: {pending} pending notes, no transcription launched")
        else:
            parser.print_help()
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        # A nonzero exit would normally be useful, but do not terminate the user's shell.
        raise


if __name__ == "__main__":
    main()
