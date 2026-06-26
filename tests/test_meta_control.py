"""Tests for the meta-level control layer (saturation → enforced regime-jump).

These pin the philosophy-critical contract:

* DETECT  — saturation is a counter (frozen_rounds over candidate attempts,
  including no-score gate-failures so it can't be starved). Once the metric
  floor is frozen past the jump threshold it is GROUND TRUTH: the agent's
  strategy_type labels can no longer SUPPRESS the jump (a frozen floor, however
  diversely labelled, is still frozen). Diversity is surfaced and gates only the
  softer sub-threshold 'explore' nudge.
* JUDGE   — ``parse_meta_decision`` validates the planner's structured output
  but never invents one; a jump that re-anchors on a forbidden/local regime is
  flagged invalid.
* ENFORCE — the never-cleared forbidden ledger (agent-authored only), the
  consume-once jump reset, and the checkpoint context reset.
"""
from __future__ import annotations

import json

from argus_skill.engineer.checkpoint import CheckpointState
from argus_skill.regime_jump import ledger
from argus_skill.regime_jump.config import MetaConfig
from argus_skill.regime_jump.flow_controller import decide, record_decision
from argus_skill.regime_jump.meta_prompter import parse_meta_decision
from argus_skill.regime_jump.saturation import from_facts
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


def test_labelled_diversity_does_not_suppress_frozen_floor():
    # GROUND-TRUTH GUARDRAIL: once the metric floor is frozen past the jump
    # threshold, the agent's own strategy_type labels can NO LONGER suppress the
    # jump. A floor that has not moved for >= threshold candidate attempts is
    # saturated by the only ground truth we have (the metric), however diversely
    # the agent labelled those attempts — if the labelled diversity were
    # genuinely productive, the floor would have moved. This reverses the earlier
    # label-trusting suppression that let label-rotation on a value-frozen basin
    # game the meta layer (the live nanochat-B200 stall).
    axes = ["optimizer", "architecture", "data", "numerics", "update_mechanics"]
    attempts = [
        {"name": f"a{i:03d}_x", "score": 0.97, "strategy_type": axes[i % len(axes)]}
        for i in range(20)
    ]
    sig = from_facts(_facts(attempts, since_improve=19, floor=0.965), MetaConfig())
    assert sig.window_labelled is True
    assert sig.diversity_score > MetaConfig().diversity_floor
    assert sig.is_saturated is True  # frozen floor (ground truth) overrides labels


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
    assert "REGIME-JUMP TURN" in block
    assert "YOU decide" in block  # soft framing — convened, not enforced
    assert "optimizer" in block  # an untouched axis is offered
    assert "Context reset" in block


def test_decide_exploits_when_healthy(tmp_path):
    _write_attempt(tmp_path, "a001_seed", 0.99, decision="promote", strategy_type="optimizer")
    _write_attempt(tmp_path, "a002_win", 0.95, decision="promote", strategy_type="architecture")
    vmod = load_vertical("nanochat")
    flow = decide(tmp_path, vmod, MetaConfig())
    assert flow.mode == "exploit"
    assert flow.prompt_block == ""


def test_floor_anchor_ignores_reject_text_and_counts_no_score(tmp_path):
    # Regression for the live nanochat-B200 stall (44 attempts frozen, no jump):
    #   Bug1 — the floor must anchor on the structured `promoted` flag (a374),
    #          NOT a rejected candidate whose decision text says "restored to
    #          promoted a374" (the "promote" substring trap re-anchored to a382);
    #   Bug2 — no-score gate-fail candidates must still count as frozen steps so
    #          the counter is not starved (pure diagnosis attempts must NOT);
    #   Bug3 — a frozen floor must saturate even though the recent window carries
    #          diverse strategy_type labels.
    A = tmp_path / "attempts"

    def w(name, **kw):
        d = A / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(json.dumps(kw), encoding="utf-8")

    axes = ["architecture", "update_mechanics", "numerics"]
    # the real promoted floor
    w("a374_floor", promoted=True, score_valid=True, MEAN_VAL_BPB=0.963634,
      strategy_type="data", decision="promote_a374_new_global_best")
    # rejected candidates whose decision text CONTAINS "promoted" (the substring trap)
    for i, s in [(380, 0.972423), (381, 0.963827), (382, 0.967003)]:
        w(f"a{i}_x", promoted=False, score_valid=True, MEAN_VAL_BPB=s, candidate_sha="d",
          strategy_type="data",
          decision="rejected; root train.py restored to promoted a374 because it regressed")
    # scored rejects, none beating the floor, rotating regime labels
    for k, i in enumerate(range(383, 400)):
        w(f"a{i}_x", promoted=False, score_valid=True, MEAN_VAL_BPB=0.9637 + k * 1e-5,
          strategy_type=axes[k % 3], decision="reject_restore_a374")
    # officially-scored rejects under the official_val_bpb key (no mean_val_bpb)
    for i, s in [(410, 0.964252), (411, 0.97307), (412, 0.963764)]:
        w(f"a{i}_x", promoted=False, official_val_bpb=s, candidate_sha="d",
          strategy_type="update_mechanics", decision="REJECT_RESTORE_A374")
    # a pure DIAGNOSIS attempt — must NOT count toward the freeze
    w("a415_diag", diagnosis_type="proxy_audit", strategy_type="architecture")
    # no-score gate-fail candidates — these MUST count as frozen steps
    for i in range(416, 421):
        w(f"a{i}_x", promoted=False, official_val_bpb=None, candidate_sha="d",
          official_scored=False, strategy_type=axes[i % 3],
          decision="PROFILE_GATE_FAIL_NO_SCORE")

    from argus_skill.verticals.nanochat.stages import search_altitude_facts

    facts = search_altitude_facts(tmp_path)
    assert facts["floor_name"].startswith("a374")          # not re-anchored to a382
    assert abs(facts["floor"] - 0.963634) < 1e-9           # not 0.967003
    # 3 (a380-382) + 17 (a383-399) + 3 (a410-412) + 5 (a416-420) = 28; a415 excluded
    assert facts["since_improve"] == 28                     # no-score counts, diagnosis doesn't

    vmod = load_vertical("nanochat")
    flow = decide(tmp_path, vmod, MetaConfig())
    assert flow.signal.is_saturated is True
    assert flow.mode == "jump"
    rec = record_decision(
        tmp_path,
        '```json\n{"mode":"jump","strategy_type":"optimizer",'
        '"forbidden":["residual-temperature row-group tweaks"]}\n```',
        flow, now=1.0,
    )
    assert rec.valid and rec.strategy_type == "optimizer"
    assert ledger.consume_jump_pending(tmp_path) is True


# --------------------------------------------------------------------------- #
# meta_decision parsing / validation (judge stays with the agent)
# --------------------------------------------------------------------------- #
def test_parse_valid_jump():
    text = 'blah\n```json\n{"mode":"jump","strategy_type":"optimizer","forbidden":["architecture"]}\n```\n'
    dec = parse_meta_decision(text, forbidden_axes=set(), require_jump=True)
    assert dec.present and dec.valid
    assert dec.strategy_type == "optimizer"
    assert dec.forbidden == ["architecture"]
    assert not hasattr(dec, "confidence")


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


def test_valley_immunity_window_suppresses_jump_and_decays(tmp_path):
    # After a jump, a post-jump exploration window opens: no new jump for N
    # rounds (develop the regime), grace block injected, window decays, then a
    # fresh jump can convene again.
    from argus_skill.regime_jump import ledger as _ledger
    _write_attempt(tmp_path, "a001_seed", 0.96, decision="promote", strategy_type="architecture")
    for i in range(2, 21):
        _write_attempt(tmp_path, f"a{i:03d}_arch", 0.97, strategy_type="architecture")
    vmod = load_vertical("nanochat")
    cfg = MetaConfig(explore_window_rounds=2)

    f1 = decide(tmp_path, vmod, cfg)
    assert f1.mode == "jump"  # frozen + window=0 → jump
    record_decision(tmp_path, '```json\n{"mode":"jump","strategy_type":"optimizer","forbidden":[]}\n```', f1, cfg, now=1.0)
    assert _ledger.load_ledger(tmp_path).explore_window == 2  # window opened

    f2 = decide(tmp_path, vmod, cfg)
    assert f2.mode == "exploit"  # window>0 → NO new jump (develop the regime)
    assert "EXPLORATION WINDOW" in f2.prompt_block
    assert "regress" in f2.prompt_block.lower() and "floor is safe" in f2.prompt_block.lower()
    record_decision(tmp_path, "", f2, cfg, now=2.0)
    assert _ledger.load_ledger(tmp_path).explore_window == 1  # decayed

    f3 = decide(tmp_path, vmod, cfg)
    assert f3.mode == "exploit" and "EXPLORATION WINDOW" in f3.prompt_block
    record_decision(tmp_path, "", f3, cfg, now=3.0)
    assert _ledger.load_ledger(tmp_path).explore_window == 0  # window closed

    f4 = decide(tmp_path, vmod, cfg)
    assert f4.mode == "jump"  # window closed + still saturated → fresh jump
