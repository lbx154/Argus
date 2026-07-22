"""Auto-downgrade gate (execute + review) — ADVISORY.

Thin CLI wrapper over ``verticals.physics.downgrade``: each round it evaluates the
downgrade triggers and, when warranted, proposes+applies a one-rung downgrade
(S->A->B->C->D), writes the four decision artifacts, and surfaces a reviewer
ratification directive via the shared research-gate repair machinery + gate-fail
feedback (so the physics ``role_banner`` injects it). Never blocks a stage; never
edits Argus core. Downgrade is a change of claim TYPE, not a cut in rigor.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from ....skills.research_gates import clear_gate_state, update_gate_state, write_gate_outputs
from ..downgrade import (
    compute_triggers,
    evaluate_and_maybe_downgrade,
    fired_triggers,
    read_current_tier,
)
from ..gate_feedback import build_feedback, clear_feedback, write_feedback

GATE_ID = "downgrade"
STAGE = "review"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_gate(project_root: object, *, now_iso: str | None = None) -> tuple[bool, list[dict]]:
    root = Path(str(project_root or "."))
    before_tier = read_current_tier(root)
    triggers = compute_triggers(root)
    fired = fired_triggers(triggers)
    decision = evaluate_and_maybe_downgrade(root, now_iso=now_iso or _now_iso())

    failures: list[dict] = []
    if decision is not None:
        # A downgrade fired — surface the ratify directive to the reviewer as a
        # (non-blocking) research-gate repair item so role_banner injects it.
        failures = [{
            "failure_id": "DWN-001", "severity": "major", "stage": STAGE,
            "artifact": "DOWNGRADE_DECISION.json", "field": "tier",
            "message": (f"Tier downgraded {decision['from_tier']} -> {decision['to_tier']} "
                        f"(triggers: {', '.join(decision['triggers_fired'])}). Reviewer must ratify."),
            "required_action": ("Reviewer: ratify this downgrade (or object with evidence) and evaluate "
                                f"against the Tier-{decision['to_tier']} bar in UPDATED_CLAIM_SCOPE.md only."),
            "blocks_progress": False,
        }]
        rec = build_feedback(
            gate_id="downgrade", gate_name="Tier downgrade (reviewer ratification)",
            failed_stage=STAGE, responsible_role="Reviewer", blocking_level="advisory",
            exact_blocker=f"tier {decision['from_tier']} unsupported; downgraded to {decision['to_tier']}",
            evidence_checked=["PIPELINE_STATE.json", "ROUTE_CLOSURE_STATUS.json", "DOWNGRADE_DECISION.json"],
            required_action=("Ratify the downgrade or object with evidence; then evaluate ONLY against the "
                             f"Tier-{decision['to_tier']} bar (do not re-apply the old tier)."),
            acceptance_test=f"reviewer verdict evaluates against Tier {decision['to_tier']}; no old-tier bar re-applied",
            next_role_directive=decision,
            downgrade_trigger=", ".join(decision["triggers_fired"]),
            do_not_do=["re-apply the higher tier's bar", "reduce rigor"],
            suggested_files_to_edit_or_create=["UPDATED_CLAIM_SCOPE.md", "REVIEW.md"],
            expected_next_stage=("review" if decision["to_tier"] == "D" else "execute"),
        )
        write_feedback(root, rec)
    else:
        clear_feedback(root, "downgrade")

    after_tier = read_current_tier(root)
    result = {
        "gate_id": GATE_ID, "stage": STAGE, "advisory": True,
        "tier_before": before_tier, "tier_after": after_tier,
        "triggers": triggers, "fired": fired,
        "downgraded": decision is not None,
        "decision": decision,
    }
    passed = decision is None
    write_gate_outputs(root, GATE_ID, result=result, failures=failures,
                       human_review=_render_review(before_tier, after_tier, triggers, fired, decision))
    if failures:
        update_gate_state(root, GATE_ID, failures)
    else:
        clear_gate_state(root, GATE_ID)
    return passed, failures


def _render_review(before, after, triggers, fired, decision) -> str:  # noqa: ANN001
    lines = [
        "# Auto-downgrade gate review (advisory)", "",
        f"Tier: {before} -> {after}  |  triggers fired: {', '.join(fired) or '(none)'}", "",
        "## Trigger values",
    ]
    for k, v in triggers.items():
        lines.append(f"- {k}: {v}")
    if decision:
        lines += ["", f"## Downgrade applied: {decision['from_tier']} -> {decision['to_tier']}",
                  f"New claim type: {decision.get('new_claim_type', '')}",
                  "Reviewer MUST ratify and switch to the new-tier bar (UPDATED_CLAIM_SCOPE.md). "
                  "Rigor is unchanged."]
    else:
        lines += ["", "No downgrade this round (thresholds not met)."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-downgrade-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true")
    args = parser.parse_args(argv)
    passed, failures = run_gate(args.project_root)
    if passed:
        print("downgrade gate: no downgrade this round")
        return 0
    for f in failures:
        print(f"  - {f['failure_id']} {f['message']}", file=sys.stderr)
    return 0  # always advisory


__all__ = ["GATE_ID", "STAGE", "run_gate", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
