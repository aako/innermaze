#!/usr/bin/env python3
# filename: innermaze_import_process.py
# version: 0.1.1
# timestamp: 2026-10-27 11:20:00
"""One-recording Mango importer: reconcile first, then optional Whisper and Qwen.

No batch workers, Cashew writes, or direct Obsidian player changes. Dry-run by
default. Designed to work with the existing Innermaze importer and Agent B/D.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import innermaze
import recording_identity as identity

HEX = re.compile(r"^[a-f0-9]{64}$")


def meter(n: int, total: int, label: str) -> None:
    k = 24 * n // total
    print(f"[{'#' * k}{'.' * (24 - k)}] {n}/{total} | {label}", flush=True)


def checked_note(vault: Path, rel: Path) -> Path:
    if rel.is_absolute() or rel.suffix.lower() != '.md':
        raise ValueError('Note must be a vault-relative .md path')
    vault = vault.resolve(strict=True)
    root = (vault / 'Voice Archive').resolve()
    path = (vault / rel).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Note must be a vault-relative path under Voice Archive')
    if path not in {p.resolve() for p in innermaze.recording_notes(vault)}:
        raise ValueError('Not a verified Voice Archive recording note')
    return path


def use_plan(plan: dict[str, Any], vault: Path) -> tuple[str, Path | None, str | None]:
    """A sole *proven* anonymous audio match can receive its verified UUID alias.

    The original B planner deliberately returns review_required for legacy notes;
    only resolve that case after uniquely matching compressed packets, checking
    every competing UUID note, and preserving the generic note's stable identity.
    """
    decision = plan['reconciliation']
    candidate = plan['candidate']
    action = decision['action']
    if action == 'create_note':
        return 'create', None, None
    if action == 'reuse_note':
        path = Path(decision['note_path']).resolve()
        if path not in {Path(n['path']).resolve() for n in identity.scan_vault(vault)}:
            raise ValueError('Planner returned a note not found in the verified vault scan')
        return 'reuse', path, None
    if action != 'review_required' or decision.get('reason') != 'possible_legacy_hash_note':
        raise ValueError('REVIEW_REQUIRED:' + decision.get('reason', 'unresolved_identity'))
    uid = candidate.get('apple_uuid')
    proposals = list(dict.fromkeys(decision.get('candidates') or []))
    if not uid or len(proposals) != 1:
        raise ValueError('REVIEW_REQUIRED:nonunique_legacy_match')
    notes = identity.scan_vault(vault)
    by_path = {str(Path(n['path']).resolve()): n for n in notes}
    match_path = str(Path(proposals[0]).resolve())
    note = by_path.get(match_path)
    if note is None or identity._bound_uuid(note['fields']) is not None:
        raise ValueError('REVIEW_REQUIRED:legacy_identity_changed')
    matched_audio = identity._resolve_vault_audio(vault, note['fields'])
    if matched_audio is None:
        raise ValueError('REVIEW_REQUIRED:legacy_audio_missing')
    observed = identity.inspect_recording(matched_audio)
    if not all(observed[k] == candidate[k] for k in ('packet_sha256', 'codec', 'sample_rate', 'channels')):
        raise ValueError('REVIEW_REQUIRED:legacy_packet_mismatch')
    for other in notes:
        if other['path'] == note['path']:
            continue
        bound = identity._bound_uuid(other['fields'])
        if bound == uid:
            raise ValueError('REVIEW_REQUIRED:uuid_already_bound')
        if bound:
            audio = identity._resolve_vault_audio(vault, other['fields'])
            if audio is None:
                raise ValueError('REVIEW_REQUIRED:other_uuid_audio_unavailable')
            other_audio = identity.inspect_recording(audio)
            if other_audio['packet_sha256'] == candidate['packet_sha256']:
                raise ValueError('REVIEW_REQUIRED:multiple_uuid_owners_same_audio')
    return 'reuse', Path(match_path), f'vm-{uid}'


def attach_uuid_alias(note: Path, alias: str) -> None:
    raw = note.read_text('utf-8')
    fields, _body = innermaze.parse_note(raw)
    existing = fields.get('archive_recording_id')
    if existing:
        if existing.casefold() != alias.casefold():
            raise ValueError('REVIEW_REQUIRED:conflicting_existing_archive_uuid')
        return
    if not re.fullmatch(r'vm-[a-fA-F0-9-]{36}', alias):
        raise ValueError('Invalid verified UUID alias')
    keys = list(re.finditer(r'(?m)^recording_id:', raw))
    if len(keys) != 1:
        raise ValueError('Expected one anchored recording_id frontmatter marker')
    new = (raw[:keys[0].start()] + f'archive_recording_id: {json.dumps(alias)}\n'
           + raw[keys[0].start():])
    if note.read_text('utf-8') != raw:
        raise ValueError('Recording note changed during UUID reconciliation')
    innermaze.atomic_replace_bytes(note, new.encode('utf-8'))


def transcribe_one(note: Path, vault: Path, *, model_python: Path, model: str,
                   whisper_root: Path, manifest_db: Path) -> str:
    """Targeted only; shares existing pending-worker serialization lock."""
    innermaze.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with (innermaze.TEMP_DIR / 'transcription.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Mango transcription already in progress; retry later') from None
        changed = innermaze.process_one(note, vault, model_python, model,
                                       whisper_root, manifest_db, allow_new_transcription=True)
    fields, _ = innermaze.parse_note(note.read_text('utf-8'))
    return 'ready' if fields.get('transcription_status') == 'ready' else ('unchanged' if not changed else 'ready')


def skip_one(note: Path) -> str:
    raw = note.read_text('utf-8')
    fields, body = innermaze.parse_note(raw)
    if fields.get('transcription_status') in ('skipped', 'ready'):
        return fields.get('transcription_status')
    if fields.get('transcription_status') != 'pending' or body.strip():
        raise ValueError('Cannot skip a note with human text or unknown transcription status')
    before = 'transcription_status: "pending"'
    if raw.count(before) != 1:
        raise ValueError('Expected one pending marker; preserving note')
    if note.read_text('utf-8') != raw:
        raise ValueError('Note changed while marking transcription skipped')
    innermaze.atomic_replace_bytes(note, raw.replace(before, 'transcription_status: "skipped"', 1).encode())
    return 'skipped'


def classify_one(note: Path, vault: Path, *, model: str, server: str) -> dict[str, Any]:
    script = Path(__file__).with_name('innermaze_classify.py')
    if not script.is_file():
        return {'status': 'failed', 'error': 'Qwen classifier source is unavailable'}
    cmd = [sys.executable, str(script), '--note', str(note),
           '--output-root', str(vault / 'Voice Archive/Classification'),
           '--model', model, '--server', server, '--apply']
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'status': 'failed', 'error': str(error)[:500]}
    if result.returncode:
        return {'status': 'failed', 'error': result.stderr.strip()[-750:]}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {'status': 'failed', 'error': 'Qwen returned a non-JSON execution receipt'}
    return {'status': parsed.get('status', 'unknown'), 'run_id': parsed.get('run_id'),
            'needs_human_review': parsed.get('needs_human_review'),
            'suggestions': parsed.get('suggestions')}


def run_file(source: Path, vault: Path, *, transcribe: str, classify: str, apply: bool,
             model_python: Path, whisper_model: str, whisper_root: Path,
             manifest_db: Path, qwen_model: str, server: str) -> dict[str, Any]:
    meter(1, 5, 'Inspect and reconcile source, archive UUID and compressed audio')
    vault = vault.expanduser().resolve(strict=True)
    if not (vault / '.obsidian').is_dir():
        raise ValueError('Not an Obsidian vault')
    plan = identity.plan_archive_file(source, vault)
    action, existing_note, uuid_alias = use_plan(plan, vault)
    candidate = plan['candidate']
    if not HEX.fullmatch(candidate['file_sha256']):
        raise ValueError('Invalid source file hash')
    proposed = candidate['proposed_recording_id']
    meter(2, 5, 'Validate immutable recording identity and import plan')
    report: dict[str, Any] = {
        'status': 'planned' if not apply else 'processing',
        'action': action, 'recording_id': proposed if not existing_note else
        innermaze.parse_note(existing_note.read_text('utf-8'))[0]['recording_id'],
        'note': existing_note.relative_to(vault).as_posix() if existing_note else None,
        'transcription': transcribe, 'classification': classify,
        'archive_alias': uuid_alias,
    }
    if not apply:
        meter(5, 5, 'DRY RUN: no vault, archive or model changes')
        return report
    innermaze.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with (innermaze.TEMP_DIR / 'integration-import.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another Innermaze integrated import is running') from None
        # Re-evaluate after lock acquisition; another import could have completed.
        plan = identity.plan_archive_file(source, vault)
        action, existing_note, uuid_alias = use_plan(plan, vault)
        meter(3, 5, 'Reuse existing note or create one verified listening copy')
        if existing_note is not None:
            note = existing_note
            if uuid_alias:
                attach_uuid_alias(note, uuid_alias)
        else:
            kind = next((name for name, folder in innermaze.SOURCES.items()
                         if source.expanduser().absolute().is_relative_to(folder.expanduser().absolute())),
                        'arbitrary-file')
            note = innermaze.import_any(source, vault, True, False,
                                        model_python, whisper_model, whisper_root,
                                        manifest_db, identity_hint=plan['candidate']['proposed_recording_id']
                                        if plan['candidate'].get('apple_uuid') else None,
                                        reuse_cached=False, source_kind=kind)
            if note is None:
                raise ValueError('Importer did not create or select a recording note')
        report['action'] = action
        report['recording_id'] = innermaze.parse_note(note.read_text('utf-8'))[0]['recording_id']
        report['note'] = note.relative_to(vault).as_posix()
        report['archive_alias'] = uuid_alias
        meter(4, 5, 'Process only this recording, respecting transcription choice')
        if transcribe == 'now':
            report['transcription'] = transcribe_one(note, vault, model_python=model_python,
                model=whisper_model, whisper_root=whisper_root, manifest_db=manifest_db)
        elif transcribe == 'skip':
            report['transcription'] = skip_one(note)
        else:
            report['transcription'] = 'deferred_not_scheduled'
        meter(5, 5, 'Classify only on request; preserve prior results and human edits')
        if classify == 'after' and report['transcription'] == 'ready':
            report['classification'] = classify_one(note, vault, model=qwen_model, server=server)
        elif classify == 'after':
            report['classification'] = 'deferred_not_scheduled_no_ready_transcript'
        elif classify == 'later':
            report['classification'] = 'deferred_not_scheduled'
        else:
            report['classification'] = 'skipped'
        report['status'] = 'imported' if action == 'create' else 'reused'
        return report


def run_note(rel: Path, vault: Path, *, apply: bool, model_python: Path, whisper_model: str,
             whisper_root: Path, manifest_db: Path, classify: str,
             qwen_model: str, server: str) -> dict[str, Any]:
    meter(1, 3, 'Verify selected vault-relative note')
    vault = vault.expanduser().resolve(strict=True)
    note = checked_note(vault, rel)
    if not apply:
        meter(3, 3, 'DRY RUN: no model or vault writes')
        return {'status': 'planned', 'note': note.relative_to(vault).as_posix()}
    meter(2, 3, 'Run targeted Mango Whisper, not the global pending queue')
    status = transcribe_one(note, vault, model_python=model_python, model=whisper_model,
                            whisper_root=whisper_root, manifest_db=manifest_db)
    result = {'status': 'processed', 'note': note.relative_to(vault).as_posix(),
              'transcription': status, 'classification': 'skipped'}
    if classify == 'after' and status == 'ready':
        result['classification'] = classify_one(note, vault, model=qwen_model, server=server)
    elif classify == 'later':
        result['classification'] = 'deferred_not_scheduled'
    meter(3, 3, 'Selected recording handled without draining other notes')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--file', type=Path, help='Single Dropbox or other audio file on Mango')
    choice.add_argument('--process-note', type=Path, help='Single vault-relative existing recording note')
    parser.add_argument('--vault', type=Path, required=True, help='Existing local iCloud Obsidian vault')
    parser.add_argument('--transcribe', choices=('now', 'later', 'skip'), default='now')
    parser.add_argument('--classify', choices=('after', 'later', 'skip'), default='skip')
    parser.add_argument('--mlx-python', type=Path, default=innermaze.MLX_PYTHON)
    parser.add_argument('--whisper-model', default=innermaze.MODEL)
    parser.add_argument('--whisper-root', type=Path, default=innermaze.WHISPER)
    parser.add_argument('--manifest-db', type=Path, default=innermaze.BACKUP_DB)
    parser.add_argument('--qwen-model', default='voice-memo-qwen')
    parser.add_argument('--server', default='http://127.0.0.1:1234/v1')
    parser.add_argument('--apply', action='store_true', help='Explicitly permit one selected vault import/process')
    args = parser.parse_args()
    try:
        vault = args.vault.expanduser().resolve(strict=True)
        if not (vault / '.obsidian').is_dir():
            raise ValueError('Selected vault has no .obsidian directory')
        options = dict(apply=args.apply, model_python=args.mlx_python.expanduser(),
            whisper_model=args.whisper_model, whisper_root=args.whisper_root.expanduser(),
            manifest_db=args.manifest_db.expanduser(), classify=args.classify,
            qwen_model=args.qwen_model, server=args.server)
        if args.file:
            result = run_file(args.file.expanduser().absolute(), vault,
                              transcribe=args.transcribe, **options)
        else:
            if args.transcribe == 'skip':
                raise ValueError('Use --file to skip transcription on import')
            result = run_note(args.process_note, vault, **options)
        print('INNERMAZE_RESULT_JSON=' + json.dumps(result, ensure_ascii=False, separators=(',', ':')), flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'INNERMAZE_ERROR: {error}', file=sys.stderr, flush=True)
        raise


if __name__ == '__main__':
    main()
