#!/usr/bin/env python3
# filename: voice_memo_describe.py
# version: 2.1.0
# timestamp: 2026-10-01 17:52:00

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_SERVER = "http://127.0.0.1:1234/v1"
DEFAULT_WHISPER_ROOT = Path.home() / "Code/VoiceMemoSaver/Whisper"
DEFAULT_OUTPUT_ROOT = Path.home() / "Code/VoiceMemoSaver/Classification-Qwen"
DEFAULT_MAX_TRANSCRIPT_CHARS = 36000

SOURCE_DIRS = {
    "voice-memos": "voice-memos",
    "jpr": "just-press-record",
}

SYSTEM_PROMPT = """\
You are cataloging a private personal archive of audio recordings.

This is STAGE 1: DESCRIPTION, NOT FINAL FOLDER CLASSIFICATION.

Your job is to describe what the evidence supports without forcing the recording
into a predetermined taxonomy. The next stage will derive categories across the
whole archive from these descriptors.

Rules:
- Use only the filename, technical metadata, and transcript excerpts provided.
- Do not invent identities, sources, events, or context.
- A blank or sparse transcript does NOT imply music. It may be music, ambience,
  silence, poor audio, non-speech, or a transcription failure.
- When evidence is weak, say so and lower confidence.
- "needs_audio_analysis" must be true when audio itself is needed to determine
  what the recording is (especially blank/sparse transcripts).
- Do NOT decide whether human review is required. The script calculates that
  deterministically after your response.
- language_primary and language_secondary MUST use ISO 639-1 two-letter codes
  where possible (en, es, fr, hi, ko, etc.). Use "und" when uncertain and
  "zxx" when there is no linguistic content. Do not write prose in language
  code fields.
- "recording_form" describes the interaction/form, not the subject matter.
- "purpose_guess" is a tentative archival-use description, not a final folder.
- subject_tags should be concise concepts actually supported by the evidence.
- people_entities should contain only people explicitly named in the filename
  or transcript. Do not infer names from context.
- Keep summaries concise and useful for later human review.
"""

SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "voice_memo_descriptor_v2",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "recording_form": {
                    "type": "string",
                    "enum": [
                        "self_monologue",
                        "single_speaker_other",
                        "lecture_or_presentation",
                        "conversation",
                        "interview",
                        "meeting_or_group_discussion",
                        "performance_or_music",
                        "media_capture",
                        "ambience_or_field_recording",
                        "test_or_accidental",
                        "unclear",
                    ],
                },
                "purpose_guess": {
                    "type": "string",
                    "enum": [
                        "note_or_idea_to_self",
                        "dream_recollection",
                        "personal_journal",
                        "learning_or_reference",
                        "conversation_documentation",
                        "creative_work",
                        "performance_capture",
                        "media_excerpt",
                        "sound_reference",
                        "test_or_accidental",
                        "other",
                        "unclear",
                    ],
                },
                "freeform_type": {"type": "string"},
                "language_status": {
                    "type": "string",
                    "enum": ["known", "mixed", "uncertain", "none"],
                },
                "language_primary": {"type": "string"},
                "language_secondary": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 6,
                },
                "subject_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "people_entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "content_signals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "needs_audio_analysis": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": [
                "summary",
                "recording_form",
                "purpose_guess",
                "freeform_type",
                "language_status",
                "language_primary",
                "language_secondary",
                "subject_tags",
                "people_entities",
                "content_signals",
                "confidence",
                "needs_audio_analysis",
                "reason",
            ],
            "additionalProperties": False,
        },
    },
}

OUTPUT_FIELDS = [
    "record_id",
    "source",
    "source_audio",
    "output_stem",
    "recorded_at",
    "original_title",
    "duration_seconds",
    "audio_bytes",
    "audio_sha256",
    "transcript_chars",
    "transcript_words",
    "transcript_sampled",
    "transcript_status",
    "summary",
    "recording_form",
    "purpose_guess",
    "freeform_type",
    "language_status",
    "language_primary",
    "language_secondary",
    "subject_tags",
    "people_entities",
    "content_signals",
    "confidence",
    "needs_audio_analysis",
    "needs_human_review",
    "review_reasons",
    "reason",
    "model",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 1 local-LLM descriptor pass for Voice Memos/JPR transcripts. "
            "Writes derived catalog data only; never moves audio."
        )
    )
    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help=f"LM Studio OpenAI-compatible base URL (default: {DEFAULT_SERVER})",
    )
    parser.add_argument(
        "--model",
        default="voice-memo-qwen",
        help=(
            "LM Studio model id/identifier. Default: voice-memo-qwen. "
            "The script never silently chooses a different model."
        ),
    )
    parser.add_argument(
        "--source",
        choices=["all", "voice-memos", "jpr"],
        default="all",
        help="Which transcript source to describe (default: all)",
    )
    parser.add_argument(
        "--whisper-root",
        type=Path,
        default=DEFAULT_WHISPER_ROOT,
        help=f"Whisper output root (default: {DEFAULT_WHISPER_ROOT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Classification output root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--max-transcript-chars",
        type=int,
        default=DEFAULT_MAX_TRANSCRIPT_CHARS,
        help=(
            "Maximum transcript characters sent per recording. Long transcripts "
            "are sampled from beginning/middle/end (default: 36000)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N pending recordings; useful for QA.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Regenerate descriptors even when the existing descriptor already "
            "uses the requested model. A timestamped backup is created first."
        ),
    )
    parser.add_argument(
        "--only-review",
        action="store_true",
        help="Reprocess only existing items currently marked needs_human_review.",
    )
    return parser.parse_args()


def ensure_local_server(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            f"Refusing non-local LLM endpoint for privacy: {url}\n"
            "Use LM Studio on localhost."
        )
    if parsed.scheme != "http":
        raise RuntimeError(
            f"Expected a local http:// LM Studio endpoint, got: {url}"
        )


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"LM Studio HTTP {exc.code} for {url}\n{body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Could not reach LM Studio on localhost.\n"
            "Start its local server in Developer, or run: lms server start"
        ) from exc


def resolve_model(server: str, requested: str | None) -> str:
    # OpenAI-compatible /v1/models tells us what this server will accept.
    # LM Studio's native /api/v1/models tells us the model type. Cross-reference
    # them so embedding models are ignored, but JIT-loadable LLMs are still
    # valid even when loaded_instances is currently empty.
    openai_result = http_json(f"{server.rstrip('/')}/models")
    served_ids = [
        str(item.get("id", "")).strip()
        for item in openai_result.get("data", [])
        if str(item.get("id", "")).strip()
    ]

    parsed = urllib.parse.urlparse(server)
    api_root = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port is not None:
        api_root += f":{parsed.port}"

    native_result = http_json(f"{api_root}/api/v1/models")
    native_models = native_result.get("models", [])

    def native_type_for(served_id: str) -> str | None:
        for item in native_models:
            candidates = {
                str(item.get("key", "")).strip(),
                str(item.get("selected_variant", "")).strip(),
            }
            for instance in item.get("loaded_instances", []):
                candidates.add(str(instance.get("id", "")).strip())

            if served_id in candidates:
                return str(item.get("type", "")).strip() or None

        return None

    served_llms = [
        model_id
        for model_id in served_ids
        if native_type_for(model_id) == "llm"
    ]

    # Compatibility fallback: if LM Studio changes identifier formatting, use
    # an exact native LLM key that is also visible by substring in /v1/models.
    if not served_llms:
        native_llm_keys = [
            str(item.get("key", "")).strip()
            for item in native_models
            if item.get("type") == "llm" and str(item.get("key", "")).strip()
        ]
        for key in native_llm_keys:
            if any(key in served_id or served_id in key for served_id in served_ids):
                served_llms.append(key)

    served_llms = list(dict.fromkeys(served_llms))

    if not served_llms:
        raise RuntimeError(
            "LM Studio is reachable, but no served LLM could be identified.\n"
            "The server may only be exposing embedding models."
        )

    if requested is None:
        if len(served_llms) == 1:
            return served_llms[0]

        joined = "\n  ".join(served_llms)
        raise RuntimeError(
            "More than one LLM is served. Choose one with --model.\n"
            f"  {joined}"
        )

    if requested in served_llms:
        return requested

    matches = [
        model
        for model in served_llms
        if requested.lower() in model.lower()
    ]

    if len(matches) == 1:
        return matches[0]

    if not matches:
        raise RuntimeError(
            f"No served LM Studio LLM matches --model {requested!r}.\n"
            f"Served LLMs: {', '.join(served_llms)}"
        )

    raise RuntimeError(
        f"--model {requested!r} matches multiple served LLMs:\n  "
        + "\n  ".join(matches)
    )


def parse_voice_memo_filename(stem: str) -> tuple[str, str]:
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}) - (.*)$",
        stem,
    )
    if not match:
        return "", stem
    return match.group(1), match.group(2)


def sample_transcript(text: str, max_chars: int) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= max_chars:
        return text, False

    # Representative beginning/middle/end sampling for classification.
    start_chars = int(max_chars * 0.40)
    middle_chars = int(max_chars * 0.20)
    end_chars = max_chars - start_chars - middle_chars

    midpoint = len(text) // 2
    middle_start = max(0, midpoint - middle_chars // 2)

    sample = (
        text[:start_chars]
        + "\n\n[... MIDDLE EXCERPT ...]\n\n"
        + text[middle_start : middle_start + middle_chars]
        + "\n\n[... END EXCERPT ...]\n\n"
        + text[-end_chars:]
    )
    return sample, True


def load_manifest(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise RuntimeError(f"Missing transcription manifest: {path}")

    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for line_no, row in enumerate(reader, start=1):
            if not row:
                continue
            if row[0] == "status":
                continue
            if len(row) not in {4, 5}:
                raise RuntimeError(
                    f"Unexpected manifest row {path}:{line_no}: {row!r}"
                )
            status, source_audio, output_stem, audio_bytes = row[:4]
            audio_sha256 = row[4].strip() if len(row) == 5 else ""
            if audio_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", audio_sha256):
                raise RuntimeError(
                    f"Invalid SHA-256 in {path}:{line_no}: {audio_sha256!r}"
                )
            rows.append(
                {
                    "transcription_status": status,
                    "source_audio": source_audio,
                    "output_stem": output_stem,
                    "audio_bytes": audio_bytes,
                    "audio_sha256": audio_sha256.lower(),
                }
            )
    return rows


def load_review_metadata(path: Path) -> dict[str, dict[str, str]]:
    """Read duration/audio metadata produced by the Whisper pass.

    This deliberately avoids reopening the original audio files. In particular,
    JPR lives in another app's protected iCloud container and touching it during
    classification causes macOS App Data/TCC permission prompts.
    """
    if not path.is_file():
        raise RuntimeError(f"Missing transcription review manifest: {path}")

    result: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"source", "duration_seconds", "audio_bytes"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise RuntimeError(
                f"Unexpected review.tsv columns in {path}: {reader.fieldnames!r}"
            )

        for row in reader:
            source_audio = str(row.get("source", "")).strip()
            if not source_audio:
                continue
            result[source_audio] = {
                "duration_seconds": str(row.get("duration_seconds", "")).strip(),
                "audio_bytes": str(row.get("audio_bytes", "")).strip(),
            }

    return result


def legacy_record_id(source: str, source_audio: str) -> str:
    digest = hashlib.sha256(
        f"{source}\0{source_audio}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{source}-{digest}"


def stable_record_id(source: str, source_audio: str, audio_sha256: str = "") -> str:
    """Title/path-proof descriptor identity.

    v2.3+ Whisper manifests provide the audio SHA-256. Older manifests fall
    back to the legacy source-path identity so upgrades remain compatible.
    """
    audio_sha256 = audio_sha256.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", audio_sha256):
        return f"{source}-audio-{audio_sha256[:24]}"
    return legacy_record_id(source, source_audio)


def make_user_prompt(metadata: dict[str, Any], transcript_sample: str) -> str:
    if transcript_sample:
        transcript_block = transcript_sample
    else:
        transcript_block = "[NO USABLE TRANSCRIPT TEXT]"

    return f"""\
Describe this recording for archival cataloging.

SOURCE: {metadata['source']}
FILENAME TITLE: {metadata['original_title']}
RECORDED AT FROM FILENAME: {metadata['recorded_at'] or 'unknown'}
DURATION SECONDS: {metadata['duration_seconds']}
AUDIO BYTES: {metadata['audio_bytes']}
TRANSCRIPT WORDS: {metadata['transcript_words']}
TRANSCRIPT CHARS: {metadata['transcript_chars']}
TRANSCRIPT STATUS: {metadata['transcript_status']}
TRANSCRIPT WAS SAMPLED: {metadata['transcript_sampled']}

TRANSCRIPT / REPRESENTATIVE EXCERPTS:
--- BEGIN ---
{transcript_block}
--- END ---

Return EXACTLY one JSON object and nothing else.
Do not use Markdown fences.
The object MUST conform to this JSON Schema:

{json.dumps(SCHEMA["json_schema"]["schema"], ensure_ascii=False)}
"""


def _strip_json_fences(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content


def validate_language_code(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"Descriptor field {field} must be a string.")
    code = value.strip().lower()
    if code in {"und", "zxx"}:
        return code
    if not re.fullmatch(r"[a-z]{2}", code):
        raise RuntimeError(
            f"Descriptor field {field} must be an ISO 639-1 two-letter code, "
            f"'und', or 'zxx'; got {value!r}."
        )
    return code


def validate_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    schema = SCHEMA["json_schema"]["schema"]
    required = schema["required"]

    missing = [field for field in required if field not in descriptor]
    if missing:
        raise RuntimeError(
            "Descriptor JSON is missing required field(s): " + ", ".join(missing)
        )

    unknown = sorted(set(descriptor) - set(schema["properties"]))
    if unknown:
        raise RuntimeError(
            "Descriptor JSON contains unexpected field(s): " + ", ".join(unknown)
        )

    for field in ("recording_form", "purpose_guess", "language_status"):
        allowed = set(schema["properties"][field]["enum"])
        if descriptor[field] not in allowed:
            raise RuntimeError(
                f"Descriptor {field}={descriptor[field]!r} is not in the allowed enum."
            )

    for field in (
        "language_secondary",
        "subject_tags",
        "people_entities",
        "content_signals",
    ):
        if not isinstance(descriptor[field], list):
            raise RuntimeError(f"Descriptor field {field} must be an array.")

    if not isinstance(descriptor["needs_audio_analysis"], bool):
        raise RuntimeError("Descriptor field needs_audio_analysis must be boolean.")

    descriptor["language_primary"] = validate_language_code(
        descriptor["language_primary"], "language_primary"
    )
    descriptor["language_secondary"] = [
        validate_language_code(item, "language_secondary")
        for item in descriptor["language_secondary"]
    ]

    status = descriptor["language_status"]
    primary = descriptor["language_primary"]
    secondary = descriptor["language_secondary"]

    # Normalize language edge cases instead of failing a whole descriptor.
    #
    # Qwen sometimes correctly recognizes a mixed-language recording but can
    # only identify one language, yielding e.g. primary="und", secondary=["en"]
    # or primary="en", secondary=[]. Preserve the uncertainty mechanically.
    # "und" is allowed as a secondary marker meaning "another language is
    # present but not identified."
    secondary = [
        code
        for index, code in enumerate(secondary)
        if code != primary and code not in secondary[:index]
    ]

    if status == "none":
        descriptor["language_primary"] = "zxx"
        descriptor["language_secondary"] = []

    elif status == "uncertain":
        descriptor["language_primary"] = "und"
        descriptor["language_secondary"] = []

    elif status == "known":
        if primary in {"und", "zxx"}:
            descriptor["language_status"] = "uncertain"
            descriptor["language_primary"] = "und"
            descriptor["language_secondary"] = []
        else:
            descriptor["language_secondary"] = []

    elif status == "mixed":
        real_secondary = [
            code for code in secondary
            if code not in {"und", "zxx"}
        ]
        has_unknown_secondary = "und" in secondary

        if primary in {"und", "zxx"}:
            if real_secondary:
                # Promote the first identified language to primary, retaining
                # an unknown-language marker because Qwen explicitly said the
                # recording is mixed.
                descriptor["language_primary"] = real_secondary[0]
                remaining = real_secondary[1:]
                if "und" not in remaining:
                    remaining.append("und")
                descriptor["language_secondary"] = remaining
            else:
                descriptor["language_status"] = "uncertain"
                descriptor["language_primary"] = "und"
                descriptor["language_secondary"] = []
        elif secondary:
            descriptor["language_secondary"] = [
                code for code in secondary if code != "zxx"
            ]
        else:
            # Qwen said mixed but supplied no second language. Retain the
            # useful fact that another language is present but unidentified.
            descriptor["language_secondary"] = ["und"]

        # A real primary plus only duplicate/zxx cleanup can still leave no
        # secondary; use an unknown-language marker rather than throwing away
        # Qwen's "mixed" judgment.
        if (
            descriptor["language_status"] == "mixed"
            and descriptor["language_primary"] not in {"und", "zxx"}
            and not descriptor["language_secondary"]
        ):
            descriptor["language_secondary"] = ["und"]

    try:
        confidence = float(descriptor["confidence"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Descriptor confidence must be numeric.") from exc

    if not 0 <= confidence <= 1:
        raise RuntimeError("Descriptor confidence must be between 0 and 1.")
    descriptor["confidence"] = confidence

    for field in ("summary", "freeform_type", "reason"):
        if not isinstance(descriptor[field], str):
            raise RuntimeError(f"Descriptor field {field} must be a string.")

    return descriptor

def call_descriptor(
    *,
    server: str,
    model: str,
    metadata: dict[str, Any],
    transcript_sample: str,
) -> dict[str, Any]:
    # LM Studio's native endpoint exposes explicit reasoning="off", which is
    # more reliable for Qwen3.5 than chat-template thinking flags.
    parsed = urllib.parse.urlparse(server)
    api_root = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port is not None:
        api_root += f":{parsed.port}"

    base_input = make_user_prompt(metadata, transcript_sample)
    last_problem: Exception | None = None

    for attempt in range(2):
        retry_suffix = ""
        if attempt:
            retry_suffix = (
                "\n\nRETRY REQUIREMENT: Your previous response could not be "
                "parsed or validated. Return EXACTLY one valid JSON object "
                "matching the supplied schema. No prose, Markdown, reasoning, "
                "or code fences."
            )

        payload = {
            "model": model,
            "system_prompt": SYSTEM_PROMPT,
            "input": base_input + retry_suffix,
            "reasoning": "off",
            "temperature": 0.2 if attempt == 0 else 0.0,
            "top_p": 0.8 if attempt == 0 else 1.0,
            "top_k": 20,
            "max_output_tokens": 1800,
            "stream": False,
        }

        result = http_json(
            f"{api_root}/api/v1/chat",
            method="POST",
            payload=payload,
            timeout=1800,
        )

        message_parts = [
            str(item.get("content", ""))
            for item in result.get("output", [])
            if item.get("type") == "message"
        ]
        content = "\n".join(
            part for part in message_parts if part
        ).strip()

        if not content:
            last_problem = RuntimeError(
                "LM Studio returned no final message content:\n"
                + json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                )[:5000]
            )
            if attempt == 0:
                continue
            raise last_problem

        content = _strip_json_fences(content)

        try:
            descriptor = json.loads(content)
            if not isinstance(descriptor, dict):
                raise RuntimeError(
                    "LM Studio descriptor response must be a JSON object."
                )
            return validate_descriptor(descriptor)

        except (json.JSONDecodeError, RuntimeError) as exc:
            last_problem = exc
            if attempt == 0:
                continue

            if isinstance(exc, json.JSONDecodeError):
                raise RuntimeError(
                    "LM Studio final message was not valid JSON after retry:\n"
                    + content[:5000]
                ) from exc
            raise

    raise RuntimeError(
        f"Descriptor generation failed unexpectedly: {last_problem}"
    )


def item_output_path(output_root: Path, source: str, record_id: str) -> Path:
    return output_root / "items" / source / f"{record_id}.json"


def migrate_legacy_item_identity(
    output_root: Path,
    source: str,
    row: dict[str, str],
    new_record_id: str,
) -> Path:
    """Move one old path-keyed descriptor to its new content-keyed identity.

    A copy of the exact pre-migration JSON is retained outside items/ so
    aggregate catalog generation cannot double-count it.
    """
    new_path = item_output_path(output_root, source, new_record_id)
    if new_path.exists():
        return new_path

    old_record_id = legacy_record_id(source, row["source_audio"])
    if old_record_id == new_record_id:
        return new_path

    old_path = item_output_path(output_root, source, old_record_id)
    if not old_path.exists():
        return new_path

    try:
        data = json.loads(old_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not migrate legacy descriptor: {old_path}") from exc

    backup_dir = output_root / "identity-migration-backups" / source
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / old_path.name
    if not backup_path.exists():
        shutil.copy2(old_path, backup_path)

    data["legacy_record_id"] = old_record_id
    data["record_id"] = new_record_id
    data["audio_sha256"] = row.get("audio_sha256", "")
    data["source_audio"] = row["source_audio"]

    new_path.parent.mkdir(parents=True, exist_ok=True)
    temp = new_path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, new_path)
    old_path.unlink()
    return new_path


def refresh_existing_identity_metadata(path: Path, row: dict[str, str]) -> None:
    """Update mutable source aliases without regenerating the AI descriptor."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    changed = False
    for key, value in (
        ("source_audio", row.get("source_audio", "")),
        ("audio_sha256", row.get("audio_sha256", "")),
    ):
        if value and data.get(key) != value:
            data[key] = value
            changed = True

    if not changed:
        return

    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)



def existing_descriptor_model(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        model = str(data.get("model", "")).strip()
        return model or None
    except (OSError, json.JSONDecodeError):
        return None


def existing_review_flag(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("needs_human_review"))
    except (OSError, json.JSONDecodeError):
        return True


def describe_one(
    *,
    source: str,
    row: dict[str, str],
    transcript_dir: Path,
    output_root: Path,
    server: str,
    model: str,
    max_transcript_chars: int,
) -> dict[str, Any]:
    source_audio = row["source_audio"]
    stem = row["output_stem"]
    txt_path = transcript_dir / f"{stem}.txt"
    json_path = transcript_dir / f"{stem}.json"

    text = ""
    if txt_path.exists():
        text = txt_path.read_text(encoding="utf-8", errors="replace")

    transcript_chars = len(text)
    transcript_words = len(text.split())

    if not txt_path.exists():
        transcript_status = "missing"
    elif transcript_words == 0:
        transcript_status = "blank"
    elif transcript_words < 20:
        transcript_status = "very_sparse"
    else:
        transcript_status = "text"

    sample, sampled = sample_transcript(text, max_transcript_chars)

    if source == "voice-memos":
        recorded_at, title = parse_voice_memo_filename(stem)
    else:
        recorded_at, title = "", stem

    raw_duration = str(row.get("duration_seconds", "")).strip()
    try:
        duration = float(raw_duration) if raw_duration else None
    except ValueError:
        duration = None

    metadata: dict[str, Any] = {
        "record_id": stable_record_id(source, source_audio, row.get("audio_sha256", "")),
        "source": source,
        "source_audio": source_audio,
        "output_stem": stem,
        "recorded_at": recorded_at,
        "original_title": title,
        "duration_seconds": duration,
        "audio_bytes": int(row["audio_bytes"]) if row["audio_bytes"] else None,
        "audio_sha256": row.get("audio_sha256", ""),
        "transcript_chars": transcript_chars,
        "transcript_words": transcript_words,
        "transcript_sampled": sampled,
        "transcript_status": transcript_status,
    }

    descriptor = call_descriptor(
        server=server,
        model=model,
        metadata=metadata,
        transcript_sample=sample,
    )

    # Mechanical safety gate: a text-only model may never declare a blank/sparse
    # recording fully understood without hearing the audio.
    if transcript_status in {"missing", "blank", "very_sparse"}:
        descriptor["needs_audio_analysis"] = True
        descriptor["confidence"] = min(float(descriptor["confidence"]), 0.65)

    review_reasons: list[str] = []
    if float(descriptor["confidence"]) < 0.80:
        review_reasons.append("low_confidence")
    if descriptor["needs_audio_analysis"]:
        review_reasons.append("needs_audio_analysis")
    if descriptor["recording_form"] == "unclear":
        review_reasons.append("unclear_recording_form")
    if descriptor["purpose_guess"] == "unclear":
        review_reasons.append("unclear_purpose")
    if transcript_status in {"missing", "blank", "very_sparse"}:
        review_reasons.append("sparse_or_missing_transcript")

    descriptor["needs_human_review"] = bool(review_reasons)
    descriptor["review_reasons"] = review_reasons

    record = {
        **metadata,
        **descriptor,
        "model": model,
        "transcript_json_exists": json_path.exists(),
        "described_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    item_path = item_output_path(
        output_root,
        source,
        metadata["record_id"],
    )
    item_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = item_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, item_path)

    return record


def iter_all_item_files(output_root: Path) -> list[Path]:
    items_root = output_root / "items"
    if not items_root.exists():
        return []
    return sorted(items_root.glob("*/*.json"))


def write_aggregate_catalogs(output_root: Path) -> None:
    records: list[dict[str, Any]] = []
    for path in iter_all_item_files(output_root):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read descriptor item: {path}") from exc

    records.sort(
        key=lambda item: (
            str(item.get("recorded_at", "")),
            str(item.get("source", "")),
            str(item.get("original_title", "")),
        )
    )

    output_root.mkdir(parents=True, exist_ok=True)

    jsonl_tmp = output_root / "catalog.jsonl.tmp"
    with jsonl_tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(jsonl_tmp, output_root / "catalog.jsonl")

    csv_tmp = output_root / "catalog.csv.tmp"
    with csv_tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for record in records:
            row: dict[str, Any] = {}
            for field in OUTPUT_FIELDS:
                value = record.get(field, "")
                if isinstance(value, list):
                    value = " | ".join(str(item) for item in value)
                row[field] = value
            writer.writerow(row)
    os.replace(csv_tmp, output_root / "catalog.csv")

    review_tmp = output_root / "review.csv.tmp"
    with review_tmp.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "recorded_at",
            "source",
            "original_title",
            "recording_form",
            "purpose_guess",
            "freeform_type",
            "language_status",
            "language_primary",
            "confidence",
            "needs_audio_analysis",
            "needs_human_review",
            "review_reasons",
            "summary",
            "reason",
            "source_audio",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            if not (
                record.get("needs_human_review")
                or record.get("needs_audio_analysis")
                or float(record.get("confidence", 0)) < 0.80
            ):
                continue
            review_row: dict[str, Any] = {}
            for field in fields:
                value = record.get(field, "")
                if isinstance(value, list):
                    value = " | ".join(str(item) for item in value)
                review_row[field] = value
            writer.writerow(review_row)
    os.replace(review_tmp, output_root / "review.csv")



def backup_existing_output(output_root: Path) -> Path | None:
    """Snapshot an existing descriptor tree before a forced replacement run."""
    if not output_root.exists():
        return None

    has_existing = (
        (output_root / "catalog.jsonl").exists()
        or (output_root / "catalog.csv").exists()
        or (output_root / "items").exists()
    )
    if not has_existing:
        return None

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = output_root.parent / f"{output_root.name}-backups"
    backup_path = backup_root / timestamp
    counter = 1

    while backup_path.exists():
        backup_path = backup_root / f"{timestamp}-{counter}"
        counter += 1

    backup_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(output_root, backup_path)
    return backup_path

def main() -> None:
    args = parse_args()

    ensure_local_server(args.server)

    if args.max_transcript_chars < 4000:
        raise RuntimeError("--max-transcript-chars must be at least 4000")
    if args.limit is not None and args.limit < 1:
        raise RuntimeError("--limit must be at least 1")

    model = resolve_model(args.server, args.model)

    selected_sources = (
        list(SOURCE_DIRS)
        if args.source == "all"
        else [args.source]
    )

    backup_path: Path | None = None
    if args.force:
        backup_path = backup_existing_output(args.output)

    pending: list[tuple[str, dict[str, str], Path, Path]] = []

    for source in selected_sources:
        transcript_dir = args.whisper_root / SOURCE_DIRS[source]
        manifest = transcript_dir / "manifest.tsv"
        review_manifest = transcript_dir / "review.tsv"
        rows = load_manifest(manifest)
        review_metadata = load_review_metadata(review_manifest)

        for row in rows:
            source_meta = review_metadata.get(row["source_audio"], {})
            row["duration_seconds"] = source_meta.get("duration_seconds", "")
            if not row.get("audio_bytes"):
                row["audio_bytes"] = source_meta.get("audio_bytes", "")
            record_id = stable_record_id(
                source,
                row["source_audio"],
                row.get("audio_sha256", ""),
            )
            item_path = migrate_legacy_item_identity(
                args.output,
                source,
                row,
                record_id,
            )
            if item_path.exists():
                refresh_existing_identity_metadata(item_path, row)

            if args.only_review:
                if not item_path.exists() or not existing_review_flag(item_path):
                    continue
            elif item_path.exists() and not args.force:
                existing_model = existing_descriptor_model(item_path)
                if existing_model == model:
                    continue
                raise RuntimeError(
                    f"Refusing to mix models in {args.output}: "
                    f"{item_path.name} was created by {existing_model!r}, "
                    f"requested model is {model!r}. Use a different --output "
                    "or --force deliberately."
                )

            pending.append(
                (source, row, transcript_dir, item_path)
            )

    if args.limit is not None:
        pending = pending[: args.limit]

    print("Voice Memo descriptor pass")
    print("==========================")
    print(f"LM Studio:   {args.server}")
    print(f"Model:       {model}")
    print(f"Sources:     {', '.join(selected_sources)}")
    print(f"Pending:     {len(pending)}")
    print(f"Output:      {args.output}")
    if backup_path is not None:
        print(f"Backup:      {backup_path}")
    print()
    print("This stage reads transcripts/metadata only; it never opens original audio.")
    print()

    failures: list[tuple[str, str]] = []

    for index, (source, row, transcript_dir, _) in enumerate(pending, start=1):
        title = row["output_stem"]
        print(f"[{index}/{len(pending)}] {source}: {title}", flush=True)

        try:
            record = describe_one(
                source=source,
                row=row,
                transcript_dir=transcript_dir,
                output_root=args.output,
                server=args.server,
                model=model,
                max_transcript_chars=args.max_transcript_chars,
            )
            print(
                "  "
                f"{record['freeform_type']} | "
                f"{record['purpose_guess']} | "
                f"confidence={float(record['confidence']):.2f}"
                + (
                    " | AUDIO REVIEW"
                    if record["needs_audio_analysis"]
                    else ""
                ),
                flush=True,
            )
        except KeyboardInterrupt:
            print(
                "\nInterrupted. Completed descriptor files are preserved; "
                "rerun the same command to resume.",
                file=sys.stderr,
            )
            write_aggregate_catalogs(args.output)
            return
        except Exception as exc:
            failures.append((row["source_audio"], str(exc)))
            print(f"  FAILED: {exc}", file=sys.stderr, flush=True)

    write_aggregate_catalogs(args.output)

    print()
    print("Descriptor pass complete.")
    print(f"Catalog JSONL: {args.output / 'catalog.jsonl'}")
    print(f"Catalog CSV:   {args.output / 'catalog.csv'}")
    print(f"Review queue:  {args.output / 'review.csv'}")

    failed_path = args.output / "failed.tsv"

    if failures:
        with failed_path.open("w", encoding="utf-8") as handle:
            handle.write("source_audio\tproblem\n")
            for source_audio, problem in failures:
                clean_problem = problem.replace("\t", " ").replace("\n", " ")
                handle.write(f"{source_audio}\t{clean_problem}\n")
        print(f"Failures:      {failed_path} ({len(failures)})")
        raise RuntimeError(
            f"{len(failures)} recording(s) failed descriptor generation."
        )

    if failed_path.exists():
        failed_path.unlink()
        print("Failures:      none (removed stale failed.tsv)")


if __name__ == "__main__":
    main()
