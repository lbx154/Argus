"""Paper-Type Classifier Gate (review stage) — deterministic ARTIFACT verifier.

Keeps the claimed result TYPE honest and consistent with the upstream research
gates. The verifier inspects ``PAPER_TYPE_CLASSIFIER.json`` and CONSUMES the
Literature / Novelty / Numerical gate results (``research/*_GATE_RESULT.json``): a
paper cannot be an original research article candidate unless those gates support
it. Advisory (does not block review->manuscript); the manuscript stage must honour
the resulting paper type (see manuscript_review_items).

Failure codes: PT-000 (artifact/schema), PT-001 (publishable type but the
Literature gate has not passed), PT-002 (original type but the Novelty gate has not
passed), PT-003 (publishable type not backed by the gates' basis fields),
PT-004 (invalid paper_type / missing confidence), PT-005 (publishable type without
an honest why_not_higher / what_would_upgrade_it / forbidden_claims).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ....skills.research_gates import (
    clear_gate_state,
    update_gate_state,
    write_gate_outputs,
)
from ....skills.capability_trace import trace_gate_run
from ..mode_config import is_downgrade_type, is_original_research_required

GATE_ID = "paper_type"
STAGE = "review"
ARTIFACT = "PAPER_TYPE_CLASSIFIER.json"

VALID_PAPER_TYPES = {
    "original research article candidate",
    "short communication / letter candidate",
    "methods paper",
    "diagnostic benchmark",
    "replication / reproduction study",
    "research training report",
    "internal progress report",
    "negative result note",
    "literature-informed proposal",
    "blocked / insufficient evidence",
}

REQUIRED_FIELDS: tuple[str, ...] = (
    "paper_type", "confidence", "basis_from_literature_gate", "basis_from_theory_gate",
    "basis_from_numerical_gate", "basis_from_novelty_gate", "why_not_higher",
    "what_would_upgrade_it", "forbidden_claims", "allowed_claims",
    "recommended_title_style", "recommended_abstract_tone",
)

_PUBLISHABLE = ("original", "letter", "communication", "methods paper")


def _fail(fid, sev, message, action, *, field="", blocks=False):
    return {"failure_id": fid, "severity": sev, "stage": STAGE, "artifact": ARTIFACT,
            "field": field, "message": message, "required_action": action,
            "blocks_progress": blocks}


def _gate_passed(root: Path, prefix: str) -> bool:
    """True only if research/<PREFIX>_RESULT.json exists and reports passed=true."""
    try:
        data = json.loads((root / "research" / f"{prefix}_RESULT.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("passed") is True)


def verify_paper_type(project_root: object) -> list[dict]:
    root = Path(str(project_root or "."))
    path = root / ARTIFACT
    if not path.is_file() or path.stat().st_size == 0:
        return [_fail("PT-000", "blocker",
                      f"{ARTIFACT} is missing or empty; the review stage must classify the result type.",
                      f"Create {ARTIFACT} with paper_type and the basis/allowed/forbidden fields.",
                      blocks=True)]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return [_fail("PT-000", "blocker", f"{ARTIFACT} is not valid JSON.",
                      "Fix the JSON syntax.", blocks=True)]
    if not isinstance(data, dict):
        return [_fail("PT-000", "blocker", f"{ARTIFACT} must be a JSON object.", "Make it an object.", blocks=True)]

    def g(name: str) -> str:
        return str(data.get(name, "") or "").strip()

    failures: list[dict] = []
    missing = [f for f in REQUIRED_FIELDS if not g(f)]
    if missing:
        failures.append(_fail("PT-000", "major",
                              f"{ARTIFACT} is missing fields: {', '.join(missing)}.",
                              "Fill every classifier field.", field=",".join(missing)))

    paper_type = g("paper_type").lower()
    if paper_type and paper_type not in VALID_PAPER_TYPES:
        failures.append(_fail("PT-004", "major",
                              f"paper_type '{g('paper_type')}' is not one of the allowed types.",
                              f"Use one of: {', '.join(sorted(VALID_PAPER_TYPES))}.", field="paper_type"))
    if not g("confidence"):
        failures.append(_fail("PT-004", "minor", "confidence is missing.",
                              "State a confidence for the classification.", field="confidence"))

    publishable = any(k in paper_type for k in _PUBLISHABLE)
    original = "original" in paper_type

    if publishable and not _gate_passed(root, "LITERATURE_GATE"):
        failures.append(_fail("PT-001", "major",
                              f"paper_type '{g('paper_type')}' claims a publishable type but the Literature "
                              "gate has not passed.",
                              "Pass the Literature Positioning gate, or downgrade the paper type to "
                              "training report / blocked-insufficient-evidence.", field="paper_type"))
    if original and not _gate_passed(root, "NOVELTY_GATE"):
        failures.append(_fail("PT-002", "major",
                              "paper_type claims an original research article but the Novelty gate has not passed.",
                              "Pass the Novelty gate (or downgrade the paper type); insufficient novelty is not "
                              "an original article.", field="paper_type"))
    if publishable and not (g("basis_from_literature_gate") and g("basis_from_numerical_gate")
                            and g("basis_from_novelty_gate")):
        failures.append(_fail("PT-003", "major",
                              "a publishable paper_type is not backed by the basis_from_*_gate fields.",
                              "Fill basis_from_literature_gate / numerical_gate / novelty_gate with the evidence "
                              "each gate provides.", field="basis_from_literature_gate"))
    if publishable and not (g("why_not_higher") and g("what_would_upgrade_it") and g("forbidden_claims")):
        failures.append(_fail("PT-005", "minor",
                              "a publishable paper_type lacks an honest why_not_higher / what_would_upgrade_it / "
                              "forbidden_claims.",
                              "State why it is not a higher type, what would upgrade it, and the forbidden claims.",
                              field="why_not_higher"))

    # PT-006: original-research-required mode — a downgrade type is not a legitimate
    # SUCCESS terminal. It is allowed only as an intermediate: either upgrade to an
    # original type (with the gates passing) or emit a justified ORIGINAL_RESEARCH_NO_GO.md
    # after running the Novelty-Seeking Loop (<=2 pivots).
    if is_original_research_required() and is_downgrade_type(g("paper_type")):
        no_go = (root / "ORIGINAL_RESEARCH_NO_GO.md").is_file() or \
                (root / "research" / "ORIGINAL_RESEARCH_NO_GO.md").is_file()
        if not no_go:
            failures.append(_fail("PT-006", "blocker",
                                  f"original-research-required mode: paper_type '{g('paper_type')}' is a "
                                  "downgrade type, which may only be intermediate — not a success terminal.",
                                  "Run the Novelty-Seeking Loop and pivot to an original type, or (after <=2 "
                                  "pivots) emit ORIGINAL_RESEARCH_NO_GO.md explaining why sufficient novelty "
                                  "was not found. Do NOT complete as a diagnostic benchmark.",
                                  field="paper_type", blocks=True))
    return failures


def _render_review(root: Path, failures: list[dict], *, passed: bool) -> str:
    lit = _gate_passed(root, "LITERATURE_GATE")
    nov = _gate_passed(root, "NOVELTY_GATE")
    numr = _gate_passed(root, "NUMERICAL_GATE")
    lines = [
        "# Paper-Type classifier review", "",
        f"Status: {'PASS' if passed else 'FAIL'}  |  failures: {len(failures)}  |  artifact: {ARTIFACT}", "",
        f"Upstream gate support -> literature: {lit}  novelty: {nov}  numerical: {numr}", "",
        "ADVISORY. A paper cannot be an original research article candidate unless the "
        "Literature and Novelty gates pass; the manuscript stage must honour the paper type.",
    ]
    if failures:
        lines += ["", "## Failures (see PAPER_TYPE_GATE_REPAIR_TASKS.md)"]
        lines += [f"- {f['failure_id']} [{f['severity']}]: {f['message']}" for f in failures]
    return "\n".join(lines)


def run_gate(project_root: object) -> tuple[bool, list[dict]]:
    root = Path(str(project_root or "."))
    failures = verify_paper_type(root)
    passed = not failures
    result = {"gate_id": GATE_ID, "stage": STAGE, "artifact": ARTIFACT, "passed": passed,
              "failure_count": len(failures),
              "blocker_count": sum(1 for f in failures if f.get("blocks_progress")),
              "failure_ids": [f["failure_id"] for f in failures], "advisory": True,
              "literature_gate_passed": _gate_passed(root, "LITERATURE_GATE"),
              "novelty_gate_passed": _gate_passed(root, "NOVELTY_GATE")}
    write_gate_outputs(root, GATE_ID, result=result, failures=failures,
                       human_review=_render_review(root, failures, passed=passed))
    if failures:
        update_gate_state(root, GATE_ID, failures)
    else:
        clear_gate_state(root, GATE_ID)
    trace_gate_run(root, GATE_ID, failures)
    return passed, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-paper-type-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true")
    args = parser.parse_args(argv)
    passed, failures = run_gate(args.project_root)
    if passed:
        print("paper-type gate: classification consistent with the research gates")
        return 0
    print("paper-type gate: classification NOT consistent:", file=sys.stderr)
    for f in failures:
        print(f"  - {f['failure_id']} [{f['severity']}] {f['message']}", file=sys.stderr)
    if args.advisory:
        return 0
    return 1 if any(f.get("blocks_progress") for f in failures) else 0


__all__ = ["GATE_ID", "STAGE", "ARTIFACT", "VALID_PAPER_TYPES", "REQUIRED_FIELDS",
           "verify_paper_type", "run_gate", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
