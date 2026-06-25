"""Shared lightweight helpers and constants for skill-generated review artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ACADEMIC_LANGUAGE_REVIEW_GENERATED_BY = "argus_skill.skills.academic_language_review"
ACADEMIC_LANGUAGE_REVIEW_HISTORY_PATH = Path("paper/ACADEMIC_LANGUAGE_REVIEW_history.jsonl")
PAPER_INFRASTRUCTURE_REVIEW_GENERATED_BY = "argus_skill.skills.paper_infrastructure_review"
PAPER_INFRASTRUCTURE_REVIEW_HISTORY_PATH = Path("paper/PAPER_INFRASTRUCTURE_REVIEW_history.jsonl")
LAYOUT_REVIEW_GENERATED_BY = "argus_skill.skills.paper_layout_review"
LAYOUT_REVIEW_HISTORY_PATH = Path("paper/LAYOUT_REVIEW_history.jsonl")
REVIEW_INPUT_SHA256_FIELD = "review_input_sha256"
REVIEW_PROMPT_SHA256_FIELD = "prompt_sha256"


def review_sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def review_sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return review_sha256_text(payload)


def review_sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
