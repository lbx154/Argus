"""Stage → gates router (post-c6b11d3 rewrite).

The harness runs two kinds of per-round checks:

* **Structural** gates (kind=``structural``) — anti-fraud / provenance
  enforcement. Failure means the artifacts themselves are broken
  (missing files, dangling claims, forged citations). These DO block
  the round via stage_check exit code; the reviewer cannot overrule
  them, and shouldn't want to. Today: ``evidence_chain`` (F4).

* **Advisory** findings (kind=``advisory``) — surface facts to the
  reviewer's prompt without making a verdict. The harness never blocks
  a round on an advisory finding; the reviewer reads the numbers and
  rules. Today: ``mediocrity_finding`` (formerly F3).

The distinction is enforced by ``GateResult.kind`` + ``stage_check.py``:
only structural failures count into the exit code.

Why this matters: the earlier F3 (``c6b11d3``) hard-coded research-quality
thresholds (``min_delta=0.02``, ``min_families>=3``) and counted them into
exit code, so the harness was secretly making research-quality verdicts.
That was rejected in review ``review/2026-06-01-research-factory-gates-
c6b11d3.md`` per the design philosophy "harness 没有 agent 自己聪明"
(README "设计哲学" + nssmd/skills/04-harness-vs-agent-boundary.md).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .anti_mediocrity import (
    collect_mediocrity_finding,
    format_finding,
)
from .evidence_chain import validate_evidence_chain


GateName = Literal["evidence_chain", "mediocrity_finding"]
GateKind = Literal["structural", "advisory"]


# Stage → gates that apply.
STAGE_GATES: dict[str, tuple[GateName, ...]] = {
    "research": (),
    "plan": (),
    "benchmark": (),
    "run": ("mediocrity_finding",),
    "analysis": ("evidence_chain", "mediocrity_finding"),
    "draft": ("evidence_chain",),
    "review": ("evidence_chain", "mediocrity_finding"),
    "submission": ("evidence_chain", "mediocrity_finding"),
}


# Which gates are structural (allowed to block via exit code) vs
# advisory (always exit 0; reviewer rules). Single source of truth.
GATE_KINDS: dict[GateName, GateKind] = {
    "evidence_chain": "structural",
    "mediocrity_finding": "advisory",
}


@dataclass
class GateResult:
    """Result of one gate.

    ``kind`` is the source-of-truth for whether the supervisor should
    treat this as a hard block:

    * ``structural`` + ``passed=False`` → block the round
    * ``advisory`` + anything → never block the round; just surface text

    ``passed`` is meaningless for advisory findings (kind=='advisory')
    and is forced to True by the runner so callers don't accidentally
    short-circuit on advisory output. The reviewer reads the ``detail``
    body and rules.
    """

    name: GateName
    kind: GateKind
    passed: bool
    summary: str
    detail: str

    @property
    def is_blocking(self) -> bool:
        """True iff a stage-check caller should count this into exit code."""
        return self.kind == "structural" and not self.passed

    def to_text_block(self) -> str:
        if self.kind == "advisory":
            head = "ADVISORY"
        else:
            head = "PASS" if self.passed else "FAIL"
        return (
            f"[{head}] gate:{self.name} — {self.summary}\n"
            f"{self.detail}".rstrip()
        )


def gates_for_stage(stage: str) -> tuple[GateName, ...]:
    """Return the gates that should run at ``stage``. Unknown stages → ()."""
    return STAGE_GATES.get(stage, ())


def _run_evidence_chain(project_root: Path) -> GateResult:
    """Structural gate: claim → evidence → bundle chain must be intact."""
    report = validate_evidence_chain(project_root)
    if report.ok:
        return GateResult(
            name="evidence_chain",
            kind="structural",
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
        kind="structural",
        passed=False,
        summary=(
            f"{len(report.issues)} chain issue(s) across "
            f"{report.claims_checked} claim(s)"
        ),
        detail="\n".join(lines),
    )


def _run_mediocrity_finding(
    project_root: Path,
    *,
    proposed_condition: str | None,
    baseline_condition: str | None,
) -> GateResult:
    """Advisory finding: surface evidence facts; reviewer rules on quality.

    Never blocks. Even if the underlying read had a structural error,
    the harness only logs it; the reviewer prompt always sees whatever
    facts were extractable.
    """
    finding = collect_mediocrity_finding(
        project_root,
        proposed_condition=proposed_condition,
        baseline_condition=baseline_condition,
    )
    n_agg = len(finding.aggregates)
    n_fam = len(finding.benchmark_families)
    delta = finding.proposed_minus_baseline
    bits = [
        f"{n_agg} aggregate(s)",
        f"{n_fam} benchmark family/families",
    ]
    if delta is not None:
        bits.append(f"Δreward={delta:+.4f}")
    if finding.structural_errors:
        bits.append(f"{len(finding.structural_errors)} read error(s)")
    summary = "; ".join(bits)
    return GateResult(
        name="mediocrity_finding",
        kind="advisory",
        passed=True,  # advisory never blocks; this field is meaningless here
        summary=summary,
        detail=format_finding(finding),
    )


def run_stage_gates(
    project_root: Path,
    *,
    stage: str,
    proposed_condition: str | None = None,
    baseline_condition: str | None = None,
) -> list[GateResult]:
    """Run every gate applicable to ``stage``. Returns one ``GateResult``
    per gate. Empty list means no gates apply at this stage."""
    gates = gates_for_stage(stage)
    results: list[GateResult] = []
    for gate in gates:
        if gate == "evidence_chain":
            results.append(_run_evidence_chain(project_root))
        elif gate == "mediocrity_finding":
            results.append(
                _run_mediocrity_finding(
                    project_root,
                    proposed_condition=proposed_condition,
                    baseline_condition=baseline_condition,
                )
            )
    return results


def any_blocking_failure(results: list[GateResult]) -> bool:
    """True iff at least one *structural* gate failed.

    This is the supervisor-facing question. Advisory failures (kind ==
    'advisory') are never counted: they exist only to inform the reviewer.
    """
    return any(r.is_blocking for r in results)


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
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results = run_stage_gates(
        args.project_root,
        stage=args.stage,
        proposed_condition=args.proposed_condition,
        baseline_condition=args.baseline_condition,
    )

    if args.json:
        payload = {
            "stage": args.stage,
            "gates_run": [r.name for r in results],
            "structural_block": any_blocking_failure(results),
            "results": [
                {
                    "name": r.name,
                    "kind": r.kind,
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

    # ONLY structural failures count into exit code. Advisory findings
    # never block; reviewer reads them and decides.
    return 1 if any_blocking_failure(results) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
