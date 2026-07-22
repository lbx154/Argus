"""fiction_writing runtime review gate — invoked by STAGE_CHECKS at run time.

Binds the shared literary review contract
(:mod:`argus_skill.verticals.literary.shared.review_contract`) to fiction's
finding VOCABULARY so
the review / revise stages are gated on a real contract at RUN TIME (via the
STAGE_CHECKS shell runner), not only in unit tests. This is what makes the
review->revise link a genuine runtime closed loop rather than a helper that only
tests call.

Subcommands (run from the mission dir; cwd holds ``fiction/``):

    validate   fiction/review.json
        exit 0 iff review.json conforms to the contract under fiction's vocab.
    check-plan fiction/review.json fiction/revision_plan.json
        exit 0 iff the revision plan addresses every blocking finding AND
        preserves its must_not_break invariants (assert_plan_covers).

Exit 1 with a diagnostic on any violation, so the STAGE_CHECK fails the stage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..literary.shared.review_contract import (
    ReviewError,
    assert_plan_covers,
    normalize_review,
)
from .revise import FICTION_FINDING_TYPES


def _load_json(path: str) -> object:
    p = Path(path)
    if not p.is_file():
        raise ReviewError(f"file not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{path} is not valid JSON: {exc}") from exc


def _cmd_validate(args: argparse.Namespace) -> int:
    review = normalize_review(_load_json(args.review),
                              type_vocabulary=FICTION_FINDING_TYPES)
    print(f"OK: review conforms ({len(review['findings'])} findings, "
          f"verdict={review['verdict']})")
    return 0


def _cmd_check_plan(args: argparse.Namespace) -> int:
    review = normalize_review(_load_json(args.review),
                              type_vocabulary=FICTION_FINDING_TYPES)
    plan = _load_json(args.plan)
    assert_plan_covers(review, plan)
    n_blocking = sum(1 for f in review["findings"] if f["blocking"])
    print(f"OK: revision plan covers all {n_blocking} blocking findings")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fiction-review-check")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="validate fiction/review.json")
    pv.add_argument("review")
    pv.set_defaults(func=_cmd_validate)

    pc = sub.add_parser("check-plan", help="check revision_plan covers review")
    pc.add_argument("review")
    pc.add_argument("plan")
    pc.set_defaults(func=_cmd_check_plan)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ReviewError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
