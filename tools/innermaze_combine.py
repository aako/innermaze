#!/usr/bin/env python3
# filename: innermaze_combine.py
# version: 0.1.0
# timestamp: 2026-11-06 15:37:00
"""Non-destructive ordered Innermaze memo combination on Mango.

Only previously imported Cabinet attachments are accepted as sources. Source
notes, source audio, Dropbox archives and the Cashew manifest are never written.
Default mode is a dry-run; --apply creates one AAC playback file, one immutable
provenance sidecar and one editable, pending-transcription recording note.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

ARCHIVE_NAMES = ('Voice Memos Full DB', 'Just Press Record Full DB')
SHA = re.compile(r'^[0-9a-f]{64}$')
ID = re.compile(r'^[A-Za-z0-9_-]{1,180}$')
FRONT = re.compile(r'\A---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|\Z)')
SCALAR = re.compile(r'^([a-z][a-z0-9_]*):\s*(.*?)\s*$')
MILLI = Decimal('0.001')


def meter(number: int, total: int, message: str) -> None:
    done = 24 * number // total
    print(f'[{"#" * done}{"." * (24 - done)}] {number}/{total} | {message}', flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def under(candidate: Path, directory: Path) -> bool:
    return candidate == directory or candidate.is_relative_to(directory)


def archive_roots() -> list[Path]:
    base = Path.home() / 'Dropbox/1 NOTES'
    return [(base / name).resolve() for name in ARCHIVE_NAMES]


def safe_output(candidate: Path, vault: Path, archives: list[Path]) -> None:
    canonical = candidate.resolve()
    if not under(canonical, vault):
        raise ValueError('Output resolves outside Cabinet vault')
    if any(under(canonical, archive) for archive in archives):
        raise ValueError('Output resolves inside permanent Dropbox archive')


def note_fields(text: str) -> dict[str, str]:
    front = FRONT.match(text)
    if not front:
        raise ValueError('Recording note has no complete YAML frontmatter')
    data: dict[str, str] = {}
    for line in front.group(1).splitlines():
        found = SCALAR.match(line)
        if not found:
            continue
        name, raw = found.groups()
        if name in data:
            raise ValueError('Duplicate recording property: ' + name)
        if raw.startswith('"'):
            value = json.loads(raw)
        elif raw.startswith("'") and raw.endswith("'"):
            value = raw[1:-1].replace("''", "'")
        else:
            value = raw
        if not isinstance(value, str):
            raise ValueError('Invalid recording property: ' + name)
        data[name] = value
    required = ('recording_id', 'voice_audio', 'voice_sync', 'playback_sha256')
    if data.get('voice_archive', '').lower() != 'true' or any(not data.get(k) for k in required):
        raise ValueError('Not a complete Innermaze recording note')
    return data


def check_title(title: str) -> str:
    title = title.strip()
    if not title or len(title) > 160 or title in ('.', '..') or re.search(r'[\\/:*?"<>|\x00-\x1f]', title) or title.endswith(('.', ' ')):
        raise ValueError('Title must be 1–160 safe filename characters')
    return title


def trim(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value)).quantize(MILLI, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'Invalid {label}') from None
    if not parsed.is_finite() or parsed < 0 or parsed > 30:
        raise ValueError(f'{label} must be between 0 and 30 seconds')
    return parsed


def duration_seconds(path: Path, ffprobe: str) -> Decimal:
    command = [ffprobe, '-v', 'error', '-select_streams', 'a:0',
               '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        raise ValueError(f'Cannot inspect source audio: {path.name}: {result.stderr[-400:]}')
    try:
        value = Decimal(result.stdout.strip()).quantize(MILLI, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f'Cannot determine duration: {path.name}') from None
    if not value.is_finite() or value <= 0:
        raise ValueError('Invalid audio duration: ' + path.name)
    return value


def validate_source(vault: Path, entry: dict[str, Any], archives: list[Path], ffprobe: str) -> dict[str, Any]:
    if not isinstance(entry, dict) or not isinstance(entry.get('note'), str):
        raise ValueError('Each item needs a vault-relative note path')
    rel = Path(entry['note'])
    if rel.is_absolute() or '..' in rel.parts or rel.suffix.lower() != '.md':
        raise ValueError('Source note must be vault-relative Markdown')
    note = (vault / rel).resolve(strict=True)
    if not under(note, vault) or note.parent not in (vault / 'Voice Archive', vault / 'Voice Archive/Notes'):
        raise ValueError('Only existing Voice Archive recording notes can be combined')
    if not note.is_file():
        raise ValueError('Source note missing')
    before = note.read_bytes()
    fields = note_fields(before.decode('utf-8'))
    recording_id = fields['recording_id']
    if not ID.fullmatch(recording_id):
        raise ValueError('Unsafe recording identity')
    if fields.get('recording_source') == 'combined-recording':
        raise ValueError('Nested combined recordings need explicit review; use original recording notes')
    audio_rel = Path(fields['voice_audio'])
    if audio_rel.is_absolute() or '..' in audio_rel.parts:
        raise ValueError('Unsafe source playback path')
    audio = (vault / audio_rel).resolve(strict=True)
    if not under(audio, vault) or not audio.is_file():
        raise ValueError('Playback file unavailable in Cabinet: ' + recording_id)
    expected = fields['playback_sha256'].lower()
    if not SHA.fullmatch(expected):
        raise ValueError('Invalid source playback hash')
    actual = sha256(audio)
    if expected != actual:
        raise ValueError('Existing source playback hash mismatch: ' + recording_id)
    head = trim(entry.get('trim_start', 0), 'trim_start')
    tail = trim(entry.get('trim_end', 0), 'trim_end')
    duration = duration_seconds(audio, ffprobe)
    usable = duration - head - tail
    if usable < Decimal('0.100'):
        raise ValueError('Trim would remove entire recording: ' + recording_id)
    if note.read_bytes() != before:
        raise ValueError('Source recording note changed during preflight')
    uuid_identity = fields.get('archive_recording_id') or (recording_id if recording_id.lower().startswith('vm-') else '')
    return dict(note=note, note_rel=rel.as_posix(), note_sha256=hashlib.sha256(before).hexdigest(),
                audio=audio, audio_rel=audio_rel.as_posix(), recording_id=recording_id,
                apple_uuid_identity=uuid_identity,
                original_source_sha256=fields.get('source_sha256', ''), playback_sha256=actual,
                duration=duration, trim_start=head, trim_end=tail, usable=usable)


def publish_new(path: Path, contents: Path | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ValueError('Output unexpectedly exists: ' + str(path))
    with path.open('xb') as target:
        try:
            if isinstance(contents, Path):
                with contents.open('rb') as source:
                    shutil.copyfileobj(source, target, 1024 * 1024)
            else:
                target.write(contents)
            target.flush()
            os.fsync(target.fileno())
        except BaseException:
            target.close()
            path.unlink(missing_ok=True)
            raise


def run(vault: Path, items: list[dict[str, Any]], title: str, *, apply: bool,
        ffmpeg: str | None = None, ffprobe: str | None = None,
        scratch: Path | None = None) -> dict[str, Any]:
    meter(1, 6, 'Validate vault, read-only source notes and independent archives')
    vault = vault.expanduser().resolve(strict=True)
    if not (vault / '.obsidian').is_dir():
        raise ValueError('Not an Obsidian vault')
    archives = archive_roots()
    if any(under(vault, archive) or under(archive, vault) for archive in archives):
        raise ValueError('Vault overlaps an original Dropbox archive')
    if not isinstance(items, list) or not (2 <= len(items) <= 10):
        raise ValueError('Combine requires 2–10 existing recordings')
    title = check_title(title)
    ffmpeg = ffmpeg or shutil.which('ffmpeg')
    ffprobe = ffprobe or shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('ffmpeg and ffprobe required on Mango; check /opt/homebrew/bin')
    scratch = (scratch or Path.home() / 'Work/Temp/Innermaze').expanduser().resolve()
    if any(under(scratch, archive) or under(archive, scratch) for archive in archives):
        raise ValueError('Scratch overlaps original archive')
    meter(2, 6, 'Check each playback SHA-256 and calculate ordered trims')
    sources = [validate_source(vault, item, archives, ffprobe) for item in items]
    ids = [item['recording_id'] for item in sources]
    if len(set(ids)) != len(ids):
        raise ValueError('A source recording cannot appear twice in one combination')
    recipe = [{'recording_id': item['recording_id'], 'playback_sha256': item['playback_sha256'],
               'trim_start_ms': int(item['trim_start'] * 1000),
               'trim_end_ms': int(item['trim_end'] * 1000)} for item in sources]
    recipe_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    recording_id = 'combined-' + recipe_hash
    asset = vault / 'Voice Archive/Audio' / (recording_id + '.m4a')
    note = vault / 'Voice Archive' / (title + '.md')
    provenance = vault / 'Voice Archive/Provenance' / (recording_id + '.json')
    for path in (asset, note, provenance):
        safe_output(path, vault, archives)
    meter(3, 6, 'Resolve deterministic recording identity and existing outputs')
    # Human renames never change immutable recording identity. Reuse by frontmatter,
    # not merely by the default creation path. Do not create duplicate notes.
    existing_notes: list[Path] = []
    for directory in (vault / 'Voice Archive', vault / 'Voice Archive/Notes'):
        if not directory.is_dir():
            continue
        for candidate_note in directory.glob('*.md'):
            if candidate_note.is_symlink():
                raise ValueError('Symlinked recording note requires manual review')
            try:
                candidate_fields = note_fields(candidate_note.read_text('utf-8'))
            except ValueError:
                continue
            if candidate_fields.get('recording_id') == recording_id:
                existing_notes.append(candidate_note)
    if len(existing_notes) > 1:
        raise ValueError('Multiple notes share combined recording ID; refusing duplicate')
    if existing_notes:
        note = existing_notes[0]
    if note.exists():
        fields = note_fields(note.read_text('utf-8'))
        if fields.get('recording_id') != recording_id or fields.get('combination_recipe_sha256') != recipe_hash:
            raise ValueError('Existing combined note conflicts with requested recipe')
        if not asset.is_file() or not provenance.is_file() or sha256(asset) != fields.get('playback_sha256'):
            raise ValueError('Existing combined playback/provenance unavailable; no auto-repair')
        details = json.loads(provenance.read_text('utf-8'))
        if details.get('recipe_sha256') != recipe_hash:
            raise ValueError('Existing combination provenance contradicts recipe')
        meter(6, 6, 'REUSE: combined recording exists and source notes untouched')
        return dict(status='reused', note=note.relative_to(vault).as_posix(),
                    audio=asset.relative_to(vault).as_posix(), recording_id=recording_id,
                    source_count=len(sources), recipe_sha256=recipe_hash)
    if any(path.exists() or path.is_symlink() for path in (note, asset, provenance)):
        raise ValueError('Partial/orphan combined output exists; manual review required')
    result = dict(status='planned', recording_id=recording_id,
                  note=note.relative_to(vault).as_posix(), audio=asset.relative_to(vault).as_posix(),
                  provenance=provenance.relative_to(vault).as_posix(), source_count=len(sources),
                  recipe_sha256=recipe_hash)
    if not apply:
        meter(6, 6, 'DRY RUN: original files, source notes and vault unchanged')
        return result
    meter(4, 6, 'Render new joined AAC in independent local scratch')
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='innermaze-combine-', dir=scratch) as temp_dir:
        rendered = Path(temp_dir) / 'combined.m4a'
        arguments = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y']
        for item in sources:
            arguments.extend(['-i', str(item['audio'])])
        filters = []
        for index, item in enumerate(sources):
            start = item['trim_start']
            end = item['duration'] - item['trim_end']
            filters.append(f'[{index}:a:0]asetpts=PTS-STARTPTS,atrim=start={start}:end={end},asetpts=PTS-STARTPTS,'
                           'aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo'
                           f'[part{index}]')
        filters.append(''.join(f'[part{i}]' for i in range(len(sources))) +
                       f'concat=n={len(sources)}:v=0:a=1[out]')
        arguments.extend(['-filter_complex', ';'.join(filters), '-map', '[out]', '-vn',
                          '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(rendered)])
        completed = subprocess.run(arguments, capture_output=True, text=True, timeout=3600, check=False)
        if completed.returncode or not rendered.is_file() or rendered.stat().st_size == 0:
            raise ValueError('FFmpeg combine failed: ' + completed.stderr[-1000:])
        output_duration = duration_seconds(rendered, ffprobe)
        expected_length = sum((item['usable'] for item in sources), Decimal('0'))
        if abs(output_duration - expected_length) > Decimal('0.250'):
            raise ValueError('Combined output duration differs unexpectedly from selected segments')
        playback_hash = sha256(rendered)
        meter(5, 6, 'Verify unchanged source hashes; publish create-only vault assets')
        for item in sources:
            if sha256(item['audio']) != item['playback_sha256'] or sha256(item['note']) != item['note_sha256']:
                raise ValueError('A source changed during FFmpeg render; refusing publication')
        at = Decimal('0')
        provenance_parts = []
        for index, item in enumerate(sources):
            provenance_parts.append(dict(index=index, recording_id=item['recording_id'],
                apple_uuid_identity=item['apple_uuid_identity'],
                note_path=item['note_rel'], source_sha256=item['original_source_sha256'],
                playback_sha256=item['playback_sha256'], audio_path=item['audio_rel'],
                original_duration_seconds=float(item['duration']),
                trim_start_seconds=float(item['trim_start']), trim_end_seconds=float(item['trim_end']),
                output_start_seconds=float(at), output_end_seconds=float(at + item['usable'])))
            at += item['usable']
        provenance_data = dict(schema='innermaze-combine-v1', recipe_sha256=recipe_hash,
                               recording_id=recording_id, playback_sha256=playback_hash,
                               playback_duration_seconds=float(output_duration),
                               estimated_join_boundaries_seconds=[float(s['output_end_seconds']) for s in provenance_parts[:-1]],
                               sources=provenance_parts)
        pretty = json.dumps(provenance_data, ensure_ascii=False, indent=2) + '\n'
        escaped = json.dumps
        metadata = [
            '---', 'voice_archive: true', f'recording_id: {escaped(recording_id)}',
            'recording_source: "combined-recording"', f'source_title: {escaped(title)}',
            f'display_title: {escaped(title)}',
            f'voice_audio: {escaped(asset.relative_to(vault).as_posix())}',
            f'voice_sync: {escaped((Path("Voice Archive/Sync") / (recording_id + ".sync.json")).as_posix())}',
            f'source_sha256: {escaped(playback_hash)}', f'playback_sha256: {escaped(playback_hash)}',
            f'combination_recipe_sha256: {escaped(recipe_hash)}',
            f'combination_provenance: {escaped(provenance.relative_to(vault).as_posix())}',
            'combination_source_ids: ' + json.dumps([item['recording_id'] for item in sources]),
            'combination_source_playback_hashes: ' + json.dumps([item['playback_sha256'] for item in sources]),
            'combination_join_boundaries_seconds: ' + json.dumps(provenance_data['estimated_join_boundaries_seconds']),
            'transcription_status: "pending"', 'tags:', '  - voice-archive', '  - combined-recording', '---', '',
            'Transcription pending for the complete combined recording.', ''
        ]
        # Pending recordings must have an EMPTY body: the legacy Whisper worker
        # refuses to overwrite human-authored text. Keep explanation in properties.
        metadata = metadata[:-2]
        publish_new(asset, rendered)
        if sha256(asset) != playback_hash:
            raise ValueError('Published combined playback hash mismatch')
        publish_new(provenance, pretty.encode('utf-8'))
        publish_new(note, ('\n'.join(metadata) + '\n').encode('utf-8'))
    meter(6, 6, 'Combined note published; originals and prior transcripts untouched')
    return dict(result, status='combined', playback_sha256=playback_hash,
                duration_seconds=float(output_duration))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    parser.add_argument('--items-json', required=True,
                        help='JSON array of 2–10 {note,trim_start,trim_end} entries in desired order')
    parser.add_argument('--title', required=True)
    parser.add_argument('--apply', action='store_true', help='Create combined vault files; default is dry-run')
    args = parser.parse_args()
    if len(args.items_json) > 30000:
        raise ValueError('Overlong combine recipe')
    entries = json.loads(args.items_json)
    receipt = run(args.vault, entries, args.title, apply=args.apply)
    print('INNERMAZE_COMBINE_JSON=' + json.dumps(receipt, ensure_ascii=False, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
