"""Manuscript-package contract gate (review stage) — ADVISORY, repair-loop-backed.

Root-cause fix for the Case B lifecycle stall: the deterministic manuscript
delivery contract (`verify_all_deliverables`, i.e. `manuscript check --layer all`)
was only ever *run* at the terminal ``manuscript`` stage's HARD gate
(``manager/_core.py`` ``enforce_terminal_stage_check``). But the paper package is
produced and judged at the ``review`` stage, so when it did not satisfy the
contract the agent never received the concrete failure list — the reviewer only
gave vague prose, the engineer-runner semantic-stall detector bailed the mission
``no_progress``, and it never reached the manuscript stage where the repair loop
lives.

This gate closes that gap. Once a paper package exists (MANUSCRIPT.md or
MANUSCRIPT.tex present), it runs the SAME deterministic checker at ``review`` and
writes the exact failure list through the shared ``research_gates`` repair
machinery, so the physics ``role_banner`` injects those failures + an executable
repair loop into the next agent round (via ``render_active_repair_blocks``). It is
strictly ADVISORY: it NEVER hard-blocks review->manuscript and NEVER weakens the
terminal contract — the terminal ``manuscript`` HARD gate is unchanged and remains
the only thing that authorises completion. When no paper package exists yet the
gate passes (the paper is a manuscript-stage deliverable; nothing to surface).

Failure ids: MPKG-000 (checker unavailable), MPKG-NNN (one per deterministic
`verify_all_deliverables` failure, verbatim).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ....skills.research_gates import (
    clear_gate_state,
    update_gate_state,
    write_gate_outputs,
)

GATE_ID = "manuscript_package"
STAGE = "review"
#: The paper package is present (agent has started building the terminal deliverable)
#: when either the source-layer MANUSCRIPT.md or the paper-layer MANUSCRIPT.tex exists.
_PRESENCE_FILES = ("MANUSCRIPT.md", "MANUSCRIPT.tex")


def _paper_package_present(root: Path) -> bool:
    return any((root / name).is_file() and (root / name).stat().st_size > 0
               for name in _PRESENCE_FILES)


def verify_manuscript_package(project_root: object) -> list[dict]:
    """Return the deterministic manuscript-contract failures as GateFailure dicts.

    Empty when the contract is satisfied OR no paper package exists yet. Never
    raises: a checker-import/runtime error degrades to a single advisory note.
    """
    root = Path(str(project_root or "."))
    if not _paper_package_present(root):
        return []
    try:
        from ..manuscript import verify_all_deliverables
    except Exception as exc:  # noqa: BLE001 — never break the stage on an import error
        return [{
            "failure_id": "MPKG-000", "severity": "warning", "stage": STAGE,
            "artifact": "manuscript.py", "field": "",
            "message": f"manuscript checker unavailable: {type(exc).__name__}: {exc}",
            "required_action": "restore the physics manuscript verifier",
            "blocks_progress": False,
        }]
    try:
        raw = verify_all_deliverables(root)
    except Exception as exc:  # noqa: BLE001
        return [{
            "failure_id": "MPKG-000", "severity": "warning", "stage": STAGE,
            "artifact": "manuscript.py", "field": "",
            "message": f"manuscript checker errored: {type(exc).__name__}: {exc}",
            "required_action": "fix the inputs so `manuscript check --layer all` can run",
            "blocks_progress": False,
        }]
    failures: list[dict] = []
    for i, msg in enumerate(raw, 1):
        failures.append({
            "failure_id": f"MPKG-{i:03d}",
            "severity": "major",
            "stage": STAGE,
            "artifact": "MANUSCRIPT/SUPPLEMENT package",
            "field": "",
            "message": str(msg),
            "required_action": (
                "Resolve this exact deterministic manuscript-contract item, then re-run "
                "`python -m argus_skill.verticals.physics.manuscript check --layer all` "
                "until it prints 'satisfied'."
            ),
            # ADVISORY at review — never hard-blocks; the terminal manuscript stage
            # remains the only HARD gate.
            "blocks_progress": False,
        })
    return failures


def _render_review(failures: list[dict], *, passed: bool, present: bool) -> str:
    lines = [
        "# Manuscript-package contract gate review (advisory, review stage)", "",
        f"Status: {'PASS' if passed else 'FAIL'}  |  failures: {len(failures)}  |  "
        f"paper package present: {present}", "",
        "ADVISORY: this surfaces the SAME deterministic contract as the terminal "
        "`manuscript check --layer all` gate, so the agent gets the exact failure list "
        "and an executable repair loop as soon as a paper package exists at review. It "
        "never hard-blocks review->manuscript and never weakens the terminal contract.",
    ]
    if not present:
        lines += ["", "No paper package yet (MANUSCRIPT.md/.tex absent) — nothing to check; "
                  "the paper is a manuscript-stage deliverable."]
    if failures:
        lines += ["", "## Deterministic failures to eliminate (see MANUSCRIPT_PACKAGE_GATE_REPAIR_TASKS.md)"]
        lines += [f"- {f['failure_id']} [{f['severity']}]: {f['message']}" for f in failures]
    return "\n".join(lines)


def run_gate(project_root: object) -> tuple[bool, list[dict]]:
    root = Path(str(project_root or "."))
    present = _paper_package_present(root)
    failures = verify_manuscript_package(root)
    passed = not failures
    result = {
        "gate_id": GATE_ID, "stage": STAGE, "artifact": "MANUSCRIPT/SUPPLEMENT package",
        "passed": passed, "failure_count": len(failures),
        "blocker_count": 0,  # advisory: never a hard blocker at review
        "failure_ids": [f["failure_id"] for f in failures],
        "advisory": True, "paper_package_present": present,
        "downstream_note": (
            "Mirrors the terminal `manuscript check --layer all` contract. If failures "
            "are present, the manuscript stage cannot complete until they are resolved; "
            "resolve them here so review->manuscript->project_done proceeds without a "
            "no_progress stall."
        ),
    }
    write_gate_outputs(root, GATE_ID, result=result, failures=failures,
                       human_review=_render_review(failures, passed=passed, present=present))
    if failures:
        update_gate_state(root, GATE_ID, failures)
    else:
        clear_gate_state(root, GATE_ID)
    return passed, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-manuscript-package-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true",
                        help="advisory mode: always exit 0 (write outputs + repair state, never block)")
    args = parser.parse_args(argv)
    passed, failures = run_gate(args.project_root)
    if passed:
        print("manuscript-package gate: terminal delivery contract satisfied (or no package yet)")
        return 0
    print("manuscript-package gate: deterministic delivery contract NOT satisfied:", file=sys.stderr)
    for f in failures:
        print(f"  - {f['failure_id']} [{f['severity']}] {f['message']}", file=sys.stderr)
    # Always advisory at the review stage: record the repair context, never block.
    return 0


__all__ = [
    "GATE_ID", "STAGE", "verify_manuscript_package", "run_gate", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
