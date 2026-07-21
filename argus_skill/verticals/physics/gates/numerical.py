"""Numerical Capability Gate (execute stage) — deterministic ARTIFACT verifier.

Forces Argus to plan and evidence a systematic numerical study proportional to its
claims. The verifier ONLY inspects the agent-produced ``NUMERICAL_STUDY_PLAN.csv``
(and cross-checks ``CLAIMS.csv``); it never runs numerics itself. Advisory (does
not block execute->review); failures feed the next-round repair context. Applicable
capabilities come from the CapabilityRegistry (families F/G).

Failure codes: NUM-001 (plan missing/incomplete / no domain-specific capability),
NUM-002 (used capability lacks evidence), NUM-003 (a robust/protected/stable claim
lacks a used+evidenced robustness capability), NUM-004 (a phase-diagram/universal
claim lacks a used+evidenced scan capability), NUM-005 (figure traceability not
executed), NUM-006 (unlisted numerical capability used without self-evaluation).
"""
from __future__ import annotations

import argparse
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

GATE_ID = "numerical"
STAGE = "execute"
ARTIFACT = "NUMERICAL_STUDY_PLAN.csv"
CLAIMS = "CLAIMS.csv"

REQUIRED_COLUMNS: tuple[str, ...] = (
    "capability_id", "capability_name", "generic_or_domain_specific", "domain",
    "is_applicable", "applicability_reason", "planned_by_argus", "used_by_argus",
    "execution_level_basic_or_advanced_or_missing", "evidence_file", "evidence_location",
    "comparison_to_prior_work", "if_not_used_why", "risk_if_missing",
    "required_for_current_claim", "recommended_next_action",
)

_ROBUST_CLAIM_KW = ("robust", "protected", "protection", "stable", "stability")
_SCAN_CLAIM_KW = ("phase diagram", "phase-diagram", "phase boundary", "universal",
                  "universality", "scan", "sweep")
_ROBUST_CAP_KW = ("robust", "stability", "perturbation")
_SCAN_CAP_KW = ("scan", "phase", "sweep")
_FIG_CAP_KW = ("traceab", "figure")


def _fail(fid, sev, message, action, *, field="", blocks=False):
    return {"failure_id": fid, "severity": sev, "stage": STAGE, "artifact": ARTIFACT,
            "field": field, "message": message, "required_action": action,
            "blocks_progress": blocks}


def _c(row: dict, name: str) -> str:
    return str(row.get(name, "") or "").strip()


def _capability_covered(rows: list[dict], keywords: tuple[str, ...]) -> bool:
    """True if some plan row for a matching capability is used_by_argus + evidenced."""
    for r in rows:
        text = (_c(r, "capability_id") + " " + _c(r, "capability_name")).lower()
        if any(k in text for k in keywords) and is_truthy(_c(r, "used_by_argus")) and _c(r, "evidence_file"):
            return True
    return False


def _claim_texts(root: Path) -> list[str]:
    _hdr, rows = read_csv_rows(root / CLAIMS)
    return [(_c(r, "claim_text") + " " + _c(r, "claim_type")).lower() for r in rows]


def verify_numerical_capability(project_root: object) -> list[dict]:
    root = Path(str(project_root or "."))
    failures: list[dict] = []

    header, rows = read_csv_rows(root / ARTIFACT)
    if not header:
        return [_fail("NUM-001", "blocker",
                      f"{ARTIFACT} is missing or empty; the execute stage must plan a numerical study.",
                      f"Create {ARTIFACT} with the required columns, one row per candidate numerical capability.",
                      blocks=True)]
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing_cols:
        return [_fail("NUM-001", "blocker",
                      f"{ARTIFACT} is missing required columns: {', '.join(missing_cols)}.",
                      "Add the missing columns and fill them per capability row.",
                      field=",".join(missing_cols), blocks=True)]

    # (NUM-001) audit completeness + at least one domain-specific capability
    incomplete = [_c(r, "capability_id") or f"row{i}" for i, r in enumerate(rows, 1)
                  if not (_c(r, "is_applicable") and _c(r, "applicability_reason"))]
    if incomplete:
        failures.append(_fail("NUM-001", "major",
                              f"{len(incomplete)} plan row(s) lack is_applicable/applicability_reason "
                              f"(e.g. {', '.join(incomplete[:5])}).",
                              "For every candidate capability set is_applicable and applicability_reason.",
                              field="is_applicable"))
    if not any("domain" in _c(r, "generic_or_domain_specific").lower()
               and is_truthy(_c(r, "is_applicable")) for r in rows):
        failures.append(_fail("NUM-001", "major",
                              "no applicable DOMAIN-SPECIFIC numerical capability is present in the plan.",
                              "Load at least one applicable domain-specific numerical capability.",
                              field="generic_or_domain_specific"))

    # (NUM-002) used capability lacks evidence
    used_no_ev = [_c(r, "capability_id") or f"row{i}" for i, r in enumerate(rows, 1)
                  if is_truthy(_c(r, "used_by_argus")) and not _c(r, "evidence_file")]
    if used_no_ev:
        failures.append(_fail("NUM-002", "major",
                              f"{len(used_no_ev)} used numerical capability(ies) have no evidence_file "
                              f"(e.g. {', '.join(used_no_ev[:5])}).",
                              "Point evidence_file at the data/figure/notebook proving each used capability.",
                              field="evidence_file"))

    # (NUM-003 / NUM-004) claim-driven cross-checks against CLAIMS.csv
    claims = _claim_texts(root)
    if any(any(k in t for k in _ROBUST_CLAIM_KW) for t in claims) and \
            not _capability_covered(rows, _ROBUST_CAP_KW):
        failures.append(_fail("NUM-003", "major",
                              "a claim asserts robust/protected/stable but the plan has no used + evidenced "
                              "robustness/perturbation capability.",
                              "Run a robustness/perturbation study with evidence, or downgrade the claim to "
                              "'shown at one point'.", field="used_by_argus"))
    if any(any(k in t for k in _SCAN_CLAIM_KW) for t in claims) and \
            not _capability_covered(rows, _SCAN_CAP_KW):
        failures.append(_fail("NUM-004", "major",
                              "a claim asserts a phase diagram / boundary / universal trend but the plan has no "
                              "used + evidenced parameter-scan capability.",
                              "Run a parameter scan with evidence, or downgrade the claim to 'single-point'.",
                              field="used_by_argus"))

    # (NUM-005) figure traceability applicable but not executed
    fig_rows = [r for r in rows
                if any(k in (_c(r, "capability_id") + " " + _c(r, "capability_name")).lower() for k in _FIG_CAP_KW)]
    if fig_rows and not any(is_truthy(_c(r, "used_by_argus")) and _c(r, "evidence_file") for r in fig_rows):
        failures.append(_fail("NUM-005", "major",
                              "figure/data traceability is applicable but not executed with evidence.",
                              "Ship a per-figure generating script + a figure manifest linking data->figure.",
                              field="evidence_file"))

    # (NUM-006) unlisted capability used without self-evaluation
    other_bad = [_c(r, "capability_id") for r in rows
                 if _c(r, "capability_id").upper().startswith("OTHER")
                 and not (_c(r, "applicability_reason") and _c(r, "comparison_to_prior_work"))]
    if other_bad:
        failures.append(_fail("NUM-006", "minor",
                              f"{len(other_bad)} OTHER_NUMERICAL_CAPABILITY row(s) lack self-evaluation "
                              f"(e.g. {', '.join(other_bad[:5])}).",
                              "For each OTHER capability give applicability_reason + comparison_to_prior_work.",
                              field="capability_id"))
    return failures


def _render_review(root: Path, failures: list[dict], *, passed: bool) -> str:
    reg = CapabilityRegistry(project_root=root)
    caps = reg.for_gate(GATE_ID)
    lines = [
        "# Numerical Capability gate review", "",
        f"Status: {'PASS' if passed else 'FAIL'}  |  failures: {len(failures)}  |  artifact: {ARTIFACT}", "",
        "ADVISORY (does not block execute->review). A claim of robustness or a phase diagram "
        "needs the corresponding numerical evidence, or the claim must be downgraded.", "",
        "## Applicable numerical capabilities (from the CapabilityRegistry, families F/G)",
    ]
    for c in caps:
        lines.append(f"- {c.capability_id} [{c.source_layer}] {c.name} — {c.pass_threshold or c.basic_criteria}")
    if failures:
        lines += ["", "## Failures (see NUMERICAL_GATE_REPAIR_TASKS.md)"]
        lines += [f"- {f['failure_id']} [{f['severity']}]: {f['message']}" for f in failures]
    return "\n".join(lines)


def run_gate(project_root: object) -> tuple[bool, list[dict]]:
    root = Path(str(project_root or "."))
    failures = verify_numerical_capability(root)
    passed = not failures
    result = {
        "gate_id": GATE_ID, "stage": STAGE, "artifact": ARTIFACT, "passed": passed,
        "failure_count": len(failures),
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
    parser = argparse.ArgumentParser(prog="physics-numerical-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true")
    args = parser.parse_args(argv)
    passed, failures = run_gate(args.project_root)
    if passed:
        print("numerical gate: numerical-capability plan satisfied")
        return 0
    print("numerical gate: numerical-capability plan NOT satisfied:", file=sys.stderr)
    for f in failures:
        print(f"  - {f['failure_id']} [{f['severity']}] {f['message']}", file=sys.stderr)
    if args.advisory:
        return 0
    return 1 if any(f.get("blocks_progress") for f in failures) else 0


__all__ = ["GATE_ID", "STAGE", "ARTIFACT", "REQUIRED_COLUMNS",
           "verify_numerical_capability", "run_gate", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
