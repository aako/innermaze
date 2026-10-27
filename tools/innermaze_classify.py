#!/usr/bin/env python3
# filename: innermaze_classify.py
# version: 0.1.0
# timestamp: 2026-10-27 10:18:00
"""Classify one Innermaze note without changing it or its human review metadata.

The original Voice Memo Qwen v2.1.0 descriptor engine is deliberately vendored
unchanged at tools/legacy/voice_memo_describe_v2.1.0.py. Only --apply calls LM Studio
and writes an append-only AI run. No automatic review acceptance or note mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

VERSION = "0.1.0"
LEGACY = Path(__file__).resolve().parent / "legacy/voice_memo_describe_v2.1.0.py"
DEFAULT_TAXONOMY: dict[str, Any] = {
    "purpose_categories": {
        "note_or_idea_to_self": ["Note to self"],
        "dream_recollection": ["Dreams"],
        "personal_journal": ["Journal"],
        "learning_or_reference": ["Reference"],
        "conversation_documentation": ["Conversation"],
        "creative_work": ["Creative"],
        "performance_capture": ["Music / Performance"],
        "media_excerpt": ["Media excerpt"],
        "sound_reference": ["Ambience / Field"],
        "test_or_accidental": ["Accidental / Test"],
        "other": ["Other"],
    },
    "form_categories": {
        "lecture_or_presentation": ["Lecture / Talk"],
        "conversation": ["Conversation"],
        "interview": ["Interview"],
        "meeting_or_group_discussion": ["Meeting"],
        "performance_or_music": ["Music / Performance"],
        "media_capture": ["Media excerpt"],
        "ambience_or_field_recording": ["Ambience / Field"],
        "test_or_accidental": ["Accidental / Test"],
    },
    "tag_prefix": "recordings",
}


def meter(step: int, total: int, label: str) -> None:
    filled = round(24 * step / total)
    print(f"[{('#' * filled).ljust(24, '.')}] {step}/{total} | {label}", file=sys.stderr)


def load_legacy() -> Any:
    if not LEGACY.is_file():
        raise ValueError(f"Original classifier missing: {LEGACY}")
    spec = importlib.util.spec_from_file_location("innermaze_legacy_qwen_210", LEGACY)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load original classifier: {LEGACY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def frontmatter_and_body(raw: str) -> tuple[dict[str, str], str]:
    match = re.match(r"\A---\r?\n([\s\S]*?)\r?\n---\r?\n", raw)
    if not match:
        raise ValueError("Recording note lacks YAML frontmatter")
    props: dict[str, str] = {}
    for line in match.group(1).splitlines():
        found = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*):\s*(.*?)\s*$", line)
        if not found:
            continue
        value = found.group(2)
        if value.startswith(('"', "'")):
            try:
                parsed = json.loads(value)
                value = parsed if isinstance(parsed, str) else value.strip("'\"")
            except json.JSONDecodeError:
                value = value.strip("'\"")
        props[found.group(1)] = value
    return props, raw[match.end():]


def recording(note: Path) -> tuple[dict[str, Any], str, str, str]:
    raw = note.read_text(encoding="utf-8")
    props, body = frontmatter_and_body(raw)
    if props.get("voice_archive", "").lower() != "true":
        raise ValueError("Not an Innermaze voice_archive note")
    record_id = props.get("recording_id", "").strip()
    if not record_id:
        raise ValueError("Missing immutable recording_id")
    transcript_state = props.get("transcription_status", "").lower()
    if transcript_state and transcript_state != "ready":
        raise ValueError(f"Transcription is {transcript_state}; Qwen must be a separate later stage")
    if not body.strip():
        raise ValueError("No transcript body; run transcription first")
    text = "" if body.strip() == "*No speech detected in this recording.*" else body
    source_title = props.get("source_title") or note.stem
    return {
        "recording_id": record_id,
        "recording_source": props.get("recording_source", ""),
        "source_title": source_title,
        "recorded_at": props.get("recorded_at", ""),
        "source_sha256": props.get("source_sha256", ""),
        "voice_sync": props.get("voice_sync", ""),
        "transcription_status": props.get("transcription_status", ""),
    }, text, sha(raw), body


def taxonomy_from_file(path: Path | None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_TAXONOMY))
    if path is None:
        return config
    custom = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(custom, dict):
        raise ValueError("Taxonomy must be a JSON object")
    for key in ("purpose_categories", "form_categories"):
        if key in custom:
            if not isinstance(custom[key], dict):
                raise ValueError(f"{key} must be an object")
            for name, categories in custom[key].items():
                if (not isinstance(name, str) or not isinstance(categories, list)
                    or not all(isinstance(item, str) and item.strip() for item in categories)):
                    raise ValueError(f"Invalid {key} entry {name!r}; expected string array")
            config[key].update(custom[key])
    if "tag_prefix" in custom:
        if not isinstance(custom["tag_prefix"], str) or not re.fullmatch(r"[a-zA-Z0-9_/-]*", custom["tag_prefix"]):
            raise ValueError("tag_prefix contains invalid Obsidian tag characters")
        config["tag_prefix"] = custom["tag_prefix"].strip("/")
    unknown = set(custom) - {"purpose_categories", "form_categories", "tag_prefix"}
    if unknown:
        raise ValueError(f"Unknown taxonomy keys: {sorted(unknown)}")
    return config


def suggestions(descriptor: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    categories = list(dict.fromkeys(
        taxonomy["purpose_categories"].get(descriptor["purpose_guess"], [])
        + taxonomy["form_categories"].get(descriptor["recording_form"], [])
    ))
    def slug(value: str) -> str:
        return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")
    prefix = taxonomy["tag_prefix"]
    tags = [(prefix + "/" if prefix else "") + slug(name) for name in categories if slug(name)]
    return {
        "description": descriptor["summary"],
        "recording_type": descriptor["freeform_type"] or descriptor["recording_form"],
        "recording_form": descriptor["recording_form"],
        "purpose": descriptor["purpose_guess"],
        "categories": categories,
        "keywords": descriptor["subject_tags"],
        "tags": tags,
        "people_entities": descriptor["people_entities"],
    }


def classification_metadata(note_data: dict[str, Any], text: str, legacy: Any, limit: int) -> tuple[dict[str, Any], str]:
    recorded_at = note_data["recorded_at"]
    if not recorded_at:
        recorded_at, _ = legacy.parse_voice_memo_filename(note_data["source_title"])
    sample, sampled = legacy.sample_transcript(text, limit)
    words = len(text.split())
    status = "blank" if not words else "very_sparse" if words < 20 else "text"
    return {
        "record_id": note_data["recording_id"],
        "source": note_data["recording_source"],
        "original_title": note_data["source_title"],
        "recorded_at": recorded_at,
        "duration_seconds": None,
        "audio_bytes": None,
        "transcript_chars": len(text),
        "transcript_words": words,
        "transcript_status": status,
        "transcript_sampled": sampled,
    }, sample


def create_only(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp, path)  # Atomic, refuses overwrites and incomplete final files.
    finally:
        temp.unlink(missing_ok=True)


def classify(note: Path, output_root: Path, *, server: str, model: str,
             prompt_file: Path | None = None, taxonomy_file: Path | None = None,
             apply: bool = False, call: Callable[..., dict[str, Any]] | None = None,
             resolve: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    meter(1, 5, "Verify recording and transcript")
    note = note.expanduser().resolve(strict=True)
    note_data, transcript, note_hash, transcript_original = recording(note)
    legacy = load_legacy()
    taxonomy = taxonomy_from_file(taxonomy_file)
    prompt = (prompt_file.read_text(encoding="utf-8") if prompt_file else legacy.SYSTEM_PROMPT)
    if not prompt.strip():
        raise ValueError("Prompt cannot be blank")
    metadata, sample = classification_metadata(note_data, transcript, legacy, legacy.DEFAULT_MAX_TRANSCRIPT_CHARS)
    meter(2, 5, "Snapshot model prompt and taxonomy")
    plan = {"recording_id": note_data["recording_id"], "model_requested": model,
            "transcript_sha256": sha(transcript_original), "prompt_sha256": sha(prompt),
            "taxonomy_sha256": sha(json.dumps(taxonomy, sort_keys=True))}
    if not apply:
        meter(5, 5, "DRY RUN: no model call and no files changed")
        return {"status": "dry_run", **plan}
    legacy.ensure_local_server(server)
    actual_model = resolve(server, model) if resolve else legacy.resolve_model(server, model)
    meter(3, 5, f"Run verified local Qwen model: {actual_model}")
    # The vendored descriptor runner uses a module-level prompt, so keep this
    # invocation process-local. The GUI must spawn one subprocess per job.
    legacy.SYSTEM_PROMPT = prompt
    descriptor = call(server=server, model=actual_model, metadata=metadata,
                      transcript_sample=sample) if call else legacy.call_descriptor(
                          server=server, model=actual_model, metadata=metadata,
                          transcript_sample=sample)
    descriptor = legacy.validate_descriptor(descriptor)
    reasons: list[str] = []
    if metadata["transcript_status"] in {"blank", "very_sparse"}:
        descriptor["needs_audio_analysis"] = True
        descriptor["confidence"] = min(descriptor["confidence"], 0.65)
        reasons.append("sparse_or_missing_transcript")
    if descriptor["confidence"] < 0.8:
        reasons.append("low_confidence")
    if descriptor["needs_audio_analysis"]:
        reasons.append("needs_audio_analysis")
    if descriptor["recording_form"] == "unclear" or descriptor["purpose_guess"] == "unclear":
        reasons.append("unclear_recording_classification")
    # If the text was edited while Qwen ran, do not publish an already-stale result.
    if sha(note.read_text(encoding="utf-8")) != note_hash:
        raise ValueError("Recording note changed while Qwen ran; no classification saved")
    meter(4, 5, "Write append-only AI result, not human review")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    run_id = now.replace(":", "-") + "-" + uuid.uuid4().hex[:10]
    result = {
        "format": "innermaze-classification-v1", "run_id": run_id,
        "recording_id": note_data["recording_id"], "created_at": now,
        "note_path_at_run": str(note), "note_sha256": note_hash,
        "source": note_data,
        "recorded_at": metadata["recorded_at"],
        "recorded_at_origin": ("note_property" if note_data["recorded_at"] else
                               "source_title" if metadata["recorded_at"] else "unknown"),
        "transcript_snapshot": transcript_original,
        "transcript_sha256": plan["transcript_sha256"], "transcript_sampled": metadata["transcript_sampled"],
        "model_requested": model, "model_resolved": actual_model,
        "engine": "voice_memo_describe_v2.1.0", "prompt_snapshot": prompt,
        "prompt_sha256": plan["prompt_sha256"], "taxonomy_snapshot": taxonomy,
        "taxonomy_sha256": plan["taxonomy_sha256"],
        "raw_descriptor": descriptor, "ai": suggestions(descriptor, taxonomy),
        "needs_human_review": bool(reasons), "review_reasons": reasons,
    }
    target = output_root.expanduser().resolve() / sha(note_data["recording_id"]) / "runs" / (run_id + ".json")
    create_only(target, result)
    meter(5, 5, "Complete; note, transcript, player, and human decisions unchanged")
    return {"status": "classified", "file": str(target), "run_id": run_id,
            "recording_id": note_data["recording_id"], "needs_human_review": bool(reasons),
            "suggestions": result["ai"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--note", required=True, type=Path, help="Existing Innermaze .md recording note")
    parser.add_argument("--output-root", type=Path, help="Defaults to Voice Archive/Classification")
    parser.add_argument("--server", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--model", default="voice-memo-qwen")
    parser.add_argument("--prompt-file", type=Path, help="Optional complete alternate classifier prompt")
    parser.add_argument("--taxonomy", type=Path, help="Optional JSON overrides for category suggestions")
    parser.add_argument("--apply", action="store_true", help="Run local model and append AI result; default dry run")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    try:
        path = args.note.expanduser().resolve(strict=True)
        archive = next((parent for parent in path.parents if parent.name == "Voice Archive"), None)
        if args.output_root is None and archive is None:
            raise ValueError("Note not under Voice Archive; supply --output-root explicitly")
        output = args.output_root or archive / "Classification"
        result = classify(path, output, server=args.server, model=args.model,
                          prompt_file=args.prompt_file, taxonomy_file=args.taxonomy,
                          apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
