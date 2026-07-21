"""Physics auto-downgrade state machine (S->A->B->C->D).

Lets the vertical start high but converge to the tier the evidence supports,
instead of pinning the gate at Nature/Science and livelocking (the s-cbac6ede
failure). Downgrade is a change of CLAIM TYPE, never a cut in rigor. This module
is pure logic + deterministic artifact I/O over the project's ``research/`` dir;
it never edits Argus core and never blocks a stage — it PROPOSES a downgrade, writes
the four decision artifacts, applies the tier switch to ``TIER_STATE.json`` (so the
role banner switches the reviewer's bar), and directs the reviewer to ratify/object.

See ``_cockpit_v5/PHYSICS_VERTICAL_DOWNGRADE_STATE_MACHINE.md``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .tiers import is_terminal_tier, next_lower_tier, resolve_start_tier, tier_spec

# Trigger thresholds (env-tunable; defaults seeded from the s-cbac6ede run).
_THRESHOLDS = {
    "model_execute_cap": ("ARGUS_SKILL_PHYSICS_MODEL_EXECUTE_CAP", 4),
    "pivot_cap": ("ARGUS_SKILL_PHYSICS_PIVOT_CAP", 2),
    "same_diagnostic_cap": ("ARGUS_SKILL_PHYSICS_SAME_DIAGNOSTIC_CAP", 2),
    "reviewer_reject_cap": ("ARGUS_SKILL_PHYSICS_REVIEWER_REJECT_CAP", 3),
    "repeat_blocker_cap": ("ARGUS_SKILL_PHYSICS_REPEAT_BLOCKER_CAP", 3),
    "tier_cost_fraction": ("ARGUS_SKILL_PHYSICS_TIER_COST_FRACTION", 0.6),
}


def _threshold(name: str) -> float:
    env, default = _THRESHOLDS[name]
    raw = os.environ.get(env)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _research_dir(root: Path) -> Path:
    return root / "research"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _pipeline_state(root: Path) -> dict:
    return _read_json(_research_dir(root) / "PIPELINE_STATE.json") or {}


def _norm(s: object) -> str:
    return str(s or "").strip().lower()


# --------------------------------------------------------------------------- #
# Trigger computation (deterministic, over PIPELINE_STATE + on-disk artifacts). #
# --------------------------------------------------------------------------- #
def compute_triggers(project_root: object, *, cost_fraction: float | None = None) -> dict:
    """Compute the downgrade-evaluation signals. Fail-open (missing data -> 0)."""
    root = Path(str(project_root or "."))
    state = _pipeline_state(root)
    stage_hist = state.get("stage_history") or []
    rollbacks = state.get("rollback_history") or []

    # model<->execute crossings across advances + rollbacks.
    crossings = 0
    for ev in list(stage_hist) + list(rollbacks):
        a, b = _norm(ev.get("from_stage")), _norm(ev.get("to_stage"))
        if {a, b} == {"model", "execute"}:
            crossings += 1
    # novelty pivots ~ rollbacks that return to model (each needs a new formalized mechanism).
    pivots = sum(1 for ev in rollbacks if _norm(ev.get("to_stage")) == "model")
    # reviewer rejections ~ rollbacks originating at review.
    reviewer_rejects = sum(1 for ev in rollbacks if _norm(ev.get("from_stage")) == "review")
    # repeated identical blocker ~ max multiplicity of a rollback reason.
    reasons = [_norm(ev.get("reason"))[:120] for ev in rollbacks if ev.get("reason")]
    repeat_blocker = max((reasons.count(r) for r in set(reasons)), default=0)
    # same-family diagnostic falsification streak ~ failed_round2_candidates in closure status.
    closure = _read_json(root / "ROUTE_CLOSURE_STATUS.json") or {}
    same_diagnostic = len(closure.get("failed_round2_candidates") or [])
    # closure / no-go artifact already exists.
    closure_artifact = bool(
        (root / "ORIGINAL_RESEARCH_NO_GO.md").is_file()
        or (root / "ROUTE_CLOSURE_STATUS.json").is_file()
        or (_research_dir(root) / "ROUTE_CLOSURE_HANDOFF.md").is_file()
    )
    # hygiene-closure loop: an operator-wait / closure handoff exists but stage is still execute.
    hygiene_loop = (
        _norm(state.get("current_stage")) == "execute"
        and ((_research_dir(root) / "OPERATOR_WAIT_STATUS.md").is_file()
             or (_research_dir(root) / "ROUTE_CLOSURE_HANDOFF.md").is_file())
    )
    return {
        "model_execute_crossings": crossings,
        "pivots_used": pivots,
        "reviewer_rejects": reviewer_rejects,
        "repeat_blocker": repeat_blocker,
        "same_diagnostic_falsified": same_diagnostic,
        "closure_artifact_exists": closure_artifact,
        "hygiene_closure_loop": hygiene_loop,
        "cost_fraction": float(cost_fraction) if cost_fraction is not None else None,
    }


def fired_triggers(triggers: dict) -> list[str]:
    """Return the list of trigger names whose threshold is exceeded."""
    fired: list[str] = []
    if triggers.get("model_execute_crossings", 0) >= _threshold("model_execute_cap"):
        fired.append("model_execute_cap")
    if triggers.get("pivots_used", 0) >= _threshold("pivot_cap"):
        fired.append("pivot_cap")
    if triggers.get("same_diagnostic_falsified", 0) >= _threshold("same_diagnostic_cap"):
        fired.append("same_diagnostic_cap")
    if triggers.get("reviewer_rejects", 0) >= _threshold("reviewer_reject_cap"):
        fired.append("reviewer_reject_cap")
    if triggers.get("repeat_blocker", 0) >= _threshold("repeat_blocker_cap"):
        fired.append("repeat_blocker_cap")
    if triggers.get("closure_artifact_exists"):
        fired.append("closure_artifact_exists")
    if triggers.get("hygiene_closure_loop"):
        fired.append("hygiene_closure_loop")
    cf = triggers.get("cost_fraction")
    if cf is not None and cf >= _threshold("tier_cost_fraction"):
        fired.append("tier_cost_fraction")
    return fired


# --------------------------------------------------------------------------- #
# Tier state.                                                                  #
# --------------------------------------------------------------------------- #
def _tier_state_path(root: Path) -> Path:
    return _research_dir(root) / "TIER_STATE.json"


def read_current_tier(project_root: object) -> str:
    root = Path(str(project_root or "."))
    st = _read_json(_tier_state_path(root))
    if st and st.get("current_tier"):
        return str(st["current_tier"]).strip().upper()
    return resolve_start_tier()


def _write_tier_state(root: Path, current_tier: str, *, last_decision: dict | None, now_iso: str | None) -> None:
    payload = {"current_tier": current_tier, "start_tier": resolve_start_tier()}
    if last_decision is not None:
        payload["last_downgrade"] = last_decision
    if now_iso:
        payload["updated_utc"] = now_iso
    _atomic_write(_tier_state_path(root), json.dumps(payload, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- #
# Decision + artifact emission.                                               #
# --------------------------------------------------------------------------- #
def evaluate_and_maybe_downgrade(
    project_root: object, *, cost_fraction: float | None = None, now_iso: str | None = None,
) -> dict | None:
    """Evaluate triggers and, if warranted, downgrade one rung + write the 4 artifacts.

    Returns the decision dict (also written to DOWNGRADE_DECISION.json) or ``None``
    when no downgrade fires. Never raises on bad inputs (fail-open -> None).
    """
    root = Path(str(project_root or "."))
    try:
        current = read_current_tier(root)
        if is_terminal_tier(current):
            return None  # already at D; nothing lower.
        triggers = compute_triggers(root, cost_fraction=cost_fraction)
        fired = fired_triggers(triggers)
        # Mandatory when >=2 triggers fire, or any single hard trigger.
        hard = {"pivot_cap", "model_execute_cap", "closure_artifact_exists", "hygiene_closure_loop"}
        if not fired or (len(fired) < 2 and not (set(fired) & hard)):
            return None
        to_tier = next_lower_tier(current)
        if not to_tier:
            return None
        spec = tier_spec(to_tier)
        decision = {
            "from_tier": current,
            "to_tier": to_tier,
            "decided_at": now_iso or "",
            "decided_by": "physics-vertical-downgrade-gate",
            "triggers_fired": fired,
            "trigger_values": triggers,
            "new_claim_type": spec.claim_types[0] if spec else "",
            "rigor_unchanged": True,
            "reviewer_adjudication_required": True,
            "operator_authorization_required": bool(spec.operator_auth_required) if spec else False,
        }
        _emit_decision_artifacts(root, decision, spec)
        _write_tier_state(root, to_tier, last_decision=decision, now_iso=now_iso)
        return decision
    except Exception:  # noqa: BLE001 — a downgrade probe must never break the stage
        return None


def _emit_decision_artifacts(root: Path, decision: dict, spec) -> None:
    _atomic_write(_research_dir(root) / "DOWNGRADE_DECISION.json",
                  json.dumps(decision, indent=2, sort_keys=True))
    _atomic_write(_research_dir(root) / "DOWNGRADE_RATIONALE.md", _render_rationale(decision, spec))
    _atomic_write(_research_dir(root) / "UPDATED_CLAIM_SCOPE.md", _render_claim_scope(decision, spec))
    _atomic_write(_research_dir(root) / "NEXT_ROLE_DIRECTIVE.json",
                  json.dumps(_next_role_directive(decision, spec), indent=2, sort_keys=True))


def _render_rationale(decision: dict, spec) -> str:
    tv = decision.get("trigger_values", {})
    lines = [
        f"# Downgrade rationale — Tier {decision['from_tier']} -> {decision['to_tier']}", "",
        f"Triggers fired: {', '.join(decision.get('triggers_fired', [])) or '(none)'}", "",
        "## Trigger values",
    ]
    for k, v in tv.items():
        lines.append(f"- {k}: {v}")
    lines += [
        "", "## Why the higher tier is unsupported",
        f"The evidence to date does not meet the Tier-{decision['from_tier']} bar; the run has "
        "exhausted the allowed effort at this tier (see triggers). Continuing at the higher tier "
        "would only repeat model<->execute churn or idle-hold.", "",
        f"## New claim type (Tier {decision['to_tier']})",
        f"- {decision.get('new_claim_type', '')}",
        "", "## Rigor statement",
        "Rigor is UNCHANGED: reproduced baseline, preregistered diagnostics, held-out tests, "
        "convergence checks, honest bounds, and provenance all still apply. This is a change of "
        "claim TYPE, not a relaxation of standards.",
    ]
    return "\n".join(lines) + "\n"


def _render_claim_scope(decision: dict, spec) -> str:
    if spec is None:
        return f"# Updated claim scope — Tier {decision['to_tier']}\n"
    return (
        f"# Updated claim scope — Tier {decision['to_tier']} ({spec.name})\n\n"
        f"## Acceptable claim types\n" + "".join(f"- {c}\n" for c in spec.claim_types) +
        f"\n## Minimum evidence\n" + "".join(f"- {c}\n" for c in spec.evidence_requirements) +
        f"\n## Numerical requirements\n" + "".join(f"- {c}\n" for c in spec.numerical_requirements) +
        f"\n## Reviewer gate (apply THIS tier only)\n- {spec.reviewer_gate}\n"
        f"\n## Manuscript gate\n- {spec.manuscript_gate}\n"
        f"\n## Stop chasing higher when\n- {spec.stop_chasing_higher_when}\n"
    )


def _next_role_directive(decision: dict, spec) -> dict:
    to_tier = decision["to_tier"]
    if to_tier == "D":
        return {
            "responsible_role": "Engineer",
            "required_action": "Assemble the systematic no-go evidence table; do NOT invent a new mechanism",
            "expected_next_stage": "review",
            "acceptance_test": "no-go evidence table + reproduced baseline + held-out results present and gate-passing",
            "do_not_do": ["propose a new diagnostic", "run another novelty pivot", "generic hygiene closure"],
            "reviewer_adjudication": "Ratify this downgrade or object with evidence; then evaluate against the Tier-D bar only.",
        }
    return {
        "responsible_role": "Engineer",
        "required_action": f"Pursue the Tier-{to_tier} claim per UPDATED_CLAIM_SCOPE.md",
        "expected_next_stage": "execute",
        "acceptance_test": f"a Tier-{to_tier} claim supported by the required evidence",
        "do_not_do": ["keep chasing the higher-tier claim", "lower rigor"],
        "reviewer_adjudication": f"Ratify this downgrade or object with evidence; then evaluate against the Tier-{to_tier} bar only.",
    }


__all__ = [
    "compute_triggers", "fired_triggers", "read_current_tier",
    "evaluate_and_maybe_downgrade",
]
