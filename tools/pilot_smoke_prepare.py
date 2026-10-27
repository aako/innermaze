#!/usr/bin/env python3
# filename: pilot_smoke_prepare.py
# version: 0.1.0
# timestamp: 2026-10-27 12:40:00
"""Prepare a NEW synthetic Obsidian vault for a human-controlled Mango GUI pilot.

No live Cabinet writes, original recording reads, worker scheduling, model calls,
or BRAT release. Dry run by default; --apply creates an isolated scratch fixture.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def meter(n: int, total: int, label: str) -> None:
    done = 24 * n // total
    print(f'[{"#" * done}{"." * (24-done)}] {n}/{total} | {label}', flush=True)


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--root', type=Path, default=Path.home()/'Code/.staging/Innermaze')
    args=p.parse_args()
    repo=Path(__file__).resolve().parents[1]
    helper=repo/'tools/innermaze_import_process.py'
    for file in (helper, repo/'main.js', repo/'manifest.json', repo/'styles.css'):
        if not file.is_file(): raise SystemExit(f'STOP: missing candidate file: {file}')
    meter(1,4,'Verify isolated candidate and locally installed ffmpeg')
    if not shutil.which('ffmpeg'): raise SystemExit('STOP: ffmpeg unavailable')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target=args.root.expanduser().resolve() / f'pilot-hardening-{stamp}'
    vault=target/'Synthetic Obsidian Vault'
    audio=target/'Synthetic Voice Memos'/ 'Synthetic original from Finder.m4a'
    addon=vault/'.obsidian/plugins/innermaze'
    meter(2,4,'Plan synthetic fixture; real Cabinet is never selected')
    print('  Synthetic root:',target)
    print('  Test vault:',vault)
    print('  Candidate helper:',helper)
    if not args.apply:
        meter(4,4,'DRY RUN: no files or model jobs created')
        return
    if target.exists(): raise SystemExit('STOP: scratch target exists; refusing overwrite')
    addon.mkdir(parents=True,exist_ok=False)
    audio.parent.mkdir(parents=True,exist_ok=False)
    meter(3,4,'Generate synthetic audio and isolated Obsidian plugin test installation')
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-f','lavfi',
         '-i','sine=frequency=630:duration=3.2','-c:a','aac','-b:a','128k',
         '-movflags','use_metadata_tags','-metadata',
         'voice-memo-uuid=9FD6F04C-3840-4C0E-98E8-9411D0FDF110',str(audio)]
    subprocess.run(cmd,check=True)
    for name in ('main.js','manifest.json','styles.css'):
        shutil.copyfile(repo/name,addon/name)
    (addon/'data.json').write_text(json.dumps({'importProcessHelperPath':str(helper)},indent=2)+'\n','utf-8')
    (vault/'.obsidian/community-plugins.json').write_text('["innermaze"]\n','utf-8')
    metadata={'schema':'innermaze-synthetic-pilot-v1',
              'audio_sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),
              'audio_path':str(audio),'vault_path':str(vault),
              'helper_path':str(helper),'model_calls_performed':0,
              'notes_imported':0, 'original_archives_modified':False}
    (target/'pilot-fixture.json').write_text(json.dumps(metadata,indent=2)+'\n','utf-8')
    meter(4,4,'Synthetic vault and unmodified candidate plugin staged; no processing run')
    print('Open only the synthetic vault in Obsidian and review docs/agents/AGENT_E_PILOT_PROCEDURE.md')
    print('AUDIT: No live Cabinet, worker, Apple archive, MLX or Qwen action was performed')

if __name__ == '__main__':
    main()
