"""The ``FINDINGS.json`` deliverable contract shared by red and blue team.

``FINDINGS.json`` at the project root is the offense deliverable and the
defense input: every finding records ``id``, ``target``, ``severity``
(``P0``..``P4``), ``title``, ``evidence`` (project-relative artifact paths
with their content hashes), and ``reproduction`` (the command that
demonstrates it). An external scanner export is mapped into this same shape
before a blue-team mission consumes it, so there is exactly one schema to
validate on both sides of the loop.

An empty ``findings`` list is a valid, honest null result. A findings document
whose evidence hashes no longer match its files is not.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .exploit_evidence import finding_evidence_issues

FINDINGS_FILE = "FINDINGS.json"
SEVERITIES: tuple[str, ...] = ("P0", "P1", "P2", "P3", "P4")


def severity_rank(severity: object) -> int:
    """P0 is rank 0 (most severe); an unknown severity sorts last, not first."""
    try:
        return SEVERITIES.index(str(severity))
    except ValueError:
        return len(SEVERITIES)


def finding_issues(
    finding: object, project_root: Path, *, label: str
) -> tuple[str, ...]:
    """Full per-finding validation: schema fields, severity, evidence binding."""
    if not isinstance(finding, dict):
        return (f"{label} must be an object",)
    issues: list[str] = []
    for key in ("id", "target", "title", "reproduction"):
        value = finding.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{label}: {key} must be a non-empty string")
    if finding.get("severity") not in SEVERITIES:
        issues.append(
            f"{label}: severity {finding.get('severity')!r} must be one of "
            f"{', '.join(SEVERITIES)}"
        )
    issues.extend(finding_evidence_issues(finding, project_root, label=label))
    return tuple(issues)


def findings_issues(project_root: Path | str) -> tuple[str, ...]:
    """Validate ``FINDINGS.json`` and every finding in it; empty tuple = valid."""
    root = Path(str(project_root))
    path = root / FINDINGS_FILE
    if not path.is_file():
        return (
            f"{FINDINGS_FILE} is missing at the project root: the mission's "
            "findings deliverable (findings with evidence-bound reproductions) "
            "cannot be shipped or consumed without it",
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return (f"{FINDINGS_FILE} is unreadable or not valid JSON: {exc}",)
    if not isinstance(document, dict):
        return (f"{FINDINGS_FILE} must be a JSON object with a findings list",)
    findings = document.get("findings")
    if not isinstance(findings, list):
        return (f"{FINDINGS_FILE} needs a 'findings' list",)
    issues: list[str] = []
    seen_ids: set[str] = set()
    for index, finding in enumerate(findings):
        raw_id = finding.get("id") if isinstance(finding, dict) else None
        label = f"finding {raw_id.strip()!r}" if isinstance(raw_id, str) and raw_id.strip() else f"finding #{index + 1}"
        if isinstance(raw_id, str) and raw_id.strip():
            if raw_id.strip() in seen_ids:
                issues.append(f"{label}: duplicate finding id")
            seen_ids.add(raw_id.strip())
        issues.extend(finding_issues(finding, root, label=label))
    return tuple(issues)


def read_findings(project_root: Path | str) -> Any:
    """Load ``FINDINGS.json`` and return its ``findings`` list, or ``None``.

    Raises on a missing, unreadable, or structurally invalid document — callers
    that need the tolerant view use :func:`findings_issues` instead and act on
    the issues.
    """
    path = Path(str(project_root)) / FINDINGS_FILE
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("findings"), list):
        raise ValueError(f"{FINDINGS_FILE} must be an object with a findings list")
    return document["findings"]


__all__ = [
    "FINDINGS_FILE",
    "SEVERITIES",
    "findings_issues",
    "finding_issues",
    "read_findings",
    "severity_rank",
]
