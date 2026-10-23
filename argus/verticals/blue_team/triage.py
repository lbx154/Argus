"""Defense triage: consume ``FINDINGS.json``, rank it, and drop what cannot reproduce.

Triage is the deterministic funnel between offense (or an external scanner
export mapped into the ``FINDINGS.json`` shape) and remediation: duplicates
collapse, findings rank by severity, and anything without a locally
reproducible finding record — missing PoC, missing raw output, broken hash
binding, missing reproduction command — is dropped to ``unverified`` and
never enters ``remediate``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..red_team.findings import finding_issues, read_findings, severity_rank


def _duplicate_key(finding: object) -> tuple[str, str]:
    if not isinstance(finding, dict):
        return ("", "")
    target = finding.get("target")
    title = finding.get("title")
    return (
        target.strip().casefold() if isinstance(target, str) else "",
        title.strip().casefold() if isinstance(title, str) else "",
    )


@dataclass(frozen=True)
class TriageReport:
    """The actionable queue, the unverified drops, and the collapsed duplicates."""

    actionable: tuple[dict[str, Any], ...]
    unverified: tuple[tuple[dict[str, Any], str], ...]
    dropped_duplicates: tuple[dict[str, Any], ...]


def triage_findings(findings: list[Any], project_root: Path | str) -> TriageReport:
    """Dedupe, severity-rank, and partition findings into actionable/unverified.

    A finding is actionable only when its full record validates (schema,
    severity, evidence files with matching hashes, reproduction command) — the
    same validator the red-team report gate uses, so the two sides of the loop
    share one definition of "reproducible". Duplicates collapse by finding id
    first (first occurrence wins), then by identical (target, title) keeping
    the more severe copy; survivors are ordered by severity rank with original
    order as the stable tiebreak.
    """
    root = Path(str(project_root))
    dropped: list[dict[str, Any]] = []

    # 1. Collapse duplicate finding ids; the first occurrence wins.
    seen_ids: set[str] = set()
    unique: list[dict[str, Any]] = []
    for finding in findings:
        fid = finding.get("id") if isinstance(finding, dict) else None
        key = fid.strip() if isinstance(fid, str) and fid.strip() else ""
        if key and key in seen_ids:
            dropped.append(finding)  # type: ignore[arg-type]
            continue
        if key:
            seen_ids.add(key)
        unique.append(finding)  # type: ignore[arg-type]

    # 2. Collapse findings naming the same target and title; keep the most severe.
    survivors: list[dict[str, Any]] = []
    for finding in unique:
        key = _duplicate_key(finding)
        incumbent_index = next(
            (
                index
                for index, other in enumerate(survivors)
                if key != ("", "") and _duplicate_key(other) == key
            ),
            None,
        )
        if incumbent_index is None:
            survivors.append(finding)
        elif severity_rank(finding.get("severity")) < severity_rank(
            survivors[incumbent_index].get("severity")
        ):
            dropped.append(survivors[incumbent_index])
            survivors[incumbent_index] = finding
        else:
            dropped.append(finding)

    # 3. Partition: only a fully valid, reproducible record is actionable.
    actionable: list[dict[str, Any]] = []
    unverified: list[tuple[dict[str, Any], str]] = []
    for finding in survivors:
        issues = finding_issues(finding, root, label="finding")
        if issues:
            unverified.append((finding, issues[0]))
        else:
            actionable.append(finding)
    actionable.sort(key=lambda finding: severity_rank(finding.get("severity")))
    return TriageReport(
        actionable=tuple(actionable),
        unverified=tuple(unverified),
        dropped_duplicates=tuple(dropped),
    )


def triage_project(project_root: Path | str) -> TriageReport:
    """Triage the project root's ``FINDINGS.json`` in one step."""
    return triage_findings(read_findings(project_root), project_root)


__all__ = ["TriageReport", "triage_findings", "triage_project"]
