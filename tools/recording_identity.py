#!/usr/bin/env python3
# filename: recording_identity.py
# version: 0.1.0
# timestamp: 2026-10-27 10:25:00
"""Read-only Apple Voice Memos identity inspection and Innermaze reconciliation.

Agent B. This module never writes archive, vault, or configuration files.
It is intentionally independent of the Obsidian importer and transcription worker.
Requires ffprobe and ffmpeg available on the executing Mango host.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable
import uuid

_UUID_TAG = 'voice-memo-uuid'
_DIGEST = re.compile(r'^[0-9a-f]{64}$')
_RECORDING = re.compile(r'^vm-([0-9a-fA-F-]{36})$', re.IGNORECASE)
_NOTE_HEADER = re.compile(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', re.DOTALL)
_FIELD = re.compile(r'^([a-z][a-z0-9_]*):\s*(.*)$')
_AUDIO_SUFFIXES = {'.m4a', '.mp4', '.m4b', '.aac', '.mp3', '.wav', '.caf', '.aif', '.aiff', '.flac', '.ogg'}


def normalize_uuid(value: str) -> str:
    """Canonical uppercase hyphenated UUID, requiring the 36-character form."""
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value.strip()):
        raise ValueError('Invalid Apple voice-memo-uuid; refusing to guess')
    return str(uuid.UUID(value.strip())).upper()


def _execute(argv: list[str], timeout: int = 900) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f'Cannot inspect recording using {argv[0]}: {exc}') from exc
    if result.returncode:
        raise ValueError(f'{argv[0]} failed: {result.stderr.strip()[:350]}')
    return result.stdout


def _stat_key(path: Path) -> tuple[int, int, int, int]:
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _probe(path: Path, ffprobe: str) -> dict[str, Any]:
    output = _execute([ffprobe, '-v', 'error', '-show_entries',
                       'format=duration,format_name:format_tags:stream=index,codec_type,codec_name,sample_rate,channels:stream_tags',
                       '-of', 'json', str(path)])
    try:
        obj = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError('ffprobe returned invalid JSON') from exc
    if not isinstance(obj, dict) or not isinstance(obj.get('format'), dict):
        raise ValueError('Not a readable media container')
    return obj


def inspect_recording(path: Path | str, *, ffprobe: str = 'ffprobe', ffmpeg: str = 'ffmpeg') -> dict[str, Any]:
    """Return immutable-file, tag, and compressed-packet identities for one audio file.

    ffmpeg streamhash hashes the demuxed compressed packets, not MP4 container atoms.
    This is intentionally *not* a PCM/perceptual fingerprint: edits or re-encoding
    are represented as audio versions pending human review, not silent merges.
    """
    source = Path(path).expanduser().absolute()
    if not source.is_file() or source.is_symlink():
        raise ValueError(f'Not a regular, non-symlink recording: {source}')
    before = _stat_key(source)
    if before[2] == 0:
        raise ValueError('Empty or incomplete audio file')
    probe = _probe(source, ffprobe)
    streams = [s for s in probe.get('streams', []) if s.get('codec_type') == 'audio']
    if len(streams) != 1:
        raise ValueError('Expected exactly one audio stream; manual review required')
    tags = []
    for container in [probe['format'], *probe.get('streams', [])]:
        for key, val in (container.get('tags') or {}).items():
            if key.casefold() == _UUID_TAG:
                tags.append(normalize_uuid(val))
    if len(set(tags)) > 1:
        raise ValueError('Conflicting embedded Apple UUID tags')
    recording_uuid = tags[0] if tags else None
    stream = streams[0]
    stream_index = stream.get('index')
    if not isinstance(stream_index, int):
        raise ValueError('Missing audio stream index')
    packet_out = _execute([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error',
                           '-i', str(source), '-map', f'0:{stream_index}',
                           '-c:a', 'copy', '-f', 'streamhash', '-hash', 'sha256', '-'])
    matches = re.findall(r'\bSHA256=([0-9a-fA-F]{64})\b', packet_out)
    if len(matches) != 1:
        raise ValueError('Unusable compressed-audio streamhash')
    digest = _file_digest(source)
    if _stat_key(source) != before:
        raise ValueError('Source audio changed during identity inspection; retry after download completes')
    format_tags = probe['format'].get('tags') or {}
    def tag(label: str) -> str | None:
        values = [str(v) for k, v in format_tags.items() if k.casefold() == label]
        return values[0] if len(values) == 1 else None
    return {
        'path': str(source), 'size': before[2], 'file_sha256': digest,
        'apple_uuid': recording_uuid,
        'proposed_recording_id': f'vm-{recording_uuid}' if recording_uuid else f'audio-{digest}',
        'packet_sha256': matches[0].lower(),
        'codec': str(stream.get('codec_name') or ''),
        'sample_rate': str(stream.get('sample_rate') or ''),
        'channels': stream.get('channels'),
        'duration': float(probe['format'].get('duration') or 0),
        'creation_time': tag('creation_time'),
        'title': tag('title'),
        'encoder': tag('encoder'),
    }


def _scalar(value: str) -> str:
    value = value.strip()
    if value.startswith('"'):
        try:
            v = json.loads(value)
            return str(v)
        except json.JSONDecodeError:
            raise ValueError('Invalid quoted YAML frontmatter value')
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def _note_fields(raw: str) -> dict[str, str] | None:
    match = _NOTE_HEADER.match(raw)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        found = _FIELD.match(line)
        if found:
            if found[1] in fields:
                raise ValueError(f'Duplicate frontmatter key: {found[1]}')
            fields[found[1]] = _scalar(found[2])
    return fields if fields.get('voice_archive', '').casefold() == 'true' and fields.get('recording_id') else None


def scan_vault(vault: Path | str) -> list[dict[str, Any]]:
    """Inspect notes by frontmatter, independent of file names or renames.

    Does not recurse into arbitrary folders and never changes note contents.
    """
    vault = Path(vault).expanduser().absolute()
    result: list[dict[str, Any]] = []
    for subdir in ('Voice Archive', 'Voice Archive/Notes'):
        directory = vault / subdir
        if not directory.is_dir():
            continue
        for note in sorted(directory.glob('*.md')):
            if note.is_symlink():
                raise ValueError(f'Symlinked recording note requires review: {note}')
            fields = _note_fields(note.read_text(encoding='utf-8'))
            if fields:
                result.append({'path': str(note), 'fields': fields})
    return result


def _bound_uuid(fields: dict[str, str]) -> str | None:
    candidates = []
    for field in ('recording_id', 'archive_recording_id'):
        val = fields.get(field, '')
        match = _RECORDING.fullmatch(val)
        if match:
            candidates.append(normalize_uuid(match[1]))
        elif val and field == 'archive_recording_id':
            raise ValueError(f'Unrecognized archive_recording_id: {val}')
    if len(set(candidates)) > 1:
        raise ValueError('Recording note has contradictory Apple UUID identities')
    return candidates[0] if candidates else None


def _resolve_vault_audio(vault: Path, fields: dict[str, str]) -> Path | None:
    rel = fields.get('voice_audio')
    if not rel:
        return None
    audio = (vault / rel).resolve()
    if not audio.is_relative_to(vault.resolve()):
        raise ValueError('Recording note references audio outside Obsidian vault')
    return audio if audio.is_file() else None


def _result(action: str, reason: str, candidate: dict[str, Any], note: str | None = None,
            **extra: Any) -> dict[str, Any]:
    return {'action': action, 'reason': reason, 'recording_id': candidate['proposed_recording_id'],
            'note_path': note, **extra}


def reconcile_recording(candidate: dict[str, Any], vault: Path | str,
                        *, notes: Iterable[dict[str, Any]] | None = None,
                        ffprobe: str = 'ffprobe', ffmpeg: str = 'ffmpeg') -> dict[str, Any]:
    """Plan ONLY. Actions: create_note, reuse_note, review_required.

    UUID match is strongest; identical packet hashes validate unchanged audio despite
    container metadata differences. Do not silently bind UUID to legacy hash note.
    """
    vault = Path(vault).expanduser().absolute()
    existing = list(notes if notes is not None else scan_vault(vault))
    uid = candidate.get('apple_uuid')
    file_sha = candidate['file_sha256']
    packet_sha = candidate['packet_sha256']
    bound: list[dict[str, Any]] = []
    packet_matches: list[dict[str, Any]] = []
    sha_matches: list[dict[str, Any]] = []
    conflicting_ids: dict[str, list[str]] = {}
    unverified_legacy: list[str] = []
    for note in existing:
        fields = note['fields']
        rec_id = fields.get('recording_id', '')
        if rec_id:
            conflicting_ids.setdefault(rec_id.casefold(), []).append(note['path'])
        note_uuid = _bound_uuid(fields)
        if uid and note_uuid == uid:
            bound.append(note)
        if file_sha in (fields.get('source_sha256'), fields.get('playback_sha256')):
            sha_matches.append(note)
        # Distinct verified Apple UUIDs must not be treated as the same recording.
        # Legacy hash-only notes still require packet matching or explicit review.
        if uid and note_uuid and note_uuid != uid:
            continue
        audio = _resolve_vault_audio(vault, fields)
        if not audio and uid and not note_uuid:
            unverified_legacy.append(note['path'])
        if audio:
            expected = fields.get('playback_sha256')
            if expected and _DIGEST.fullmatch(expected.lower()) and _file_digest(audio) != expected.lower():
                raise ValueError(f'Vault audio hash contradicts note: {note["path"]}')
            packet = inspect_recording(audio, ffprobe=ffprobe, ffmpeg=ffmpeg)
            if (packet['packet_sha256'] == packet_sha and
                packet['codec'] == candidate['codec'] and
                packet['sample_rate'] == candidate['sample_rate'] and
                packet['channels'] == candidate['channels']):
                packet_matches.append(note)
    if any(len(paths) > 1 for paths in conflicting_ids.values()):
        return _result('review_required', 'duplicate_recording_id', candidate,
                       conflicting_ids={k: v for k, v in conflicting_ids.items() if len(v) > 1})
    if len(bound) > 1:
        return _result('review_required', 'duplicate_apple_uuid_binding', candidate,
                       candidates=[note['path'] for note in bound])
    if len(bound) == 1:
        note = bound[0]
        if note in packet_matches or note in sha_matches:
            return _result('reuse_note', 'uuid_and_audio_match', candidate, note['path'],
                           existing_recording_id=note['fields']['recording_id'])
        return _result('review_required', 'same_uuid_different_audio_version', candidate, note['path'],
                       existing_recording_id=note['fields']['recording_id'])
    if uid:
        legacy = [n for n in packet_matches + sha_matches if _bound_uuid(n['fields']) is None]
        if legacy:
            return _result('review_required', 'possible_legacy_hash_note', candidate,
                           candidates=sorted({n['path'] for n in legacy}))
        if unverified_legacy:
            return _result('review_required', 'legacy_note_audio_unavailable', candidate,
                           candidates=sorted(unverified_legacy))
        # Different Apple UUIDs may legitimately hold identical audio.
        return _result('create_note', 'new_apple_uuid', candidate)
    if len(sha_matches) == 1 and sha_matches[0]['fields'].get('recording_id') == f'audio-{file_sha}':
        match = sha_matches[0]
        return _result('reuse_note', 'exact_anonymous_file', candidate, match['path'],
                       existing_recording_id=match['fields']['recording_id'])
    if sha_matches or packet_matches:
        return _result('review_required', 'missing_uuid_possible_existing_recording', candidate,
                       candidates=sorted({n['path'] for n in sha_matches + packet_matches}))
    return _result('create_note', 'new_anonymous_file', candidate)


def plan_archive_file(path: Path | str, vault: Path | str, **kwargs: Any) -> dict[str, Any]:
    """Entry point for Agent C or future Mango watcher: no source/vault writes."""
    candidate = inspect_recording(path, **{k: v for k, v in kwargs.items() if k in ('ffprobe', 'ffmpeg')})
    plan = reconcile_recording(candidate, vault, **kwargs)
    return {'candidate': candidate, 'reconciliation': plan}


def iter_archive_paths(archive: Path | str):
    """Sorted candidate discovery, strictly read-only; no Cashew dependency."""
    root = Path(archive).expanduser().absolute()
    if not root.is_dir():
        raise ValueError(f'Archive directory not found: {root}')
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink() and path.suffix.casefold() in _AUDIO_SUFFIXES:
            yield path


def main() -> None:
    """Read-only diagnostic command for one Dropbox recording or bounded scan."""
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Dry-run Innermaze Apple Voice Memo reconciliation')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--file', type=Path, help='One Dropbox or iPhone recording')
    source.add_argument('--archive', type=Path, help='Discover recordings in read-only archive')
    parser.add_argument('--vault', type=Path, default=Path.home() /
                        'Library/Mobile Documents/iCloud~md~obsidian/Documents/Cabinet')
    parser.add_argument('--limit', type=int, default=1, help='Maximum archive candidates (default 1)')
    args = parser.parse_args()
    if args.limit < 1:
        raise ValueError('--limit must be positive')
    paths = [args.file] if args.file else iter_archive_paths(args.archive)
    results = []
    for i, path in enumerate(paths):
        if i >= args.limit:
            break
        print(f'[#{"." * 23}] {i + 1}/{args.limit} | Inspect and reconcile {path.name}', file=sys.stderr, flush=True)
        results.append(plan_archive_file(path, args.vault))
    print(f'[{"#" * 24}] {len(results)}/{len(results)} | Read-only planning complete', file=sys.stderr, flush=True)
    print(json.dumps(results[0] if args.file else results, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
