#!/usr/bin/env python3
# filename: prepare_mobile_candidate.py
# version: 0.2.0
# timestamp: 2026-10-27 14:38:00
"""Make a standalone BRAT-ready main.js candidate *without changing the source*.

Agent E integration-only helper. Dry-run by default. It refuses in-place writes,
existing outputs, and unexpected changes to the exact mobile-import baseline.
Input must be the current Agent E candidate main.js, not a BRAT install path.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
START = 'function makeGenericNote('
END = '// Strictly configured Mango candidate helper:'


def progress(step: int, total: int, message: str) -> None:
    length = 24
    filled = round(length * step / total)
    print(f"[{'#' * filled}{'.' * (length - filled)}] {step}/{total} | {message}", flush=True)


def prepare(original: str, identity: str, methods: str) -> str:
    if original.count(START) != 1 or original.count(END) != 1:
        raise ValueError('Unexpected Agent E main.js structure; refusing automatic transformation')
    a = original.index(START)
    b = original.index(END)
    if b <= a:
        raise ValueError('Mobile import boundaries reversed')
    old = original[a:b]
    checks = (
        'const id = `audio-${digest}`;',
        'async function importPickedAudio(plugin)',
        'async function importVaultAudio(plugin, file)',
        'await makeNoteAndOpen(plugin, file.name, audioPath, digest, progress);',
        'await makeNoteAndOpen(plugin, file.name, file.path, digest, progress);',
    )
    if any(old.count(value) != 1 for value in checks):
        raise ValueError('Mobile import implementation diverged from reviewed Agent E baseline')
    if 'const innermazeMobileIdentity' in original:
        raise ValueError('Mobile identity already integrated')
    if identity.count('module.exports = {parseAppleVoiceMemoUuid, mobileNoteFields, mobileBoundUuid, planMobileImport};') != 1:
        raise ValueError('Unexpected identity module version')
    stripped = identity.replace(
        'module.exports = {parseAppleVoiceMemoUuid, mobileNoteFields, mobileBoundUuid, planMobileImport};',
        'return {parseAppleVoiceMemoUuid, mobileNoteFields, mobileBoundUuid, planMobileImport};')
    # An IIFE avoids collisions with unrelated existing Innermaze symbols.
    replacement = ('// BEGIN Agent B mobile UUID import (source-only contribution)\n'
                   'const innermazeMobileIdentity = (() => {\n'
                   + stripped + '\n})();\n\n'
                   + methods + '\n'
                   '// END Agent B mobile UUID import\n\n')
    return original[:a] + replacement + original[b:]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True,
                   help='Existing Agent E worktree main.js, read-only')
    p.add_argument('--output', type=Path, required=True,
                   help='A NEW file outside the source worktree, e.g. ~/Code/.staging/Innermaze/agent-b-mobile/main.js')
    p.add_argument('--apply', action='store_true', help='Create only the requested NEW staged output')
    args = p.parse_args()
    source = args.source.expanduser().resolve(strict=True)
    output = args.output.expanduser().absolute()
    progress(1, 4, 'Verify source and separate staging destination')
    if source == output or (output.exists() and output.samefile(source)):
        raise ValueError('Refusing to overwrite source main.js')
    if output.exists() or output.is_symlink():
        raise ValueError('Refusing to overwrite existing output; preserve previous candidate')
    data = source.read_text(encoding='utf-8')
    identity = (HERE / 'mobile_recording_identity.cjs').read_text(encoding='utf-8')
    methods = (HERE / 'mobile_import_methods.cjs').read_text(encoding='utf-8')
    progress(2, 4, 'Check exact Agent E mobile import markers')
    generated = prepare(data, identity, methods)
    if 'const innermazeMobileIdentity' not in generated or not generated.startswith(data[:data.index(START)]):
        raise ValueError('Candidate verification failed')
    progress(3, 4, 'Validate create-only output plan')
    print(f'  Source: {source} (READ ONLY)')
    print(f'  Output: {output} ({"CREATE" if args.apply else "WOULD CREATE"})')
    print(f'  Candidate bytes: {len(generated.encode("utf-8"))}')
    if args.apply:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8', newline='\n') as target:
            target.write(generated)
    progress(4, 4, 'Candidate created' if args.apply else 'DRY RUN: all files unchanged')


if __name__ == '__main__':
    main()
