"""Novelty / Originality Gate (review stage) — deterministic ARTIFACT verifier.

Forces an honest novelty judgement: every claim mapped to its closest prior work,
known separated from new, significance stated, wording calibrated to evidence.
The verifier ONLY inspects the agent-produced ``NOVELTY_CLAIM_TABLE.csv``; it does
not judge whether a contribution is "truly novel". Advisory (does not block
review->manuscript); it FEEDS the Paper-Type classifier (insufficient novelty ->
not an original research article). Capabilities come from the CapabilityRegistry
(family L).

Failure codes: NOV-000 (artifact/schema), NOV-001 (claim lacks closest prior work),
NOV-002 (marked new but already-known not separated), NOV-003 (risky/weak-evidence
claim lacks allowed_wording), NOV-004 (all claims already known but paper_type
implication still original), NOV-005 (why_it_matters / who_would_care missing).
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

GATE_ID = "novelty"
STAGE = "review"
ARTIFACT = "NOVELTY_CLAIM_TABLE.csv"

REQUIRED_COLUMNS: tuple[str, ...] = (
    "claim_id", "claim_text", "claim_type", "closest_prior_work", "already_known",
    "what_is_new_here", "why_it_matters", "who_would_care_and_why", "evidence_required",
    "evidence_available", "evidence_strength", "risk_of_overclaim", "allowed_wording",
    "forbidden_wording", "paper_type_implication",
)


def _fail(fid, sev, message, action, *, field="", blocks=False):
    return {"failure_id": fid, "severity": sev, "stage": STAGE, "artifact": ARTIFACT,
            "field": field, "message": message, "required_action": action,
            "blocks_progress": blocks}


def _c(row: dict, name: str) -> str:
    return str(row.get(name, "") or "").strip()


def verify_novelty(project_root: object) -> list[dict]:
    root = Path(str(project_root or "."))
    header, rows = read_csv_rows(root / ARTIFACT)
    if not header:
        return [_fail("NOV-000", "blocker",
                      f"{ARTIFACT} is missing or empty; the review stage must audit claim novelty.",
                      f"Create {ARTIFACT} with the required columns, one row per headline claim.",
                      blocks=True)]
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing_cols:
        return [_fail("NOV-000", "blocker",
                      f"{ARTIFACT} is missing required columns: {', '.join(missing_cols)}.",
                      "Add the missing columns and fill them per claim.",
                      field=",".join(missing_cols), blocks=True)]

    failures: list[dict] = []
    no_prior = [_c(r, "claim_id") or f"row{i}" for i, r in enumerate(rows, 1) if not _c(r, "closest_prior_work")]
    if no_prior:
        failures.append(_fail("NOV-001", "major",
                              f"{len(no_prior)} claim(s) have no closest_prior_work (e.g. {', '.join(no_prior[:5])}).",
                              "Map every claim to its closest prior work.", field="closest_prior_work"))

    unsep = [_c(r, "claim_id") or f"row{i}" for i, r in enumerate(rows, 1)
             if _c(r, "what_is_new_here") and not _c(r, "already_known")]
    if unsep:
        failures.append(_fail("NOV-002", "major",
                              f"{len(unsep)} claim(s) assert what_is_new_here without stating already_known "
                              f"(e.g. {', '.join(unsep[:5])}).",
                              "State what is already known (with citation) so the new part is separated.",
                              field="already_known"))

    risky_no_wording = [_c(r, "claim_id") or f"row{i}" for i, r in enumerate(rows, 1)
                        if (is_truthy(_c(r, "risk_of_overclaim")) or not is_truthy(_c(r, "evidence_available")))
                        and not _c(r, "allowed_wording")]
    if risky_no_wording:
        failures.append(_fail("NOV-003", "major",
                              f"{len(risky_no_wording)} risky/weak-evidence claim(s) have no allowed_wording "
                              f"(e.g. {', '.join(risky_no_wording[:5])}).",
                              "Set allowed_wording (hedged) and forbidden_wording for each risky claim.",
                              field="allowed_wording"))

    all_known = bool(rows) and all(
        _c(r, "already_known") and not _c(r, "what_is_new_here") for r in rows
    )
    if all_known and any("original" in _c(r, "paper_type_implication").lower() for r in rows):
        failures.append(_fail("NOV-004", "major",
                              "all claims are already known yet a paper_type_implication still says 'original'.",
                              "Downgrade the paper type (benchmark/reproduction/report); an all-known result is "
                              "not an original research article.", field="paper_type_implication"))

    no_sig = [_c(r, "claim_id") or f"row{i}" for i, r in enumerate(rows, 1)
              if not (_c(r, "why_it_matters") and _c(r, "who_would_care_and_why"))]
    if no_sig:
        failures.append(_fail("NOV-005", "minor",
                              f"{len(no_sig)} claim(s) lack why_it_matters / who_would_care "
                              f"(e.g. {', '.join(no_sig[:5])}).",
                              "State why each claim matters and who would care.", field="why_it_matters"))
    return failures


def _render_review(root: Path, failures: list[dict], *, passed: bool) -> str:
    reg = CapabilityRegistry(project_root=root)
    caps = reg.for_gate(GATE_ID)
    lines = [
        "# Novelty gate review", "",
        f"Status: {'PASS' if passed else 'FAIL'}  |  failures: {len(failures)}  |  artifact: {ARTIFACT}", "",
        "ADVISORY (does not block review->manuscript) but FEEDS the Paper-Type classifier: "
        "insufficient novelty means the manuscript may not be framed as an original research article.", "",
        "## Novelty capabilities (from the CapabilityRegistry, family L)",
    ]
    for c in caps:
        lines.append(f"- {c.capability_id} [{c.source_layer}] {c.name} — {c.pass_threshold or c.basic_criteria}")
    if failures:
        lines += ["", "## Failures (see NOVELTY_GATE_REPAIR_TASKS.md)"]
        lines += [f"- {f['failure_id']} [{f['severity']}]: {f['message']}" for f in failures]
    return "\n".join(lines)


def run_gate(project_root: object) -> tuple[bool, list[dict]]:
    root = Path(str(project_root or "."))
    failures = verify_novelty(root)
    passed = not failures
    result = {"gate_id": GATE_ID, "stage": STAGE, "artifact": ARTIFACT, "passed": passed,
              "failure_count": len(failures),
              "blocker_count": sum(1 for f in failures if f.get("blocks_progress")),
              "failure_ids": [f["failure_id"] for f in failures], "advisory": True}
    write_gate_outputs(root, GATE_ID, result=result, failures=failures,
                       human_review=_render_review(root, failures, passed=passed))
    if failures:
        update_gate_state(root, GATE_ID, failures)
    else:
        clear_gate_state(root, GATE_ID)
    trace_gate_run(root, GATE_ID, failures)
    return passed, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-novelty-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true")
    args = parser.parse_args(argv)
    passed, failures = run_gate(args.project_root)
    if passed:
        print("novelty gate: novelty audit satisfied")
        return 0
    print("novelty gate: novelty audit NOT satisfied:", file=sys.stderr)
    for f in failures:
        print(f"  - {f['failure_id']} [{f['severity']}] {f['message']}", file=sys.stderr)
    if args.advisory:
        return 0
    return 1 if any(f.get("blocks_progress") for f in failures) else 0


__all__ = ["GATE_ID", "STAGE", "ARTIFACT", "REQUIRED_COLUMNS", "verify_novelty", "run_gate", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
