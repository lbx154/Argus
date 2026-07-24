"""Novelty-Seeking Loop gate (review stage) — deterministic ARTIFACT verifier.

Part of the physics vertical (NOT a post-processing step). In
``original-research-required`` mode (see ``mode_config``), before the terminal
manuscript the agent must actively seek novelty rather than settle for a
diagnostic benchmark: propose a pool of candidate directions, score them, select
the top 2-3, and do extra theory/numerical verification.

This verifier only inspects agent-produced artifacts; it never invents ideas.
Advisory (does not hard-block review->manuscript) but it feeds the Paper-Type /
manuscript decision.

Artifacts: NOVELTY_IDEA_POOL.csv (+ .md), PIVOT_SELECTION.md,
REVISED_RESEARCH_OBJECTIVE.md, ADDITIONAL_THEORY_PLAN.md, ADDITIONAL_NUMERICAL_PLAN.md.

Failure codes: NSL-000 (idea pool missing/empty), NSL-001 (< MIN_DIRECTIONS),
NSL-002 (direction rows missing required reasoning columns), NSL-003 (missing the
six required scores), NSL-004 (no top-2-3 selection in PIVOT_SELECTION.md),
NSL-005 (missing revised-objective / additional theory / additional numerical plan).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ....skills.capability_registry import CapabilityRegistry
from ....skills.capability_trace import trace_gate_run
from ....skills.research_gates import (
    clear_gate_state,
    read_csv_rows,
    update_gate_state,
    write_gate_outputs,
)
from ..mode_config import is_original_research_required

GATE_ID = "novelty_seeking"
STAGE = "review"
ARTIFACT = "NOVELTY_IDEA_POOL.csv"
MIN_DIRECTIONS = 10

REASONING_COLUMNS: tuple[str, ...] = (
    "direction_id", "direction", "closest_prior_work", "already_known", "possible_gap",
    "why_physically_meaningful", "minimal_theory_check", "minimal_numerical_experiment",
    "expected_evidence_artifact", "risk_of_already_known", "kill_criterion",
)
SCORE_COLUMNS: tuple[str, ...] = (
    "novelty_potential", "prior_work_separation", "physical_significance",
    "feasibility", "evidence_clarity", "risk_of_already_known_score",
)
SUPPORTING_FILES: tuple[str, ...] = (
    "PIVOT_SELECTION.md", "REVISED_RESEARCH_OBJECTIVE.md",
    "ADDITIONAL_THEORY_PLAN.md", "ADDITIONAL_NUMERICAL_PLAN.md",
)


def _fail(fid, sev, message, action, *, field="", blocks=False):
    return {"failure_id": fid, "severity": sev, "stage": STAGE, "artifact": ARTIFACT,
            "field": field, "message": message, "required_action": action,
            "blocks_progress": blocks}


def _c(row: dict, name: str) -> str:
    return str(row.get(name, "") or "").strip()


def _exists(root: Path, name: str) -> bool:
    return (root / name).is_file() or (root / "research" / name).is_file()


def verify_novelty_seeking(project_root: object) -> list[dict]:
    root = Path(str(project_root or "."))
    # Only enforced in original-research-required mode. In auto mode the loop is
    # optional, so nothing is required (pass).
    if not is_original_research_required():
        return []
    header, rows = read_csv_rows(root / ARTIFACT)
    if not header:
        return [_fail("NSL-000", "blocker",
                      f"{ARTIFACT} is missing/empty; original-research mode requires a novelty idea pool.",
                      f"Create {ARTIFACT} with >= {MIN_DIRECTIONS} candidate directions and the required columns.",
                      blocks=True)]
    failures: list[dict] = []
    missing_reason = [c for c in REASONING_COLUMNS if c not in header]
    if missing_reason:
        failures.append(_fail("NSL-002", "major",
                              f"{ARTIFACT} missing reasoning columns: {', '.join(missing_reason)}.",
                              "Add the missing reasoning columns and fill them per direction.",
                              field=",".join(missing_reason)))
    missing_scores = [c for c in SCORE_COLUMNS if c not in header]
    if missing_scores:
        failures.append(_fail("NSL-003", "major",
                              f"{ARTIFACT} missing score columns: {', '.join(missing_scores)}.",
                              "Score every direction on novelty_potential, prior_work_separation, "
                              "physical_significance, feasibility, evidence_clarity, risk_of_already_known.",
                              field=",".join(missing_scores)))
    if len(rows) < MIN_DIRECTIONS:
        failures.append(_fail("NSL-001", "major",
                              f"only {len(rows)} candidate direction(s); need >= {MIN_DIRECTIONS}.",
                              f"Propose at least {MIN_DIRECTIONS} distinct candidate directions.",
                              field="rows"))

    # top 2-3 selection must be recorded
    sel_in_csv = [r for r in rows if _c(r, "selected") and _c(r, "selected").lower() in {"1", "true", "yes", "y", "selected", "top"}]
    if not _exists(root, "PIVOT_SELECTION.md") or (not sel_in_csv and "selected" in header):
        failures.append(_fail("NSL-004", "major",
                              "no top 2-3 selection recorded (PIVOT_SELECTION.md and/or a 'selected' mark).",
                              "Select the top 2-3 directions in PIVOT_SELECTION.md (and mark them 'selected' in the pool).",
                              field="selected"))

    missing_files = [f for f in ("REVISED_RESEARCH_OBJECTIVE.md",
                                 "ADDITIONAL_THEORY_PLAN.md", "ADDITIONAL_NUMERICAL_PLAN.md")
                     if not _exists(root, f)]
    if missing_files:
        failures.append(_fail("NSL-005", "major",
                              f"missing follow-through artifacts: {', '.join(missing_files)}.",
                              "For the selected directions, produce a revised objective + additional theory + additional numerical plan.",
                              field=",".join(missing_files)))
    return failures


def _render_review(root: Path, failures: list[dict], *, passed: bool) -> str:
    reg = CapabilityRegistry(project_root=root)
    caps = reg.for_gate("novelty")  # novelty family informs the loop
    lines = [
        "# Novelty-Seeking Loop review", "",
        f"Status: {'PASS' if passed else 'FAIL'}  |  failures: {len(failures)}  |  artifact: {ARTIFACT}",
        f"Mode: original_research_required={is_original_research_required()}", "",
        "ADVISORY, but in original-research-required mode a downgrade terminal is "
        "illegitimate unless this loop ran (>=10 scored directions, top 2-3 "
        "selected + verified).", "",
        "## Novelty capabilities available (family L)",
    ]
    for c in caps[:12]:
        lines.append(f"- {c.capability_id} [{c.source_layer}] {c.name}")
    if failures:
        lines += ["", "## Failures (see NOVELTY_SEEKING_GATE_REPAIR_TASKS.md)"]
        lines += [f"- {f['failure_id']} [{f['severity']}]: {f['message']}" for f in failures]
    return "\n".join(lines)


def run_gate(project_root: object) -> tuple[bool, list[dict]]:
    root = Path(str(project_root or "."))
    failures = verify_novelty_seeking(root)
    passed = not failures
    result = {"gate_id": GATE_ID, "stage": STAGE, "artifact": ARTIFACT, "passed": passed,
              "failure_count": len(failures),
              "blocker_count": sum(1 for f in failures if f.get("blocks_progress")),
              "failure_ids": [f["failure_id"] for f in failures], "advisory": True,
              "original_research_required": is_original_research_required()}
    write_gate_outputs(root, GATE_ID, result=result, failures=failures,
                       human_review=_render_review(root, failures, passed=passed))
    if failures:
        update_gate_state(root, GATE_ID, failures)
    else:
        clear_gate_state(root, GATE_ID)
    trace_gate_run(root, "novelty", failures)
    return passed, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-novelty-seeking-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true")
    args = parser.parse_args(argv)
    passed, failures = run_gate(args.project_root)
    if passed:
        print("novelty-seeking gate: satisfied (or not required)")
        return 0
    print("novelty-seeking gate: NOT satisfied:", file=sys.stderr)
    for f in failures:
        print(f"  - {f['failure_id']} [{f['severity']}] {f['message']}", file=sys.stderr)
    if args.advisory:
        return 0
    return 1 if any(f.get("blocks_progress") for f in failures) else 0


__all__ = ["GATE_ID", "STAGE", "ARTIFACT", "MIN_DIRECTIONS", "REASONING_COLUMNS",
           "SCORE_COLUMNS", "verify_novelty_seeking", "run_gate", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
