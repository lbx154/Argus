"""Literature Positioning Gate (scope stage) — deterministic ARTIFACT verifier.

Forces Argus to actually read and position the closest direct prior work. The
verifier ONLY inspects the agent-produced ``PRIOR_WORK_MATRIX.csv`` (+ ``.md``); it
never performs web search or full-text reading (those are the agent's scope/model
stage actions) and never touches the network. On failure it emits a machine-readable
failure list + repair context (via :mod:`argus_skill.skills.research_gates`) that is
fed back into the next scope/model prompt.

Phase 1 wiring is **advisory**: the gate does NOT hard-block scope→model, but its
``LITERATURE_GATE_RESULT.json`` feeds the review/claims/paper-type discipline — an
un-passed literature gate forbids framing the manuscript as an original research
article (claims must be downgraded or moved to Limitations).

Failure codes: LIT-000 (artifact/schema), LIT-001 (< 8 direct prior works),
LIT-002 (< 6 substantially read), LIT-003 (missing overlap/difference/special
features), LIT-004 (missing claim implication / closest-prior-work coverage),
LIT-005 (fulltext_status inconsistent with sections_read/evidence).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from ....skills.capability_registry import CapabilityRegistry
from ....skills.capability_trace import trace_gate_run
from ....skills.research_gates import (
    clear_gate_state,
    render_gate_repair_block,
    update_gate_state,
    write_gate_outputs,
)

GATE_ID = "literature"
STAGE = "scope"
ARTIFACT = "PRIOR_WORK_MATRIX.csv"

MIN_DIRECT_PRIOR_WORKS = 8
MIN_FULLTEXT_READ = 6

#: The required PRIOR_WORK_MATRIX.csv columns (the scope-stage prior-work schema).
REQUIRED_COLUMNS: tuple[str, ...] = (
    "paper_id", "citation", "url_or_doi", "fulltext_status", "sections_read",
    "model", "problem", "method", "main_results", "what_it_solved",
    "what_it_did_not_solve", "special_features_of_this_paper",
    "overlap_with_current_work", "difference_from_current_work",
    "does_it_cover_our_claims", "claim_downgrade_needed", "new_opportunity_suggested",
    "required_followup_theory", "required_followup_numerics", "evidence_quote_or_location",
)

_VALID_FULLTEXT_STATUS = {"FULLTEXT_READ", "ABSTRACT_ONLY", "FULLTEXT_UNAVAILABLE"}


def _fail(failure_id: str, severity: str, message: str, required_action: str,
          *, field: str = "", blocks: bool = False) -> dict:
    return {
        "failure_id": failure_id,
        "severity": severity,
        "stage": STAGE,
        "artifact": ARTIFACT,
        "field": field,
        "message": message,
        "required_action": required_action,
        "blocks_progress": blocks,
    }


def _read_rows(root: Path) -> tuple[list[str], list[dict]]:
    path = root / ARTIFACT
    if not path.is_file() or path.stat().st_size == 0:
        return [], []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            rows = [r for r in reader]
    except OSError:
        return [], []
    return header, rows


def verify_literature_positioning(project_root: object) -> list[dict]:
    """Return the deterministic failure list for the literature gate (empty == pass)."""
    root = Path(str(project_root or "."))
    failures: list[dict] = []

    header, rows = _read_rows(root)
    if not header:
        return [_fail(
            "LIT-000", "blocker",
            f"{ARTIFACT} is missing or empty; the scope stage must produce a prior-work matrix.",
            f"Create {ARTIFACT} with the required columns and one row per direct prior work.",
            blocks=True,
        )]

    # (LIT-000) schema: required columns present
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing_cols:
        failures.append(_fail(
            "LIT-000", "blocker",
            f"{ARTIFACT} is missing required columns: {', '.join(missing_cols)}.",
            "Add the missing columns to the prior-work matrix header and fill them per row.",
            field=",".join(missing_cols), blocks=True,
        ))

    def col(row: dict, name: str) -> str:
        return str(row.get(name, "") or "").strip()

    # (LIT-001) >= 8 direct prior works
    if len(rows) < MIN_DIRECT_PRIOR_WORKS:
        failures.append(_fail(
            "LIT-001", "blocker",
            f"only {len(rows)} direct prior works listed; need >= {MIN_DIRECT_PRIOR_WORKS}.",
            f"Search for and add at least {MIN_DIRECT_PRIOR_WORKS - len(rows)} more DIRECT prior "
            "works (not reviews/textbooks) and fill every column.",
            field="paper_id", blocks=True,
        ))

    # (LIT-002) >= 6 substantially read (FULLTEXT_READ with real sections_read)
    fulltext = [r for r in rows
                if col(r, "fulltext_status").upper() == "FULLTEXT_READ" and col(r, "sections_read")]
    if len(fulltext) < MIN_FULLTEXT_READ:
        failures.append(_fail(
            "LIT-002", "major",
            f"only {len(fulltext)} papers substantially read (FULLTEXT_READ with sections_read); "
            f"need >= {MIN_FULLTEXT_READ}.",
            f"Read the main text of at least {MIN_FULLTEXT_READ - len(fulltext)} more direct prior "
            "works and record sections_read + an evidence quote/location.",
            field="fulltext_status", blocks=False,
        ))

    # (LIT-003) per-row positioning: overlap + difference + special features
    incomplete_pos = [col(r, "paper_id") or f"row{i}"
                      for i, r in enumerate(rows, 1)
                      if not (col(r, "overlap_with_current_work")
                              and col(r, "difference_from_current_work")
                              and col(r, "special_features_of_this_paper"))]
    if incomplete_pos:
        failures.append(_fail(
            "LIT-003", "major",
            f"{len(incomplete_pos)} prior-work row(s) lack overlap/difference/special-features "
            f"analysis (e.g. {', '.join(incomplete_pos[:5])}).",
            "For each listed paper fill overlap_with_current_work, difference_from_current_work "
            "and special_features_of_this_paper.",
            field="overlap_with_current_work", blocks=False,
        ))

    # (LIT-004) claim implication: does_it_cover_our_claims per row
    missing_claim = [col(r, "paper_id") or f"row{i}"
                     for i, r in enumerate(rows, 1)
                     if not col(r, "does_it_cover_our_claims")]
    if missing_claim:
        failures.append(_fail(
            "LIT-004", "major",
            f"{len(missing_claim)} prior-work row(s) do not state whether they cover our claims "
            f"(e.g. {', '.join(missing_claim[:5])}); claims must be positioned against the closest "
            "prior work.",
            "For each listed paper fill does_it_cover_our_claims (and set claim_downgrade_needed "
            "when a claim overlaps prior work).",
            field="does_it_cover_our_claims", blocks=False,
        ))

    # (LIT-005) evidence honesty: fulltext_status consistent with sections_read/evidence
    inconsistent: list[str] = []
    for i, r in enumerate(rows, 1):
        status = col(r, "fulltext_status").upper()
        pid = col(r, "paper_id") or f"row{i}"
        if status and status not in _VALID_FULLTEXT_STATUS:
            inconsistent.append(f"{pid}(bad status '{status}')")
        elif status == "FULLTEXT_READ" and not (col(r, "sections_read") and col(r, "evidence_quote_or_location")):
            inconsistent.append(f"{pid}(FULLTEXT_READ w/o sections/evidence)")
    if inconsistent:
        failures.append(_fail(
            "LIT-005", "major",
            f"{len(inconsistent)} row(s) have fulltext_status inconsistent with the recorded "
            f"reading depth (e.g. {', '.join(inconsistent[:5])}).",
            "Set fulltext_status to FULLTEXT_READ / ABSTRACT_ONLY / FULLTEXT_UNAVAILABLE honestly; "
            "a FULLTEXT_READ row must have sections_read AND an evidence_quote_or_location.",
            field="fulltext_status", blocks=False,
        ))

    return failures


def _render_review(root: Path, failures: list[dict], *, passed: bool) -> str:
    reg = CapabilityRegistry(project_root=root)
    caps = reg.for_gate(GATE_ID)
    lines = [
        "# Literature Positioning gate review", "",
        f"Status: {'PASS' if passed else 'FAIL'}  |  failures: {len(failures)}  |  "
        f"artifact: {ARTIFACT}", "",
        "This gate is ADVISORY (it does not block scope->model), but an un-passed "
        "literature gate forbids framing the manuscript as an original research article: "
        "claims lacking a mapped closest prior work must be downgraded or moved to Limitations.",
        "", "## Capabilities exercised (from the CapabilityRegistry)",
    ]
    for c in caps:
        lines.append(f"- {c.capability_id} [{c.source_layer}] {c.name} — {c.pass_threshold or c.basic_criteria}")
    if caps and reg.diagnostics():
        lines += ["", "## Registry diagnostics"] + [f"- {d}" for d in reg.diagnostics()]
    if failures:
        lines += ["", "## Failures (see LITERATURE_GATE_REPAIR_TASKS.md)"]
        lines += [f"- {f['failure_id']} [{f['severity']}]: {f['message']}" for f in failures]
    return "\n".join(lines)


def run_gate(project_root: object) -> tuple[bool, list[dict]]:
    """Run the gate: verify, write RESULT/FAILURES/REPAIR/REVIEW + update repair state."""
    root = Path(str(project_root or "."))
    failures = verify_literature_positioning(root)
    passed = not failures
    blocker_count = sum(1 for f in failures if f.get("blocks_progress"))
    result = {
        "gate_id": GATE_ID,
        "stage": STAGE,
        "artifact": ARTIFACT,
        "passed": passed,
        "failure_count": len(failures),
        "blocker_count": blocker_count,
        "failure_ids": [f["failure_id"] for f in failures],
        "advisory": True,
        "downstream_note": (
            "If passed is false, the manuscript must not be framed as an original research "
            "article; downgrade or limit claims lacking a mapped closest prior work."
        ),
    }
    write_gate_outputs(
        root, GATE_ID,
        result=result, failures=failures,
        human_review=_render_review(root, failures, passed=passed),
    )
    if failures:
        update_gate_state(root, GATE_ID, failures)
    else:
        clear_gate_state(root, GATE_ID)
    trace_gate_run(root, GATE_ID, failures)
    return passed, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-literature-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--advisory", action="store_true",
        help="advisory mode: always exit 0 (write outputs + repair state, never block)",
    )
    args = parser.parse_args(argv)

    passed, failures = run_gate(args.project_root)
    if passed:
        print("literature gate: prior-work positioning satisfied")
        return 0
    blockers = [f for f in failures if f.get("blocks_progress")]
    print("literature gate: prior-work positioning NOT satisfied:", file=sys.stderr)
    for f in failures:
        print(f"  - {f['failure_id']} [{f['severity']}] {f['message']}", file=sys.stderr)
    if args.advisory:
        return 0  # advisory: failures recorded + repair context written, but never blocks
    return 1 if blockers else 0


__all__ = [
    "GATE_ID", "STAGE", "ARTIFACT", "REQUIRED_COLUMNS",
    "MIN_DIRECT_PRIOR_WORKS", "MIN_FULLTEXT_READ",
    "verify_literature_positioning", "run_gate", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
