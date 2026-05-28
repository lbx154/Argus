from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BtwAttachment:
    path: str
    reason: str


@dataclass(frozen=True)
class BtwSkillResult:
    is_file_request: bool
    attachments: list[BtwAttachment]
    summary_lines: list[str]


@dataclass(frozen=True)
class BtwFileReturnSkillConfig:
    name: str
    max_attachments: int
    skip_dir_names: set[str]
    image_extensions: set[str]
    textual_file_extensions: set[str]
    image_request_keywords: list[str]
    file_request_keywords: list[str]
    preferred_image_hints: list[str]
    preferred_dir_hints: list[str]


def resolve_btw_skill_result(*, working_dir: str, question: str, max_attachments: int = 4) -> BtwSkillResult:
    config = load_btw_file_return_skill_config()
    normalized = (question or "").strip()
    lowered = normalized.lower()
    explicit_names = _extract_explicit_file_names(normalized)
    image_request = any(keyword in lowered for keyword in config.image_request_keywords) or "图" in normalized
    file_request = image_request or bool(explicit_names) or (
        any(keyword in lowered for keyword in config.file_request_keywords)
        and _has_file_intent_context(normalized)
    )
    if not file_request:
        return BtwSkillResult(is_file_request=False, attachments=[], summary_lines=[])

    attachments = _collect_candidates(
        config=config,
        root=Path(working_dir),
        explicit_names=explicit_names,
        image_request=image_request,
        max_attachments=min(max_attachments, config.max_attachments),
    )
    summary_lines = [f"- {Path(item.path).name}: {item.reason}" for item in attachments]
    return BtwSkillResult(
        is_file_request=True,
        attachments=attachments,
        summary_lines=summary_lines,
    )


def load_btw_file_return_skill_config() -> BtwFileReturnSkillConfig:
    path = Path(__file__).resolve().parents[1] / "skills" / "btw-file-return" / "config.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BtwFileReturnSkillConfig(
        name=str(payload.get("name", "btw-file-return")),
        max_attachments=int(payload.get("maxAttachments", 4)),
        skip_dir_names={str(item) for item in payload.get("skipDirNames", [])},
        image_extensions={str(item).lower() for item in payload.get("imageExtensions", [])},
        textual_file_extensions={str(item).lower() for item in payload.get("textualFileExtensions", [])},
        image_request_keywords=[str(item).lower() for item in payload.get("imageRequestKeywords", [])],
        file_request_keywords=[str(item).lower() for item in payload.get("fileRequestKeywords", [])],
        preferred_image_hints=[str(item).lower() for item in payload.get("preferredImageHints", [])],
        preferred_dir_hints=[str(item).lower() for item in payload.get("preferredDirHints", [])],
    )


def _collect_candidates(
    *,
    config: BtwFileReturnSkillConfig,
    root: Path,
    explicit_names: list[str],
    image_request: bool,
    max_attachments: int,
) -> list[BtwAttachment]:
    if not root.exists():
        return []
    candidates: list[tuple[int, float, Path, str]] = []
    explicit_lower = [item.lower() for item in explicit_names]
    for path in _iter_files(root, skip_dir_names=config.skip_dir_names):
        suffix = path.suffix.lower()
        if image_request and suffix not in config.image_extensions:
            continue
        if not image_request and explicit_lower:
            rel = str(path.relative_to(root)).lower()
            if path.name.lower() not in explicit_lower and rel not in explicit_lower:
                continue
        elif (
            not image_request
            and suffix not in config.textual_file_extensions
            and suffix not in config.image_extensions
        ):
            continue
        score, reason = _score_candidate(
            config=config,
            root=root,
            path=path,
            explicit_names=explicit_lower,
            image_request=image_request,
        )
        if score <= 0:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        candidates.append((score, mtime, path, reason))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

    output: list[BtwAttachment] = []
    seen: set[str] = set()
    for _, _, path, reason in candidates[: max_attachments * 3]:
        full = str(path.resolve())
        if full in seen:
            continue
        seen.add(full)
        output.append(BtwAttachment(path=full, reason=reason))
        if len(output) >= max_attachments:
            break
    return output


def _score_candidate(
    *,
    config: BtwFileReturnSkillConfig,
    root: Path,
    path: Path,
    explicit_names: list[str],
    image_request: bool,
) -> tuple[int, str]:
    score = 0
    reasons: list[str] = []
    name_lower = path.name.lower()
    rel_lower = str(path.relative_to(root)).lower()
    parent_lower = str(path.parent.relative_to(root)).lower() if path.parent != root else ""

    if explicit_names:
        for item in explicit_names:
            if item == name_lower or item == rel_lower:
                score += 100
                reasons.append("explicit filename match")
            elif item in name_lower or item in rel_lower:
                score += 70
                reasons.append("partial filename match")
    if image_request:
        if path.suffix.lower() in config.image_extensions:
            score += 30
            reasons.append("image extension")
        for hint in config.preferred_image_hints:
            if hint in name_lower:
                score += 20
                reasons.append(f"name contains {hint}")
                break
        for hint in config.preferred_dir_hints:
            if hint in parent_lower:
                score += 15
                reasons.append(f"directory contains {hint}")
                break
    else:
        if path.suffix.lower() in config.textual_file_extensions:
            score += 20
            reasons.append("document/code extension")
        if name_lower in {"readme.md", "readme.txt"} and any("readme" in n for n in explicit_names):
            score += 50
            reasons.append("README priority")

    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > 0:
        score += 1
    return score, ", ".join(reasons) if reasons else "generic candidate"


def _iter_files(root: Path, *, skip_dir_names: set[str]):
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [item for item in dirnames if item not in skip_dir_names]
        current_path = Path(current_root)
        for name in filenames:
            yield current_path / name


def _has_file_intent_context(question: str) -> bool:
    """Return True when the question looks like a file request, not a general question that happens to contain a keyword."""
    lowered = question.lower()
    # Short queries with a file keyword are likely file requests
    if len(question.split()) <= 4:
        return True
    # Patterns that strongly suggest "give me a file"
    intent_patterns = [
        r"发我\s*\S+",
        r"给我\s*\S+",
        r"send\s+(me\s+)?the\s+",
        r"send\s+(me\s+)?\S+\.\S+",
        r"上传\s*\S+",
        r"把\s*\S+.*发",
        r"file\s+for\b",
        r"files?\s+named\b",
    ]
    for pat in intent_patterns:
        if re.search(pat, lowered):
            return True
    return False


def _extract_explicit_file_names(question: str) -> list[str]:
    matches = re.findall(r"([A-Za-z0-9_\-./\\]+\.[A-Za-z0-9]{1,8})", question)
    normalized: list[str] = []
    for item in matches:
        fixed = item.replace("\\", "/").strip()
        if fixed:
            normalized.append(fixed.lower())
            normalized.append(Path(fixed).name.lower())
    return list(dict.fromkeys(normalized))
