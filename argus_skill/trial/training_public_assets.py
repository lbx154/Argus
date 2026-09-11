"""Recognize shipped public Skill paths and verify their actual read results.

Only this installed package's built-in inventory is authoritative. Tenant files,
project extensions, plugin registries and model logs are never read here.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

ROOT = "/tenant/home/.argus-skill/skills"
_PATH = re.compile(r"(?<![\w/.\\%+-])/tenant/home/\.argus-skill/skills/[^\s\"'`<>()[\]{};,|]+")
_PACKAGE = Path(__file__).resolve().parents[1]


def _sources(path):
    from ..skills.builtins import _VERTICAL_SKILL_INHERITANCE
    from ..verticals import builtin_verticals

    if path == ROOT:
        return [(_PACKAGE / "builtin_skills", "")]
    if not isinstance(path, str) or not path.startswith(ROOT + "/") or len(path) > 1024:
        return []
    relative = path[len(ROOT) + 1:]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", relative):
        return []
    parts = relative.split("/")
    if any(part in {".", ".."} or part.startswith(".") for part in parts):
        return []
    if parts[0] == "_shared_verticals":
        if len(parts) < 2 or parts[1] not in builtin_verticals():
            return []
        vertical = parts[1]
        name = "/".join(parts[2:])
        return [(_PACKAGE / "verticals" / source / "skills", name)
                for source in (*_VERTICAL_SKILL_INHERITANCE.get(vertical, ()), vertical)]
    return [(_PACKAGE / "builtin_skills", relative)]


@lru_cache(maxsize=256)
def public_skill_body(path):
    """Return a small, exact published Markdown body, never a tenant copy."""
    for root, name in _sources(path):
        if not name.endswith(".md"):
            continue
        candidate = root / name
        if (candidate.is_file() and not candidate.is_symlink()
                and candidate.resolve().is_relative_to(root.resolve()) and candidate.stat().st_size <= 64 * 1024):
            return candidate.read_text(encoding="utf-8")
    return None


def public_skill_literal(path):
    """A published library directory or exact shipped file, never a wildcard."""
    for root, name in _sources(path):
        candidate = root / name
        if (candidate.is_dir() and not candidate.is_symlink()
                and candidate.resolve().is_relative_to(root.resolve())):
            return True
    return public_skill_body(path) is not None


def _mentioned_assets(value):
    if isinstance(value, str):
        return {match[0] for match in _PATH.finditer(value) if public_skill_body(match[0]) is not None}
    if isinstance(value, dict):
        return set().union(*(_mentioned_assets(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_mentioned_assets(item) for item in value))
    return set()


def check_public_skill_event(kind, payload):
    """Public-library bodies are accepted only from Pi read with exact bytes.

    This follows pinned Pi's plain-text offset/limit rendering. No output is
    rewritten. Unrecognized selections, tool methods or modified copies fail.
    """
    if kind not in {"tool_call", "tool_result"} or not isinstance(payload, dict):
        return
    arguments = payload.get("input")
    assets = _mentioned_assets(arguments)
    if not assets:
        return
    path = arguments.get("path") if isinstance(arguments, dict) else None
    body = public_skill_body(path) if isinstance(path, str) else None
    if payload.get("toolName") != "read" or body is None or assets != {path}:
        raise ValueError("unverified_public_skill_content")
    if any(value is not None and key not in {"path", "offset", "limit"} for key, value in arguments.items()):
        raise ValueError("unverified_public_skill_content")
    offset, limit = arguments.get("offset"), arguments.get("limit")
    if any(value is not None and (type(value) is not int or value < 1) for value in (offset, limit)):
        raise ValueError("unverified_public_skill_content")
    lines = body.split("\n")
    start = (offset or 1) - 1
    if start >= len(lines):
        raise ValueError("unverified_public_skill_content")
    end = min(start + limit, len(lines)) if limit is not None else len(lines)
    expected = "\n".join(lines[start:end])
    if end < len(lines):
        expected += f"\n\n[{len(lines) - end} more lines in file. Use offset={end + 1} to continue.]"
    if kind == "tool_result":
        content = payload.get("content")
        if (payload.get("isError") is not False or payload.get("output_complete") is not True
                or not isinstance(content, list) or len(content) != 1
                or not isinstance(content[0], dict)
                or set(content[0]) != {"type", "text"} or content[0].get("type") != "text"
                or not isinstance(content[0].get("text"), str)
                or hashlib.sha256(content[0]["text"].encode()).digest() != hashlib.sha256(expected.encode()).digest()):
            raise ValueError("unverified_public_skill_content")
