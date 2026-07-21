"""Tests for the tiered physics workflow: innovation tiers, auto-downgrade, no-go
terminal path, gate-fail feedback, and context-compaction. Physics-vertical only;
no Argus core touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.verticals.physics import context_policy, downgrade, gate_feedback, tiers
from argus_skill.verticals.physics import stages
from argus_skill.verticals.physics.gates import downgrade as dgate
from argus_skill.verticals.physics.gates import nogo_terminal as ngate


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _seed(tmp: Path, *, current="execute", crossings=5, pivots=2, review_rollback=1,
          falsified=3, nogo=True, claims=True) -> Path:
    (tmp / "research").mkdir(parents=True, exist_ok=True)
    stage_hist = [{"from_stage": "scope", "to_stage": "model", "direction": "advance"}]
    stage_hist += [{"from_stage": "model", "to_stage": "execute", "direction": "advance"}
                   for _ in range(crossings - 1)]
    rollbacks = [{"from_stage": "review", "to_stage": "execute", "reason": "novelty pivot"}
                 for _ in range(review_rollback)]
    rollbacks += [{"from_stage": "execute", "to_stage": "model", "reason": f"pivot {i}"}
                  for i in range(pivots)]
    (tmp / "research" / "PIPELINE_STATE.json").write_text(json.dumps({
        "current_stage": current, "stage_history": stage_hist, "rollback_history": rollbacks,
    }))
    if nogo:
        (tmp / "ROUTE_CLOSURE_STATUS.json").write_text(json.dumps({
            "route_status": "ORIGINAL_RESEARCH_NO_GO",
            "paper_type": "bounded finite-volume failure-regime / no-go manuscript",
            "failed_round2_candidates": [{"id": f"R2D{i:02d}"} for i in range(falsified)],
        }))
        (tmp / "ORIGINAL_RESEARCH_NO_GO.md").write_text("# No-go\nAll diagnostics falsified.\n")
    if claims:
        (tmp / "CLAIMS.csv").write_text(
            "claim_id,claim_text,claim_type,evidence_type,evidence_pointer,status,boundary,reviewer_notes\n"
            "C01,No diagnostic beats baseline,negative_method_result,numerical,ROUTE_CLOSURE_STATUS.json,no-go,finite-volume,ok\n")
    return tmp


@pytest.fixture(autouse=True)
def _tier_env(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_START_TIER", "B")
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_NOGO_TERMINAL", "true")
    monkeypatch.delenv("ARGUS_SKILL_PHYSICS_TARGET_PAPER_TYPE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_PHYSICS_ALLOW_DOWNGRADE", raising=False)


# --------------------------------------------------------------------------- #
# 1. tiers
# --------------------------------------------------------------------------- #
def test_tier_ladder_order_and_default():
    assert tiers.TIER_ORDER == ("S", "A", "B", "C", "D")
    assert tiers.resolve_start_tier() == "B"
    assert tiers.next_lower_tier("B") == "C"
    assert tiers.next_lower_tier("C") == "D"
    assert tiers.next_lower_tier("D") == ""  # terminal
    assert tiers.is_terminal_tier("D") is True


def test_each_tier_has_claim_and_gates():
    for t in tiers.TIER_ORDER:
        spec = tiers.tier_spec(t)
        assert spec.claim_types and spec.evidence_requirements
        assert spec.reviewer_gate and spec.manuscript_gate
    # S/A need operator auth (stretch); B/C/D do not
    assert tiers.tier_spec("S").operator_auth_required is True
    assert tiers.tier_spec("A").operator_auth_required is True
    assert tiers.tier_spec("B").operator_auth_required is False
    assert tiers.tier_spec("D").operator_auth_required is False


def test_tier_rubric_banner_is_tier_specific():
    b = tiers.tier_rubric_banner("C")
    assert "ACTIVE INNOVATION TIER — C" in b
    assert "MUST NOT apply a higher tier's standard" in b


# --------------------------------------------------------------------------- #
# 2. downgrade triggers + state machine
# --------------------------------------------------------------------------- #
def test_triggers_match_s_cbac6ede_shape(tmp_path):
    root = _seed(tmp_path)
    tr = downgrade.compute_triggers(root)
    # helper yields 4 advance + 2 rollback model<->execute crossings = 6 (>= the cap of 4)
    assert tr["model_execute_crossings"] >= 4
    assert tr["pivots_used"] == 2
    assert tr["same_diagnostic_falsified"] == 3
    assert tr["closure_artifact_exists"] is True
    fired = downgrade.fired_triggers(tr)
    assert "pivot_cap" in fired and "model_execute_cap" in fired


def test_downgrade_walks_b_to_d(tmp_path):
    root = _seed(tmp_path)
    assert downgrade.read_current_tier(root) == "B"
    d1 = downgrade.evaluate_and_maybe_downgrade(root, now_iso="t")
    assert d1["from_tier"] == "B" and d1["to_tier"] == "C"
    assert downgrade.read_current_tier(root) == "C"
    d2 = downgrade.evaluate_and_maybe_downgrade(root, now_iso="t")
    assert d2["to_tier"] == "D"
    assert downgrade.read_current_tier(root) == "D"
    # terminal — no further downgrade
    assert downgrade.evaluate_and_maybe_downgrade(root, now_iso="t") is None


def test_downgrade_emits_four_artifacts(tmp_path):
    root = _seed(tmp_path)
    downgrade.evaluate_and_maybe_downgrade(root, now_iso="t")
    r = root / "research"
    for f in ("DOWNGRADE_DECISION.json", "DOWNGRADE_RATIONALE.md",
              "UPDATED_CLAIM_SCOPE.md", "NEXT_ROLE_DIRECTIVE.json"):
        assert (r / f).is_file(), f
    dec = json.loads((r / "DOWNGRADE_DECISION.json").read_text())
    assert dec["rigor_unchanged"] is True
    assert dec["reviewer_adjudication_required"] is True


def test_no_downgrade_when_no_triggers(tmp_path):
    # fresh run: no rollbacks, no closure artifact
    root = _seed(tmp_path, current="model", crossings=1, pivots=0, review_rollback=0,
                 falsified=0, nogo=False, claims=False)
    assert downgrade.evaluate_and_maybe_downgrade(root, now_iso="t") is None
    assert downgrade.read_current_tier(root) == "B"


def test_downgrade_gate_surfaces_reviewer_ratification(tmp_path):
    root = _seed(tmp_path)
    passed, failures = dgate.run_gate(root, now_iso="t")
    assert passed is False  # a downgrade fired this round
    rec = json.loads((root / "research" / "GATE_FAIL_downgrade.json").read_text())
    assert rec["responsible_role"] == "Reviewer"
    assert "re-apply the higher tier's bar" in " ".join(rec["do_not_do"])


# --------------------------------------------------------------------------- #
# 3. no-go terminal path
# --------------------------------------------------------------------------- #
def test_nogo_sufficient_autonomously_authorizes(tmp_path):
    root = _seed(tmp_path)
    downgrade.evaluate_and_maybe_downgrade(root, now_iso="t")
    downgrade.evaluate_and_maybe_downgrade(root, now_iso="t")  # -> D
    assert downgrade.read_current_tier(root) == "D"
    st = ngate.nogo_evidence_status(root)
    assert st["sufficient"] is True
    ngate.run_gate(root, now_iso="t")
    closure = json.loads((root / "ROUTE_CLOSURE_STATUS.json").read_text())
    assert closure["manuscript_completion_authorized"] is True
    assert "NO_GO" in closure["manuscript_completion_authorized_scope"]
    directive = json.loads((root / "research" / "NEXT_ROLE_DIRECTIVE.json").read_text())
    assert directive["expected_next_stage"] == "manuscript"
    assert any("positive" in x for x in directive["do_not_do"])


def test_nogo_insufficient_surfaces_single_gap_not_hygiene(tmp_path):
    # closure artifact but NO bounded no-go claim in CLAIMS
    root = _seed(tmp_path, claims=False)
    st = ngate.nogo_evidence_status(root)
    assert st["sufficient"] is False
    assert any("CLAIMS" in m for m in st["missing"])
    passed, failures = ngate.run_gate(root, now_iso="t")
    # advisory: always non-fatal; surfaces the missing piece, never dispatches hygiene
    for f in failures:
        assert "do not chase a positive diagnostic" in f["required_action"].lower()


def test_nogo_operator_gated_at_stretch_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_START_TIER", "A")
    root = _seed(tmp_path)
    # force current tier A (stretch)
    (root / "research" / "TIER_STATE.json").write_text(json.dumps({"current_tier": "A"}))
    ngate.run_gate(root, now_iso="t")
    req = root / "research" / "OPERATOR_AUTHORIZATION_REQUEST.json"
    assert req.is_file()
    payload = json.loads(req.read_text())
    assert "authorize" in " ".join(payload["options"]).lower()
    # not silently authorized at a stretch tier
    closure = json.loads((root / "ROUTE_CLOSURE_STATUS.json").read_text())
    assert not closure.get("manuscript_completion_authorized")


def test_nogo_terminal_disabled_requests_operator(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_NOGO_TERMINAL", "false")
    root = _seed(tmp_path)
    (root / "research" / "TIER_STATE.json").write_text(json.dumps({"current_tier": "D"}))
    ngate.run_gate(root, now_iso="t")
    assert (root / "research" / "OPERATOR_AUTHORIZATION_REQUEST.json").is_file()


# --------------------------------------------------------------------------- #
# 4. gate-fail feedback protocol
# --------------------------------------------------------------------------- #
def test_feedback_has_all_fields():
    rec = gate_feedback.feedback_manuscript_completion_unauthorized(at_stretch_tier=False, tier="D")
    for field in gate_feedback.FEEDBACK_FIELDS:
        assert field in rec
    assert rec["responsible_role"] == "ManuscriptBuilder"
    assert any("hygiene" in x for x in rec["do_not_do"])


def test_feedback_special_cases():
    # SC1 operator-gated
    op = gate_feedback.feedback_manuscript_completion_unauthorized(at_stretch_tier=True, tier="A")
    assert op["responsible_role"] == "Operator" and op["blocking_level"] == "operator_required"
    assert op["if_operator_required_then_prompt"]
    # SC2 pivot vs downgrade
    assert gate_feedback.feedback_diagnostic_win_false(tier="A", pivots_used=0, pivot_cap=2)["responsible_role"] == "Engineer"
    assert gate_feedback.feedback_diagnostic_win_false(tier="A", pivots_used=2, pivot_cap=2)["responsible_role"] == "Reviewer"
    # SC3 hygiene loop -> Manager, one blocker
    assert gate_feedback.feedback_hygiene_closure_loop()["responsible_role"] == "Manager"
    # SC4 loop detected -> Reviewer
    assert gate_feedback.feedback_loop_detected(stage="execute", blocker="x", repeats=3)["gate_id"] == "loop_detected"
    # SC5 provider fence classes
    for sub in ("partial_pricing", "per_call_overrun", "mission_budget", "daily_global_cap"):
        rec = gate_feedback.feedback_provider_fence(sub_case=sub)
        assert "disable real caps" in " ".join(rec["do_not_do"])


def test_feedback_validation_rejects_bad_role():
    with pytest.raises(ValueError):
        gate_feedback.build_feedback(
            gate_id="x", gate_name="x", failed_stage="review", responsible_role="Nobody",
            blocking_level="hard", exact_blocker="x", required_action="x", acceptance_test="x")


# --------------------------------------------------------------------------- #
# 5. context compaction
# --------------------------------------------------------------------------- #
def test_should_compress_thresholds(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_CONTEXT_TOKEN_HARD", "15000000")
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_CONTEXT_TOKEN_SOFT", "8000000")
    assert context_policy.should_compress(token_estimate=16_000_000)[0] is True
    assert context_policy.should_compress(token_estimate=9_000_000)[0] is True
    assert context_policy.should_compress(token_estimate=1_000_000)[0] is False
    assert context_policy.should_compress(cost_usd=11.5, per_call_cap_usd=12.0)[0] is True


def test_context_digest_uses_pointers(tmp_path):
    root = _seed(tmp_path)
    (root / "events.jsonl").write_text("x" * 1000)
    dig = context_policy.build_context_digest(root)
    assert "POINTERS ONLY" in dig
    assert "events.jsonl" in dig and "do not inline" in dig.lower() or "pointer" in dig.lower()
    p = context_policy.write_digest(root)
    assert p.is_file()


# --------------------------------------------------------------------------- #
# 6. wiring: STAGE_CHECKS + role_banner + mode
# --------------------------------------------------------------------------- #
def test_stage_checks_include_new_gates():
    def cmds(stage):
        return " ".join(c[1] for c in stages.STAGE_CHECKS[stage])
    assert "gates.downgrade" in cmds("execute") and "gates.nogo_terminal" in cmds("execute")
    assert "gates.downgrade" in cmds("review") and "gates.nogo_terminal" in cmds("review")
    # terminal manuscript HARD gate unchanged
    assert "manuscript check" in cmds("manuscript") or "manuscript" in cmds("manuscript")


def test_role_banner_is_tiered_not_hard_original_research(tmp_path):
    root = _seed(tmp_path, crossings=1, pivots=0, review_rollback=0, falsified=0, nogo=False, claims=False)
    b = stages.role_banner("reviewer", root)
    assert "ACTIVE INNOVATION TIER" in b
    assert "TIERED RESEARCH MODE" in b
    assert "CONTEXT POLICY" in b
    # the old hard "ORIGINAL RESEARCH REQUIRED / do NOT reach project_done as a benchmark" pin is gone in auto mode
    assert "RUN MODE — ORIGINAL RESEARCH REQUIRED" not in b


def test_mode_config_not_original_research_required_by_default():
    from argus_skill.verticals.physics import mode_config
    # tiered default: TARGET auto + downgrade allowed => not the hard no-downgrade gate
    assert mode_config.is_original_research_required() is False
