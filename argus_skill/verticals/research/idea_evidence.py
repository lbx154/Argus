"""Idea evidence for research work: what a probe actually established.

Research has the same failure mode kernel work has, with higher stakes. A
pilot that under-performs looks like a refuted hypothesis, and an early idea
judged against a publication bar looks like a bad idea. Both readings kill
work that was never actually tested.

The four-state model and its invariants come from
:mod:`argus_skill.core.evidence_status`. What this module adds is the research
vocabulary, and three rules that follow from it:

* **An inadequate implementation is not a disproof.** ``implementation`` is
  not a refuting failure class. Before a negative result may be called
  ``refuted``, someone has to establish that the thing being tested was built
  competently — otherwise the finding is about the code, not the idea.
* **An under-powered pilot is inconclusive, not negative.** ``statistical_power``
  sits in the non-idea set: a noise-dominated run or a toy-sized sample may
  inform the next probe, but it may not settle the premise. This is about the
  measurement being drowned in noise, not about repeating runs — a single
  well-configured run at real scale is normal evidence.
* **Prior art is a replanning signal, not a refutation.** Finding that an
  existing paper covers the idea changes what to work on next. It is not
  evidence that the premise is false, and recording it as such loses the
  distinction between "someone did this" and "this does not work".

``premise`` is versioned deliberately: redefining the premise starts a fresh
evidence record, so a conclusion about the old premise cannot silently be
carried over to the new one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ...core.evidence_status import (
    BASE_FAILURE_CLASSES,
    BASE_NON_IDEA_FAILURES,
    EXECUTION_STATUSES,
    IDEA_STATUSES,
    EvidenceContract,
    is_placeholder_text,
    validate_evidence,
)

SCHEMA_VERSION = 1

_RESEARCH_ONLY_FAILURES = frozenset(
    {
        # The data, benchmark, or license was not reachable.
        "data_access",
        # The evaluator was missing, stubbed, or scoring constants.
        "evaluator_infrastructure",
        # It ran, but the measurement was noise-dominated or toy-sized.
        "statistical_power",
        # A real, adequately-powered measurement. The only thing that can settle
        # the premise either way.
        "empirical",
        # Scheduling signals: they change what to do next, not what is true.
        "prior_art",
        "scope_change",
    }
)
FAILURE_CLASSES = BASE_FAILURE_CLASSES | _RESEARCH_ONLY_FAILURES

NON_IDEA_FAILURES = BASE_NON_IDEA_FAILURES | frozenset(
    {
        "data_access",
        "evaluator_infrastructure",
        "statistical_power",
    }
)

ADVISORY_FAILURES = frozenset({"prior_art", "scope_change"})

RESEARCH_EVIDENCE = EvidenceContract(
    domain="research",
    failure_classes=FAILURE_CLASSES,
    non_idea_failures=NON_IDEA_FAILURES,
    # Without these a result cannot be re-checked, and "we tried it and it
    # didn't work" is indistinguishable from "we ran the wrong thing".
    grounding_fields=("premise", "evaluator_identity", "comparison_identity"),
    # Notably excludes `implementation`: under-performance is a statement about
    # the implementation until an adequacy audit says otherwise.
    refuting_failures=frozenset({"empirical"}),
    advisory_failures=ADVISORY_FAILURES,
)

__all__ = [
    "ADVISORY_FAILURES",
    "EXECUTION_STATUSES",
    "FAILURE_CLASSES",
    "IDEA_STATUSES",
    "NON_IDEA_FAILURES",
    "RESEARCH_EVIDENCE",
    "SCHEMA_VERSION",
    "evidence_files",
    "main",
    "template",
    "validate_idea_evidence",
]


def validate_idea_evidence(record: dict[str, Any]) -> list[str]:
    """Research-specific checks, then the shared four-state invariants."""
    errors: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {record.get('schema_version')!r}")
    if is_placeholder_text(record.get("idea_id")):
        errors.append("idea_id is empty or templated")
    # The premise is required on every record, not only conclusive ones: an
    # untested idea still has to say what it would have tested.
    if is_placeholder_text(record.get("premise")):
        errors.append("premise is empty or templated")
    if not isinstance(record.get("premise_version"), int):
        errors.append(
            "premise_version must be an integer; bump it when the premise changes "
            "so an old verdict is not carried onto a new premise"
        )
    errors.extend(validate_evidence(record, RESEARCH_EVIDENCE))
    return list(dict.fromkeys(errors))


def template(idea_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "idea_id": idea_id,
        "premise_version": 1,
        "premise": "REPLACE with the exact falsifiable premise under test",
        "execution_status": "blocked",
        "failure_class": "environment",
        "idea_status": "untested",
        "evaluator_identity": "REPLACE with the evaluator/metric and its revision",
        "comparison_identity": "REPLACE with the baseline or comparison condition",
        "summary": "REPLACE with the concise observed outcome",
        "evidence": "REPLACE with artifact paths or exact command result",
    }


def evidence_files(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for dirname in ("research/ideas", "experiments", "ideas"):
        root = project_root / dirname
        if root.is_dir():
            paths.extend(root.rglob("EVIDENCE.json"))
    return sorted(set(paths))


def _read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("template")
    show.add_argument("--idea-id", required=True)
    check = sub.add_parser("check")
    check.add_argument("--project-root", type=Path, default=Path.cwd())
    check_file = sub.add_parser("check-file")
    check_file.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "template":
        print(json.dumps(template(args.idea_id), indent=2, sort_keys=True))
        return 0
    paths = [args.path] if args.command == "check-file" else evidence_files(args.project_root)
    if not paths:
        print("no research/ideas/**/EVIDENCE.json records found", file=sys.stderr)
        return 2
    failed = False
    for path in paths:
        record = _read(path)
        if record is None:
            print(f"ERROR: {path}: unreadable evidence JSON", file=sys.stderr)
            failed = True
            continue
        errors = validate_idea_evidence(record)
        for error in errors:
            print(f"ERROR: {path}: {error}", file=sys.stderr)
        failed = failed or bool(errors)
    if failed:
        return 2
    print(f"research idea evidence: {len(paths)} valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
