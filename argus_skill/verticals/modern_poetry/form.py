"""modern_poetry FORM layer — the honest, thin, deterministic checks.

Free verse has no 平仄/韵 to check, so this does NOT pretend to judge poetic
quality. It checks only the DECLARED hard constraints in a ``form_spec``:

* **language** — the poem is predominantly in the declared script (zh Han / en Latin);
* **line_count / min_lines / max_lines** — if the brief pins a count, the poem must meet it;
* **banned_words** — a CURATED cliché blocklist; a listed 陈词 present is a finding
  (this catches ONLY listed items — it is not a general cliché detector);
* **non_empty** — no blank required lines.

Everything about imagery, lineation, tone, and cliché beyond the list is a
live-reviewer judgement and lives in the review rubric, not here. Returns
structured findings so the runtime gate and the review contract can consume them.
"""
from __future__ import annotations

import re
from typing import Any

#: The machine-decidable modern-poetry finding types (hard constraints only).
FORM_FINDING_TYPES: frozenset[str] = frozenset({
    "language", "line_count", "banned_word", "empty_line",
})


class FormError(ValueError):
    """Raised when a form_spec is malformed."""


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _han_ratio(s: str) -> float:
    letters = [c for c in s if c.strip() and not c.isspace()]
    if not letters:
        return 0.0
    han = sum(1 for c in s if "一" <= c <= "鿿")
    return han / max(1, len([c for c in s if not c.isspace()]))


def _finding(ftype: str, detail: str, line: int | None = None) -> dict[str, Any]:
    return {"type": ftype, "severity": "blocking", "line": line, "detail": detail}


def check_form(text: str, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return blocking findings where ``text`` violates the declared ``spec``.

    ``spec`` keys (all optional): ``language`` ('zh'|'en'), ``line_count`` (int),
    ``min_lines``, ``max_lines`` (int), ``banned_words`` (list[str]). An empty/None
    spec means only the always-on non-empty check applies.
    """
    spec = spec or {}
    if not isinstance(spec, dict):
        raise FormError("form_spec must be an object")
    findings: list[dict[str, Any]] = []
    raw_lines = text.splitlines()
    lines = _lines(text)

    if not lines:
        findings.append(_finding("empty_line", "poem is empty (no non-blank lines)"))
        return findings

    # an interior blank line inside the body when a fixed line_count is declared
    if spec.get("line_count") is not None:
        want = int(spec["line_count"])
        if len(lines) != want:
            findings.append(_finding(
                "line_count", f"declared {want} lines but poem has {len(lines)}"))
    if spec.get("min_lines") is not None and len(lines) < int(spec["min_lines"]):
        findings.append(_finding(
            "line_count", f"poem has {len(lines)} lines, below min {spec['min_lines']}"))
    if spec.get("max_lines") is not None and len(lines) > int(spec["max_lines"]):
        findings.append(_finding(
            "line_count", f"poem has {len(lines)} lines, above max {spec['max_lines']}"))

    lang = spec.get("language")
    if lang == "zh" and _han_ratio(text) < 0.5:
        findings.append(_finding("language", "declared zh but not predominantly Han script"))
    if lang == "en" and _han_ratio(text) > 0.2:
        findings.append(_finding("language", "declared en but contains substantial Han script"))

    banned = [w for w in (spec.get("banned_words") or []) if w]
    for i, ln in enumerate(lines, 1):
        for w in banned:
            if w in ln:
                findings.append(_finding("banned_word", f"banned cliché {w!r} present", i))
    return findings


def is_compliant(text: str, spec: dict[str, Any] | None = None) -> bool:
    return not check_form(text, spec)


__all__ = ["FORM_FINDING_TYPES", "FormError", "check_form", "is_compliant"]
