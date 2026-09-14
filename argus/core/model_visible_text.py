"""Keep opaque machine identifiers out of model-facing semantic judgment.

Checksums and content digests are useful to host code for cache keys, atomic
identity, corruption detection, and deduplication.  Their values are not useful
semantic evidence for an LLM: comparing two opaque strings cannot establish
correctness, freshness, provenance, or task completion.

This module therefore owns the boundary between machine-only integrity metadata
and text shown to or produced by a role agent.
"""

from __future__ import annotations

import re

MODEL_INTEGRITY_BOUNDARY = """## Opaque integrity IDs
Checksums, digests, fingerprints, and commit IDs are host-only. Never inspect,
quote, compare, or use their values as evidence. Differences cannot prove
freshness, correctness, provenance, completion, contradiction, or justify
`continue`, `blocked`, or `replan_requested`. Use content, timestamps, tests,
metrics, and readable provenance; ignore lower-level identifier adjudication.
"""

_HEX_VALUE = r"[0-9a-f]{7,128}"
_PREFIXED_DIGEST_RE = re.compile(rf"(?i)\b(?:sha(?:-?1|-?256|-?512)?|md5):{_HEX_VALUE}\b")
_LABELED_IDENTIFIER_RE = re.compile(
    rf"""(?ix)
    \b[a-z0-9_.-]*
    (?:sha(?:-?256)?|hash|checksum|digest|fingerprint|commit(?:[_ -]?id)?|revision)
    [a-z0-9_.-]*\b
    \s*(?::|=|\bis\b)?\s*
    [`\"']?(?:sha(?:-?256)?:)?{_HEX_VALUE}[`\"']?
    """
)
_BARE_LONG_HEX_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32,128}(?![0-9a-f])")
_INTEGRITY_TERM_RE = re.compile(
    r"(?i)\b(?:sha(?:-?256)?|hash(?:es|ed|ing)?|checksum|digest|fingerprint)\b"
    r"|哈希|校验和|摘要值"
)
_INTEGRITY_JUDGMENT_RE = re.compile(
    r"(?i)\b(?:match(?:es|ed|ing)?|mismatch(?:es|ed)?|stale|fresh|same|different|"
    r"changed?|drift(?:ed|ing)?|contradict(?:s|ed|ory)?|invalid|missing|verify|"
    r"verified|passes?|passed|fails?|failed|refresh(?:ed)?)\b"
    r"|一致|不一致|陈旧|过期|匹配|不同|变化|漂移|校验|验证|冲突|失效"
)
_MATERIAL_BLOCKER_RE = re.compile(
    r"(?i)\b(?:incomplete|missing|required|must|need(?:s|ed)?|fix|repair|reject|"
    r"fail(?:s|ed|ure)?|wrong|unsatisfied|unresolved|cannot|can't|blocker|"
    r"problem|issue|gap|lacks?|unable)\b"
    r"|\b(?:could\s+not|was\s+not\s+able)\b"
    r"|not\s+(?:done|complete|completed|satisfied|verified)"
    r"|does\s+not\s+(?:meet|pass)"
    r"|未完成|缺失|必须|需要|修复|失败|未满足|阻塞|问题"
)


def sanitize_model_visible_text(value: object) -> str:
    """Redact opaque integrity values before text reaches a role model."""
    text = str(value or "")
    text = _LABELED_IDENTIFIER_RE.sub("<machine-integrity-metadata omitted>", text)
    text = _PREFIXED_DIGEST_RE.sub("<machine-integrity-metadata omitted>", text)
    return _BARE_LONG_HEX_RE.sub("<machine-integrity-metadata omitted>", text)


def contains_integrity_judgment(value: object) -> bool:
    """Return whether text asks a semantic verdict from opaque identifiers."""
    text = str(value or "")
    return bool(_INTEGRITY_TERM_RE.search(text) and _INTEGRITY_JUDGMENT_RE.search(text))


def sanitize_model_judgment_text(value: object) -> str:
    """Remove identifier-based verdict clauses and redact remaining values."""
    text = str(value or "").strip()
    if not text:
        return ""
    units = re.split(r"(?<=[.!?。！？;；])\s+|\n+", text)
    kept: list[str] = []
    for unit in units:
        cleaned = unit.strip()
        if not cleaned:
            continue
        if _INTEGRITY_TERM_RE.search(cleaned) and _INTEGRITY_JUDGMENT_RE.search(cleaned):
            continue
        kept.append(sanitize_model_visible_text(cleaned))
    return " ".join(kept).strip()


def has_material_blocker(value: object) -> bool:
    """Return whether sanitized prose still names a non-integrity blocker."""
    return bool(_MATERIAL_BLOCKER_RE.search(str(value or "")))


__all__ = [
    "MODEL_INTEGRITY_BOUNDARY",
    "contains_integrity_judgment",
    "has_material_blocker",
    "sanitize_model_judgment_text",
    "sanitize_model_visible_text",
]
