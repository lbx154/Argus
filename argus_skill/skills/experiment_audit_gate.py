"""Experiment-audit artifact structural gate.

Pairs with the ARIS-derived ``reviewer/experiment-audit.md`` skill. The
skill produces ``paper/EXPERIMENT_AUDIT.md`` and ``.json``; this gate
enforces that they exist, are machine-readable, and cover every
checkpoint at the analysis / review / submission stages.

The gate is structural / anti-fab: it does NOT score WHAT the audit
verdict is (the reviewer is the only authority on integrity calls). It
only enforces the audit was produced, was structured, and covered each
required check. A hand-edited "PASS" file with no `auditor` field or
missing checks counts as malformed.

The five required check keys mirror the skill's checklist sections:
``gt_provenance``, ``score_normalization``, ``result_existence``,
``dead_code``, ``scope``. ``eval_type`` is required as a free-form
classification.

CLI:
    python -m argus_skill.skills.experiment_audit_gate --project-root .
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

REPORT_BASENAME = "EXPERIMENT_AUDIT"
REQUIRED_CHECK_KEYS = (
    "gt_provenance",
    "score_normalization",
    "result_existence",
    "dead_code",
    "scope",
)
ALLOWED_STATUSES = frozenset({"pass", "warn", "fail"})
ALLOWED_INTEGRITY_STATUSES = frozenset({"pass", "warn", "fail"})


@dataclass
class AuditIssue:
    code: str
    detail: str


@dataclass
class AuditReport:
    md_path: Path | None
    json_path: Path | None
    integrity_status: str | None = None
    checks_present: list[str] = field(default_factory=list)
    issues: list[AuditIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_text(self) -> str:
        lines = []
        if not self.ok:
            lines.append(
                f"{len(self.issues)} experiment-audit contract violation(s); "
                f"draft cannot advance until the audit artifact is regenerated:"
            )
            for issue in self.issues:
                lines.append(f"  [{issue.code}] {issue.detail}")
            lines.append("")
        lines.append("Audit artifact status:")
        lines.append(f"  EXPERIMENT_AUDIT.md present: {self.md_path is not None}")
        lines.append(f"  EXPERIMENT_AUDIT.json present: {self.json_path is not None}")
        lines.append(f"  integrity_status: {self.integrity_status or '<missing>'}")
        if self.checks_present:
            lines.append(f"  checks covered: {sorted(self.checks_present)}")
        return "\n".join(lines)


def _find_artifacts(project_root: Path) -> tuple[Path | None, Path | None]:
    paper = project_root / "paper"
    md = paper / f"{REPORT_BASENAME}.md"
    js = paper / f"{REPORT_BASENAME}.json"
    return (md if md.exists() else None), (js if js.exists() else None)


def validate_experiment_audit(project_root: Path) -> AuditReport:
    md_path, json_path = _find_artifacts(project_root)
    report = AuditReport(md_path=md_path, json_path=json_path)

    if md_path is None:
        report.issues.append(AuditIssue(
            code="missing_experiment_audit_md",
            detail=(
                f"paper/{REPORT_BASENAME}.md not found — run the "
                "reviewer/experiment-audit skill to produce a structured "
                "integrity audit before claiming results"
            ),
        ))
    if json_path is None:
        report.issues.append(AuditIssue(
            code="missing_experiment_audit_json",
            detail=(
                f"paper/{REPORT_BASENAME}.json not found — the JSON file is "
                "the gate's machine-readable surface; the .md alone is not "
                "enough"
            ),
        ))
        return report

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.issues.append(AuditIssue(
            code="malformed_experiment_audit_json",
            detail=f"{json_path.name} is not valid JSON: {exc}",
        ))
        return report

    if not isinstance(data, dict):
        report.issues.append(AuditIssue(
            code="malformed_experiment_audit_json",
            detail=f"{json_path.name} top level must be a JSON object",
        ))
        return report

    auditor = str(data.get("auditor") or "").strip()
    if not auditor:
        report.issues.append(AuditIssue(
            code="missing_auditor_field",
            detail=(
                "'auditor' field is empty — every audit must record which "
                "reviewer route produced it (so a hand-edited PASS can be "
                "traced back to a real call)"
            ),
        ))

    integrity = str(data.get("integrity_status") or "").strip().lower()
    report.integrity_status = integrity or None
    if integrity not in ALLOWED_INTEGRITY_STATUSES:
        report.issues.append(AuditIssue(
            code="invalid_integrity_status",
            detail=(
                f"integrity_status={integrity!r} not in "
                f"{sorted(ALLOWED_INTEGRITY_STATUSES)}"
            ),
        ))

    checks = data.get("checks")
    if not isinstance(checks, dict):
        report.issues.append(AuditIssue(
            code="missing_checks_object",
            detail="'checks' must be a JSON object with per-check entries",
        ))
        return report

    for key in REQUIRED_CHECK_KEYS:
        entry = checks.get(key)
        if entry is None:
            report.issues.append(AuditIssue(
                code="missing_check",
                detail=(
                    f"required check {key!r} is missing from checks{{}} — "
                    "the audit must cover all five integrity dimensions"
                ),
            ))
            continue
        if not isinstance(entry, dict):
            report.issues.append(AuditIssue(
                code="malformed_check_entry",
                detail=f"checks[{key!r}] must be an object with 'status' + 'details'",
            ))
            continue
        status = str(entry.get("status") or "").strip().lower()
        if status not in ALLOWED_STATUSES:
            report.issues.append(AuditIssue(
                code="invalid_check_status",
                detail=(
                    f"checks[{key!r}].status={status!r} not in "
                    f"{sorted(ALLOWED_STATUSES)}"
                ),
            ))
            continue
        report.checks_present.append(key)

    if "eval_type" not in checks:
        report.issues.append(AuditIssue(
            code="missing_eval_type",
            detail=(
                "'eval_type' missing from checks{} — classify the evaluation "
                "as real_gt / synthetic_proxy / self_supervised_proxy / "
                "simulation_only / human_eval"
            ),
        ))

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_experiment_audit(args.project_root.resolve())
    if args.json:
        payload = {
            "ok": report.ok,
            "md_path": str(report.md_path) if report.md_path else None,
            "json_path": str(report.json_path) if report.json_path else None,
            "integrity_status": report.integrity_status,
            "checks_present": report.checks_present,
            "issues": [
                {"code": i.code, "detail": i.detail} for i in report.issues
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_text())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
