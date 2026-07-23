"""Structured gate-fail feedback protocol for the physics vertical.

Today a gate failure mostly says *that* it failed. This module makes every physics
gate emit a **role-addressed, actionable** record so the next agent round knows WHO
must act, WHAT to do, and HOW success is checked — killing the s-cbac6ede idle
hygiene loops. Records are written to ``research/GATE_FAIL_<gate_id>.json`` and
rendered into role banners (via the physics ``role_banner``); they are advisory and
never edit Argus core.

See ``_cockpit_v5/GATE_FAIL_FEEDBACK_PROTOCOL_DESIGN.md``.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Every gate-fail record MUST carry exactly these fields.
FEEDBACK_FIELDS: tuple[str, ...] = (
    "gate_id", "gate_name", "failed_stage", "responsible_role", "blocking_level",
    "exact_blocker", "evidence_checked", "missing_artifact", "missing_field",
    "required_action", "next_role_directive", "acceptance_test", "max_retry",
    "downgrade_trigger", "if_operator_required_then_prompt", "do_not_do",
    "suggested_files_to_edit_or_create", "suggested_commands", "expected_next_stage",
)

ROLES = ("Planner", "Manager", "Engineer", "Reviewer", "ManuscriptBuilder", "Operator")
BLOCKING_LEVELS = ("hard", "soft", "advisory", "operator_required")


def build_feedback(
    *,
    gate_id: str,
    gate_name: str,
    failed_stage: str,
    responsible_role: str,
    blocking_level: str,
    exact_blocker: str,
    required_action: str,
    acceptance_test: str,
    next_role_directive: dict | None = None,
    evidence_checked: list | None = None,
    missing_artifact: object = None,
    missing_field: object = None,
    max_retry: int = 1,
    downgrade_trigger: str = "",
    if_operator_required_then_prompt: str = "",
    do_not_do: list | None = None,
    suggested_files_to_edit_or_create: list | None = None,
    suggested_commands: list | None = None,
    expected_next_stage: str = "",
) -> dict:
    """Assemble a complete, schema-valid gate-fail feedback record."""
    if responsible_role not in ROLES:
        raise ValueError(f"responsible_role must be one of {ROLES}; got {responsible_role!r}")
    if blocking_level not in BLOCKING_LEVELS:
        raise ValueError(f"blocking_level must be one of {BLOCKING_LEVELS}; got {blocking_level!r}")
    rec = {
        "gate_id": gate_id,
        "gate_name": gate_name,
        "failed_stage": failed_stage,
        "responsible_role": responsible_role,
        "blocking_level": blocking_level,
        "exact_blocker": exact_blocker,
        "evidence_checked": list(evidence_checked or []),
        "missing_artifact": missing_artifact,
        "missing_field": missing_field,
        "required_action": required_action,
        "next_role_directive": dict(next_role_directive or {}),
        "acceptance_test": acceptance_test,
        "max_retry": int(max_retry),
        "downgrade_trigger": downgrade_trigger,
        "if_operator_required_then_prompt": if_operator_required_then_prompt,
        "do_not_do": list(do_not_do or []),
        "suggested_files_to_edit_or_create": list(suggested_files_to_edit_or_create or []),
        "suggested_commands": list(suggested_commands or []),
        "expected_next_stage": expected_next_stage,
    }
    validate_feedback(rec)
    return rec


def validate_feedback(rec: dict) -> None:
    """Raise ValueError if the record is missing any required field."""
    missing = [f for f in FEEDBACK_FIELDS if f not in rec]
    if missing:
        raise ValueError(f"gate-fail feedback missing fields: {missing}")


def write_feedback(project_root: object, rec: dict) -> Path:
    validate_feedback(rec)
    root = Path(str(project_root or "."))
    rdir = root / "research"
    rdir.mkdir(parents=True, exist_ok=True)
    path = rdir / f"GATE_FAIL_{rec['gate_id']}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def render_feedback_block(rec: dict) -> str:
    """Render an imperative prompt block addressed to the responsible role."""
    d = rec.get("next_role_directive") or {}
    lines = [
        f"## GATE FEEDBACK — {rec.get('gate_name', rec.get('gate_id'))} "
        f"[{rec.get('blocking_level')}]  (stage: {rec.get('failed_stage')})",
        f"RESPONSIBLE ROLE: **{rec.get('responsible_role')}** — this block is for YOU.",
        f"BLOCKER: {rec.get('exact_blocker')}",
        f"REQUIRED ACTION: {rec.get('required_action')}",
        f"ACCEPTANCE TEST: {rec.get('acceptance_test')}",
        f"EXPECTED NEXT STAGE: {rec.get('expected_next_stage') or '(unchanged)'}",
    ]
    if d:
        lines.append(f"NEXT-ROLE DIRECTIVE: {json.dumps(d, ensure_ascii=False)}")
    if rec.get("do_not_do"):
        lines.append("DO NOT: " + "; ".join(rec["do_not_do"]))
    if rec.get("suggested_files_to_edit_or_create"):
        lines.append("SUGGESTED FILES: " + ", ".join(rec["suggested_files_to_edit_or_create"]))
    if rec.get("suggested_commands"):
        lines.append("SUGGESTED COMMANDS: " + " ; ".join(rec["suggested_commands"]))
    if rec.get("downgrade_trigger"):
        lines.append(f"DOWNGRADE TRIGGER: {rec['downgrade_trigger']}")
    if rec.get("blocking_level") == "operator_required" and rec.get("if_operator_required_then_prompt"):
        lines.append("OPERATOR PROMPT: " + rec["if_operator_required_then_prompt"])
    return "\n".join(lines)


def render_active_feedback(project_root: object) -> str:
    """Render every active ``research/GATE_FAIL_*.json`` for role-banner injection."""
    rdir = Path(str(project_root or ".")) / "research"
    if not rdir.is_dir():
        return ""
    blocks: list[str] = []
    for path in sorted(rdir.glob("GATE_FAIL_*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and "gate_id" in rec:
            blocks.append(render_feedback_block(rec))
    return "\n\n".join(blocks)


def clear_feedback(project_root: object, gate_id: str) -> None:
    try:
        (Path(str(project_root or ".")) / "research" / f"GATE_FAIL_{gate_id}.json").unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# The six special-case builders.                                              #
# --------------------------------------------------------------------------- #
def feedback_diagnostic_win_false(*, tier: str, pivots_used: int, pivot_cap: int) -> dict:
    """SC2: diagnostic method win=false. Pivot if pivots remain at S/A; else downgrade."""
    exhausted = pivots_used >= pivot_cap
    if exhausted:
        return build_feedback(
            gate_id="diagnostic_win", gate_name="Diagnostic method win", failed_stage="execute",
            responsible_role="Reviewer", blocking_level="advisory",
            exact_blocker=f"no method win after {pivots_used}/{pivot_cap} pivots (cap exhausted)",
            evidence_checked=["CLAIMS.csv", "ROUTE_CLOSURE_STATUS.json", "diagnostic result files"],
            required_action="Adjudicate a tier downgrade (A->B->C->D); do NOT invent another mechanism",
            next_role_directive={"responsible_role": "Reviewer", "action": "accept/reject downgrade per UPDATED_CLAIM_SCOPE.md"},
            acceptance_test="a reviewer-accepted DOWNGRADE_DECISION.json exists and the tier bar switches",
            max_retry=0, downgrade_trigger="pivot cap exhausted",
            do_not_do=["invent another mechanism", "hold silently", "run another pivot"],
            expected_next_stage="review",
        )
    return build_feedback(
        gate_id="diagnostic_win", gate_name="Diagnostic method win", failed_stage="execute",
        responsible_role="Engineer", blocking_level="soft",
        exact_blocker=f"no method win yet ({pivots_used}/{pivot_cap} pivots used)",
        evidence_checked=["CLAIMS.csv", "diagnostic result files"],
        required_action="Run the next preregistered novelty pivot with held-out validation",
        next_role_directive={"responsible_role": "Engineer", "action": "execute the next preregistered pivot"},
        acceptance_test="a new diagnostic beats the reproduced baseline on held-out data",
        max_retry=pivot_cap - pivots_used, downgrade_trigger=f"downgrade at pivot cap {pivot_cap}",
        do_not_do=["skip held-out validation", "claim a win without beating baseline"],
        expected_next_stage="execute",
    )


def feedback_hygiene_closure_loop(*, blocker_hint: str = "") -> dict:
    """SC3: hygiene closure complete but stage stays execute -> closure-loop risk."""
    return build_feedback(
        gate_id="closure_loop", gate_name="Hygiene closure-loop", failed_stage="execute",
        responsible_role="Manager", blocking_level="hard",
        exact_blocker="a hygiene/closure round returned clean but produced no new physics and the stage did not advance",
        evidence_checked=["events.jsonl (repeated hygiene rounds)", "PIPELINE_STATE.json (stage unchanged)"],
        required_action="State the SINGLE unique remaining blocker, then advance or trigger a downgrade" + (f" (hint: {blocker_hint})" if blocker_hint else ""),
        next_role_directive={"responsible_role": "Manager", "action": "name one blocker or advance/downgrade"},
        acceptance_test="stage advances OR a downgrade evaluation is emitted; no further generic hygiene round",
        max_retry=0, downgrade_trigger="repeated hygiene without progress",
        do_not_do=["run another oversized-file/telemetry scan", "dispatch a generic hygiene round"],
        expected_next_stage="review",
    )


def feedback_loop_detected(*, stage: str, blocker: str, repeats: int) -> dict:
    """SC4: reviewer evidence accepted but stage_decider repeats the same stage."""
    return build_feedback(
        gate_id="loop_detected", gate_name="Stage loop detected", failed_stage=stage,
        responsible_role="Reviewer", blocking_level="hard",
        exact_blocker=f"same (stage={stage}, blocker={blocker!r}) recurred {repeats}x with reviewer evidence unchanged",
        evidence_checked=["events.jsonl stage_decision history", "reviewer verdicts"],
        required_action="Force a downgrade evaluation OR request one explicit operator decision — not another identical round",
        next_role_directive={"responsible_role": "Reviewer", "action": "downgrade evaluation", "fallback": "operator decision"},
        acceptance_test="a DOWNGRADE_DECISION.json or an OPERATOR_AUTHORIZATION_REQUEST.json is emitted",
        max_retry=0, downgrade_trigger=f"loop repeats>={repeats}",
        do_not_do=["repeat the same stage decision", "re-dispatch the same round"],
        expected_next_stage="review",
    )


def feedback_provider_fence(*, sub_case: str) -> dict:
    """SC5: provider fence / budget — classify and name the next step precisely."""
    table = {
        "partial_pricing": ("advisory", "Engineer",
                            "pricing_status=partial recorded as unpriced_skipped (already non-blocking)",
                            "continue; no action (fixed in place)", ""),
        "per_call_overrun": ("operator_required", "Operator",
                            "per-call reservation cap breached (overrun_usd>0) + cooldown",
                            "raise ARGUS_SKILL_PER_CALL_CAP_USD (env) and compact context",
                            "Raise the per-call cap and/or enable context compaction? [config patch]"),
        "mission_budget": ("operator_required", "Operator",
                            "per-mission item budget exhausted (spent>=cap)",
                            "raise ARGUS_SKILL_MISSION_BUDGET_USD (keep <= daily) or accept stop",
                            "Mission budget exhausted. Raise MISSION_BUDGET_USD (<= daily) or stop? [config | stop]"),
        "daily_global_cap": ("hard", "Operator",
                            "daily/global provider cap hit",
                            "WAIT — do not disable caps", "Daily/global cap hit. Wait for reset. [wait]"),
    }
    if sub_case not in table:
        raise ValueError(f"unknown provider-fence sub_case: {sub_case!r}")
    level, role, blocker, action, prompt = table[sub_case]
    return build_feedback(
        gate_id=f"provider_fence_{sub_case}", gate_name="Provider fence / budget", failed_stage="execute",
        responsible_role=role, blocking_level=level, exact_blocker=blocker,
        evidence_checked=["cost-control.json", "usage.jsonl", "budget.reservation.settled events"],
        required_action=action,
        next_role_directive={"responsible_role": role, "action": action},
        acceptance_test="the classified next step (wait/resume/config patch) is taken; run continues or stops cleanly",
        max_retry=0, downgrade_trigger="",
        if_operator_required_then_prompt=prompt,
        do_not_do=["disable real caps", "set unlimited budget"],
        expected_next_stage="execute",
    )


__all__ = [
    "FEEDBACK_FIELDS", "ROLES", "BLOCKING_LEVELS", "build_feedback", "validate_feedback",
    "write_feedback", "render_feedback_block", "render_active_feedback", "clear_feedback",
    "feedback_diagnostic_win_false",
    "feedback_hygiene_closure_loop", "feedback_loop_detected", "feedback_provider_fence",
]
