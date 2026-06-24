"""Tests for the meta-level control layer (saturation → enforced regime-jump).

These pin the philosophy-critical contract:

* DETECT  — saturation is a counter (frozen_rounds + diversity), with the
  diversity descriptor only allowed to SUPPRESS a jump when the agent's OWN
  regime labels make it trustworthy; an unlabelled long freeze still trips.
* JUDGE   — ``parse_meta_decision`` validates the planner's structured output
  but never invents one; a jump that re-anchors on a forbidden/local regime is
  flagged invalid.
* ENFORCE — the never-cleared forbidden ledger (agent-authored only), the
  consume-once jump reset, and the checkpoint context reset.
"""
from __future__ import annotations

import json

from argus_skill.engineer.checkpoint import CheckpointState
from argus_skill.meta import ledger
from argus_skill.meta.config import MetaConfig
from argus_skill.meta.flow_controller import decide, record_decision
from argus_skill.meta.meta_prompter import parse_meta_decision
from argus_skill.meta.saturation import from_facts
from argus_skill.verticals._base import load_vertical


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _write_attempt(root, name, score, *, decision="", strategy_type=None):
    d = root / "attempts" / name
    d.mkdir(parents=True, exist_ok=True)
    obj = {"candidate": name, "mean_val_bpb": score}
    if decision:
        obj["decision"] = decision
    if strategy_type is not None:
        obj["strategy_type"] = strategy_type
    (d / "summary.json").write_text(json.dumps(obj), encoding="utf-8")


def _facts(attempts, *, since_improve, floor):
    return {
        "floor": floor,
        "floor_name": "a_floor",
        "since_improve": since_improve,
        "raw_best": floor,
        "n_attempts": len(attempts),
        "attempts": attempts,
    }


# --------------------------------------------------------------------------- #
# saturation detection (pure)
# --------------------------------------------------------------------------- #
def test_frozen_and_low_diversity_is_saturated():
    # 20 rounds frozen, all labelled 'architecture' → one regime → saturated.
    attempts = [
        {"name": f"a{i:03d}_x", "score": 0.97, "decision": "", "strategy_type": "architecture"}
        for i in range(20)
    ]
    sig = from_facts(_facts(attempts, since_improve=19, floor=0.965), MetaConfig())
    assert sig.is_saturated
    assert sig.frozen_rounds == 19
    assert sig.diversity_score == 1
    assert sig.window_labelled is True
    assert "architecture" not in sig.untouched_axes
    assert "optimizer" in sig.untouched_axes


def test_labelled_high_diversity_suppresses_jump():
    # Frozen, but the agent IS genuinely cycling regimes → do NOT force a jump.
    axes = ["optimizer", "architecture", "data", "numerics", "update_mechanics"]
    attempts = [
        {"name": f"a{i:03d}_x", "score": 0.97, "strategy_type": axes[i % len(axes)]}
        for i in range(20)
    ]
    sig = from_facts(_facts(attempts, since_improve=19, floor=0.965), MetaConfig())
    assert sig.window_labelled is True
    assert sig.diversity_score > MetaConfig().diversity_floor
    assert sig.is_saturated is False  # already exploring — no forced jump


def test_unlabelled_long_freeze_still_saturates():
    # No strategy_type labels (legacy). The name-token proxy must NOT be trusted
    # to SUPPRESS a long freeze: frozen-only fallback fires.
    attempts = [
        {"name": f"a{i:03d}_freshadjective{i}_bundle", "score": 0.97} for i in range(20)
    ]
    sig = from_facts(_facts(attempts, since_improve=19, floor=0.965), MetaConfig())
    assert sig.window_labelled is False
    assert sig.is_saturated  # long freeze trips despite superficial name variety


def test_short_freeze_is_not_saturated():
    attempts = [{"name": f"a{i:03d}_x", "score": 0.97, "strategy_type": "architecture"} for i in range(5)]
    sig = from_facts(_facts(attempts, since_improve=4, floor=0.965), MetaConfig())
    assert sig.is_saturated is False


# --------------------------------------------------------------------------- #
# flow controller over a real synthetic mission (via the nanochat vertical)
# --------------------------------------------------------------------------- #
def test_decide_jumps_on_frozen_mission(tmp_path):
    _write_attempt(tmp_path, "a001_seed", 0.96, decision="promote", strategy_type="architecture")
    for i in range(2, 21):
        _write_attempt(tmp_path, f"a{i:03d}_arch_tweak", 0.97, strategy_type="architecture")
    vmod = load_vertical("nanochat")
    flow = decide(tmp_path, vmod, MetaConfig())
    assert flow.mode == "jump"
    assert flow.signal.is_saturated
    block = flow.prompt_block
    assert "REGIME JUMP CONVENED" in block
    assert "optimizer" in block  # an untouched axis is offered
    assert "Context reset" in block


def test_decide_exploits_when_healthy(tmp_path):
    _write_attempt(tmp_path, "a001_seed", 0.99, decision="promote", strategy_type="optimizer")
    _write_attempt(tmp_path, "a002_win", 0.95, decision="promote", strategy_type="architecture")
    vmod = load_vertical("nanochat")
    flow = decide(tmp_path, vmod, MetaConfig())
    assert flow.mode == "exploit"
    assert flow.prompt_block == ""


# --------------------------------------------------------------------------- #
# meta_decision parsing / validation (judge stays with the agent)
# --------------------------------------------------------------------------- #
def test_parse_valid_jump():
    text = 'blah\n```json\n{"mode":"jump","confidence":0.6,"strategy_type":"optimizer","forbidden":["architecture"]}\n```\n'
    dec = parse_meta_decision(text, forbidden_axes=set(), require_jump=True)
    assert dec.present and dec.valid
    assert dec.strategy_type == "optimizer"
    assert dec.forbidden == ["architecture"]


def test_parse_rejects_repick_of_forbidden_regime():
    text = '```json\n{"mode":"jump","strategy_type":"architecture"}\n```'
    dec = parse_meta_decision(text, forbidden_axes={"architecture"}, require_jump=True)
    assert dec.present and not dec.valid
    assert any("FORBIDDEN" in v or "dead" in v for v in dec.violations)


def test_parse_rejects_local_as_jump():
    text = '```json\n{"mode":"jump","strategy_type":"local"}\n```'
    dec = parse_meta_decision(text, require_jump=True)
    assert not dec.valid
    assert any("regime axis" in v for v in dec.violations)


def test_parse_absent_block_on_required_jump_is_invalid():
    dec = parse_meta_decision("no json here", require_jump=True)
    assert dec.present is False
    assert dec.valid is False


# --------------------------------------------------------------------------- #
# ledger: never-cleared forbidden + consume-once jump reset + coverage
# --------------------------------------------------------------------------- #
def test_forbidden_is_never_cleared(tmp_path):
    ledger.merge_forbidden(tmp_path, ["architecture"], coverage={"architecture": 5})
    ledger.merge_forbidden(tmp_path, ["data"])  # add another, must KEEP architecture
    led = ledger.load_ledger(tmp_path)
    assert set(led.forbidden) == {"architecture", "data"}
    assert led.coverage == {"architecture": 5}


def test_jump_pending_is_consume_once(tmp_path):
    ledger.set_jump_pending(tmp_path, True)
    assert ledger.consume_jump_pending(tmp_path) is True
    assert ledger.consume_jump_pending(tmp_path) is False


def test_record_decision_persists_agent_forbidden_and_arms_reset(tmp_path):
    _write_attempt(tmp_path, "a001_seed", 0.96, decision="promote", strategy_type="architecture")
    for i in range(2, 21):
        _write_attempt(tmp_path, f"a{i:03d}_arch", 0.97, strategy_type="architecture")
    vmod = load_vertical("nanochat")
    flow = decide(tmp_path, vmod, MetaConfig())
    assert flow.mode == "jump"
    planner_out = '```json\n{"mode":"jump","strategy_type":"optimizer","forbidden":["architecture micro-tweaks"]}\n```'
    dec = record_decision(tmp_path, planner_out, flow, now=123.0)
    assert dec.valid and dec.strategy_type == "optimizer"
    led = ledger.load_ledger(tmp_path)
    assert any("architecture" in f for f in led.forbidden)  # agent-declared, persisted
    assert led.jump_pending is True  # reset armed for the engineer
    rows = (tmp_path / "research" / "META_LEDGER.jsonl").read_text().strip().splitlines()
    row = json.loads(rows[-1])
    assert row["was_jump"] is True and row["strategy_type"] == "optimizer"


# --------------------------------------------------------------------------- #
# checkpoint context reset
# --------------------------------------------------------------------------- #
def test_cleared_for_jump_drops_local_keeps_durable():
    cp = CheckpointState(
        goal="lower bpb",
        done=["verified A275"],
        tried_and_failed=["dead end X"],
        maturing=["semi-global carrier refinement"],
        active_line={"desc": "semiglobal", "rounds_active": 20},
        next_step="tweak gate",
        env_facts=["B200 at :2231"],
    )
    j = cp.cleared_for_jump()
    assert j.active_line == {} and j.maturing == [] and j.next_step == ""
    assert j.goal == "lower bpb"
    assert j.done == ["verified A275"]
    assert j.tried_and_failed == ["dead end X"]  # dead ends stay dead
    assert j.env_facts == ["B200 at :2231"]
