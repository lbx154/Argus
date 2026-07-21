"""Theory Capability Gate (model stage) — deterministic ARTIFACT verifier.

Forces Argus to audit which theoretical capabilities apply to the task, which it
actually used, at what depth, and whether any missing capability undermines a
claim. The verifier ONLY inspects the agent-produced ``DOMAIN_CLASSIFICATION.json``
and ``THEORY_OPPORTUNITY_AUDIT.csv``; it never derives theory itself.

Advisory (does not block model->execute); failures feed the next-round repair
context. Applicable capabilities come from the CapabilityRegistry (families D/E).

Failure codes: TH-000 (artifact/schema), TH-001 (missing domain classification),
TH-002 (incomplete applicability audit / no domain-specific capability),
TH-003 (claimed capability lacks evidence file), TH-004 (applicable capability
left missing and unjustified), TH-005 (used capability lacks prior-work
comparison), TH-006 (missing required theory with no claim downgrade).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ....skills.capability_registry import CapabilityRegistry
from ....skills.capability_trace import trace_gate_run
from ....skills.research_gates import (
    clear_gate_state,
    is_truthy,
    read_csv_rows,
    update_gate_state,
    write_gate_outputs,
)

GATE_ID = "theory"
STAGE = "model"
ARTIFACT = "THEORY_OPPORTUNITY_AUDIT.csv"
DOMAIN_FILE = "DOMAIN_CLASSIFICATION.json"

REQUIRED_COLUMNS: tuple[str, ...] = (
    "capability_id", "capability_name", "generic_or_domain_specific", "domain",
    "is_applicable", "applicability_reason", "used_by_argus",
    "execution_level_basic_or_advanced_or_missing", "evidence_file", "evidence_location",
    "comparison_to_prior_work", "if_not_used_why", "impact_if_missing",
    "required_action", "claim_downgrade_if_missing",
)


def _fail(fid, sev, message, action, *, field="", blocks=False):
    return {"failure_id": fid, "severity": sev, "stage": STAGE, "artifact": ARTIFACT,
            "field": field, "message": message, "required_action": action,
            "blocks_progress": blocks}


def _c(row: dict, name: str) -> str:
    return str(row.get(name, "") or "").strip()


def verify_theory_capability(project_root: object) -> list[dict]:
    root = Path(str(project_root or "."))
    failures: list[dict] = []

    # (TH-001) domain classification present
    dom_path = root / DOMAIN_FILE
    primary = ""
    if dom_path.is_file() and dom_path.stat().st_size > 0:
        try:
            dom = json.loads(dom_path.read_text(encoding="utf-8"))
            primary = str(dom.get("primary_domain", "") or "").strip() if isinstance(dom, dict) else ""
        except ValueError:
            primary = ""
    if not primary:
        failures.append(_fail(
            "TH-001", "blocker",
            f"{DOMAIN_FILE} missing or has no primary_domain.",
            f"Write {DOMAIN_FILE} with primary_domain, secondary_domains, confidence, "
            "why_this_domain and domain_specific_capabilities_loaded.",
            field="primary_domain", blocks=True,
        ))

    header, rows = read_csv_rows(root / ARTIFACT)
    if not header:
        failures.append(_fail(
            "TH-000", "blocker",
            f"{ARTIFACT} is missing or empty; the model stage must audit theory capabilities.",
            f"Create {ARTIFACT} with the required columns, one row per candidate theory capability.",
            blocks=True,
        ))
        return failures
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing_cols:
        failures.append(_fail(
            "TH-000", "blocker",
            f"{ARTIFACT} is missing required columns: {', '.join(missing_cols)}.",
            "Add the missing columns and fill them per capability row.",
            field=",".join(missing_cols), blocks=True))
        return failures

    # (TH-002) applicability audit complete + at least one domain-specific capability
    incomplete = [_c(r, "capability_id") or f"row{i}" for i, r in enumerate(rows, 1)
                  if not (_c(r, "is_applicable") and _c(r, "applicability_reason"))]
    if incomplete:
        failures.append(_fail(
            "TH-002", "major",
            f"{len(incomplete)} capability row(s) lack is_applicable/applicability_reason "
            f"(e.g. {', '.join(incomplete[:5])}).",
            "For every candidate capability set is_applicable and give an applicability_reason.",
            field="is_applicable"))
    if not any("domain" in _c(r, "generic_or_domain_specific").lower()
               and is_truthy(_c(r, "is_applicable")) for r in rows):
        failures.append(_fail(
            "TH-002", "major",
            "no applicable DOMAIN-SPECIFIC theory capability is present in the audit.",
            "Classify the domain and load at least one applicable domain-specific theory "
            "capability (mark generic_or_domain_specific=domain-specific).",
            field="generic_or_domain_specific"))

    # per-row checks
    used_no_evidence, applicable_missing, used_no_cmp, missing_no_downgrade = [], [], [], []
    for i, r in enumerate(rows, 1):
        pid = _c(r, "capability_id") or f"row{i}"
        used = is_truthy(_c(r, "used_by_argus"))
        applicable = is_truthy(_c(r, "is_applicable"))
        missing = _c(r, "execution_level_basic_or_advanced_or_missing").lower() == "missing"
        impactful = bool(_c(r, "impact_if_missing"))
        if used and not _c(r, "evidence_file"):
            used_no_evidence.append(pid)
        if used and not _c(r, "comparison_to_prior_work"):
            used_no_cmp.append(pid)
        if applicable and missing and impactful and not _c(r, "if_not_used_why"):
            applicable_missing.append(pid)
        if applicable and missing and impactful and not _c(r, "claim_downgrade_if_missing"):
            missing_no_downgrade.append(pid)

    if used_no_evidence:
        failures.append(_fail(
            "TH-003", "major",
            f"{len(used_no_evidence)} capability row(s) claim used_by_argus but have no "
            f"evidence_file (e.g. {', '.join(used_no_evidence[:5])}).",
            "For each used capability point evidence_file at the derivation/notebook that shows it.",
            field="evidence_file"))
    if applicable_missing:
        failures.append(_fail(
            "TH-004", "major",
            f"{len(applicable_missing)} applicable, impactful theory capability(ies) are marked "
            f"missing with no justification (e.g. {', '.join(applicable_missing[:5])}).",
            "Either execute the capability or record if_not_used_why for each.",
            field="if_not_used_why"))
    if used_no_cmp:
        failures.append(_fail(
            "TH-005", "minor",
            f"{len(used_no_cmp)} used capability(ies) lack comparison_to_prior_work "
            f"(e.g. {', '.join(used_no_cmp[:5])}).",
            "State how the theoretical treatment compares to the closest prior work.",
            field="comparison_to_prior_work"))
    if missing_no_downgrade:
        failures.append(_fail(
            "TH-006", "major",
            f"{len(missing_no_downgrade)} claim(s) depend on a missing theory capability with no "
            f"claim_downgrade recorded (e.g. {', '.join(missing_no_downgrade[:5])}).",
            "Downgrade the dependent claim (partial/inconclusive) or fill claim_downgrade_if_missing.",
            field="claim_downgrade_if_missing"))
    return failures


def _render_review(root: Path, failures: list[dict], *, passed: bool) -> str:
    reg = CapabilityRegistry(project_root=root)
    caps = reg.for_gate(GATE_ID)
    lines = [
        "# Theory Capability gate review", "",
        f"Status: {'PASS' if passed else 'FAIL'}  |  failures: {len(failures)}  |  "
        f"artifacts: {DOMAIN_FILE}, {ARTIFACT}", "",
        "ADVISORY (does not block model->execute). A theory capability the task needs but "
        "Argus did not execute must either be executed or lead to a claim downgrade.", "",
        "## Applicable theory capabilities (from the CapabilityRegistry, families D/E)",
    ]
    for c in caps:
        lines.append(f"- {c.capability_id} [{c.source_layer}] {c.name} — {c.pass_threshold or c.basic_criteria}")
    if failures:
        lines += ["", "## Failures (see THEORY_GATE_REPAIR_TASKS.md)"]
        lines += [f"- {f['failure_id']} [{f['severity']}]: {f['message']}" for f in failures]
    return "\n".join(lines)


def run_gate(project_root: object) -> tuple[bool, list[dict]]:
    root = Path(str(project_root or "."))
    failures = verify_theory_capability(root)
    passed = not failures
    result = {
        "gate_id": GATE_ID, "stage": STAGE, "artifacts": [DOMAIN_FILE, ARTIFACT],
        "passed": passed, "failure_count": len(failures),
        "blocker_count": sum(1 for f in failures if f.get("blocks_progress")),
        "failure_ids": [f["failure_id"] for f in failures], "advisory": True,
    }
    write_gate_outputs(root, GATE_ID, result=result, failures=failures,
                       human_review=_render_review(root, failures, passed=passed))
    if failures:
        update_gate_state(root, GATE_ID, failures)
    else:
        clear_gate_state(root, GATE_ID)
    trace_gate_run(root, GATE_ID, failures)
    return passed, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-theory-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true")
    args = parser.parse_args(argv)
    passed, failures = run_gate(args.project_root)
    if passed:
        print("theory gate: theory-capability audit satisfied")
        return 0
    print("theory gate: theory-capability audit NOT satisfied:", file=sys.stderr)
    for f in failures:
        print(f"  - {f['failure_id']} [{f['severity']}] {f['message']}", file=sys.stderr)
    if args.advisory:
        return 0
    return 1 if any(f.get("blocks_progress") for f in failures) else 0


__all__ = ["GATE_ID", "STAGE", "ARTIFACT", "DOMAIN_FILE", "REQUIRED_COLUMNS",
           "verify_theory_capability", "run_gate", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
