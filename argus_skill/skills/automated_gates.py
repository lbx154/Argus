"""Automated gates — F3/F4 integration into the per-round check pipeline.

This module is the bridge between the standalone validators
(:mod:`argus_skill.skills.evidence_chain` F4,
:mod:`argus_skill.skills.anti_mediocrity` F3) and the existing per-round
check protocol in :mod:`argus_skill.tools.stage_check` /
:mod:`argus_skill.engineer.checks`.

For each pipeline stage, ``gates_for_stage(stage)`` returns the list of
automated gates that should run. The reviewer agent reads the resulting
findings as additional evidence in its prompt; failed gates inject
specific, actionable failures rather than vague "needs more work".

Stage-to-gate map (matches stage_check semantics):

============== =====================================================
stage          gates run
============== =====================================================
research       — (no evidence to validate yet)
plan           — (no evidence to validate yet)
benchmark      — (artifacts being prepared, not yet citable)
run            anti_mediocrity (baseline reproduction, Δ ≥ min_delta,
               benchmark diversity)
analysis       evidence_chain, anti_mediocrity
draft          evidence_chain
review         evidence_chain, anti_mediocrity
submission     evidence_chain, anti_mediocrity
============== =====================================================

Stages without configured gates return an empty list. Callers should
treat that as "the agent layer is the only check at this stage" — the
absence of an automated gate is NOT a pass.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .anti_mediocrity import (
    DEFAULT_MIN_DELTA,
    DEFAULT_MIN_FAMILIES,
    run_anti_mediocrity_gate,
)
from .evidence_chain import validate_evidence_chain


GateName = Literal["evidence_chain", "anti_mediocrity"]


# Stage → gates that apply.
STAGE_GATES: dict[str, tuple[GateName, ...]] = {
    "research": (),
    "plan": (),
    "benchmark": (),
    "run": ("anti_mediocrity",),
    "analysis": ("evidence_chain", "anti_mediocrity"),
    "draft": ("evidence_chain",),
    "review": ("evidence_chain", "anti_mediocrity"),
    "submission": ("evidence_chain", "anti_mediocrity"),
}


@dataclass
class GateResult:
    """Result of one gate. Mirrors the shape of ``CheckResult`` so it can
    be folded into the reviewer's existing check list, but stays a
    plain-Python dataclass so it doesn't pull in engineer/runner imports."""

    name: GateName
    passed: bool
    summary: str
    detail: str

    def to_text_block(self) -> str:
        head = "PASS" if self.passed else "FAIL"
        return (
            f"[{head}] gate:{self.name} — {self.summary}\n"
            f"{self.detail}".rstrip()
        )


def gates_for_stage(stage: str) -> tuple[GateName, ...]:
    """Return the gates that should run at ``stage``. Unknown stages → ()."""
    return STAGE_GATES.get(stage, ())


def _run_evidence_chain(project_root: Path) -> GateResult:
    report = validate_evidence_chain(project_root)
    if report.ok:
        return GateResult(
            name="evidence_chain",
            passed=True,
            summary=(
                f"all {report.claims_checked} claim(s) and "
                f"{report.bundles_checked} bundle(s) resolve cleanly"
            ),
            detail="",
        )
    # Group by code for compact reviewer-friendly output.
    by_code: dict[str, list] = {}
    for issue in report.issues:
        by_code.setdefault(issue.code, []).append(issue)
    lines = [
        f"{len(report.issues)} chain integrity issue(s); "
        f"draft cannot advance to submission until fixed:",
    ]
    for code in sorted(by_code):
        bucket = by_code[code]
        lines.append(f"  [{code}] x{len(bucket)}")
        for issue in bucket[:5]:  # cap at 5 per code to keep prompt small
            head = issue.claim_id or "<no-claim>"
            tail = f" ({issue.evidence_path})" if issue.evidence_path else ""
            lines.append(f"    - {head}{tail}: {issue.detail}")
        if len(bucket) > 5:
            lines.append(f"    ... and {len(bucket) - 5} more")
    return GateResult(
        name="evidence_chain",
        passed=False,
        summary=(
            f"{len(report.issues)} chain issue(s) across "
            f"{report.claims_checked} claim(s)"
        ),
        detail="\n".join(lines),
    )


def _run_anti_mediocrity(
    project_root: Path,
    *,
    proposed_condition: str | None,
    baseline_condition: str | None,
    min_delta: float,
    min_families: int,
) -> GateResult:
    report = run_anti_mediocrity_gate(
        project_root,
        proposed_condition=proposed_condition,
        baseline_condition=baseline_condition,
        min_delta=min_delta,
        min_families=min_families,
    )
    if report.ok:
        agg_count = len(report.aggregates)
        return GateResult(
            name="anti_mediocrity",
            passed=True,
            summary=(
                f"baseline reproduced, improvement ≥{min_delta}, "
                f"≥{min_families} benchmark families (across {agg_count} aggregates)"
            ),
            detail="",
        )
    lines = [
        f"{len(report.issues)} anti-mediocrity gate failure(s):",
    ]
    for issue in report.issues:
        prefix = f"  [{issue.code}] {issue.detail}"
        if issue.measured is not None and issue.threshold is not None:
            prefix += f" (measured={issue.measured}, threshold={issue.threshold})"
        lines.append(prefix)
    if not proposed_condition or not baseline_condition:
        lines.append(
            "  (note: --proposed-condition and --baseline-condition were "
            "not supplied; only benchmark-diversity was checked. Pass them "
            "to run baseline reproduction and Δ-reward gates.)"
        )
    return GateResult(
        name="anti_mediocrity",
        passed=False,
        summary=f"{len(report.issues)} gate failure(s)",
        detail="\n".join(lines),
    )


def run_stage_gates(
    project_root: Path,
    *,
    stage: str,
    proposed_condition: str | None = None,
    baseline_condition: str | None = None,
    min_delta: float = DEFAULT_MIN_DELTA,
    min_families: int = DEFAULT_MIN_FAMILIES,
) -> list[GateResult]:
    """Run every gate applicable to ``stage``. Returns one ``GateResult``
    per gate. Empty list means no gates apply at this stage."""
    gates = gates_for_stage(stage)
    results: list[GateResult] = []
    for gate in gates:
        if gate == "evidence_chain":
            results.append(_run_evidence_chain(project_root))
        elif gate == "anti_mediocrity":
            results.append(
                _run_anti_mediocrity(
                    project_root,
                    proposed_condition=proposed_condition,
                    baseline_condition=baseline_condition,
                    min_delta=min_delta,
                    min_families=min_families,
                )
            )
    return results


def all_passed(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)


def format_results(results: list[GateResult]) -> str:
    if not results:
        return ""
    return "\n\n".join(r.to_text_block() for r in results)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--stage",
        type=str,
        required=True,
        help="Pipeline stage (research/plan/benchmark/run/analysis/draft/review/submission).",
    )
    parser.add_argument("--proposed-condition", type=str, default=None)
    parser.add_argument("--baseline-condition", type=str, default=None)
    parser.add_argument(
        "--min-delta", type=float, default=DEFAULT_MIN_DELTA
    )
    parser.add_argument(
        "--min-benchmark-families", type=int, default=DEFAULT_MIN_FAMILIES
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results = run_stage_gates(
        args.project_root,
        stage=args.stage,
        proposed_condition=args.proposed_condition,
        baseline_condition=args.baseline_condition,
        min_delta=args.min_delta,
        min_families=args.min_benchmark_families,
    )

    if args.json:
        payload = {
            "stage": args.stage,
            "gates_run": [r.name for r in results],
            "all_passed": all_passed(results),
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "summary": r.summary,
                    "detail": r.detail,
                }
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        if not results:
            print(
                f"No automated gates configured for stage {args.stage!r}; "
                f"reviewer-only review for this stage."
            )
        else:
            print(format_results(results))

    return 0 if all_passed(results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
