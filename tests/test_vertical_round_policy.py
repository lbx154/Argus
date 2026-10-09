"""Round guards are a per-vertical policy, not a hidden global cap.

Missions have no fixed round ceiling. The anti-livelock guards that remain
(Reviewer no-progress streak, empty Engineer turns, the soft progress window,
and the hard progress-judgement boundary) are resolved per mission from the
active vertical, with operator knobs on top. Whatever the values, a count-based
guard never ends a mission while the Reviewer reports forward progress.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import ReviewDecision
from argus.core.round_policy import (
    DEFAULT_ROUND_POLICY,
    ROUND_POLICY_KNOBS,
    RoundPolicy,
    RoundPolicyError,
    explain_round_policy,
    operator_round_policy,
    parse_round_policy,
    resolve_round_policy,
)
from argus.core.vertical_contract import VerticalContractError, vertical_contract
from argus.engineer.round_settlement import RoundSettlementMixin
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.loop import SkillLoop, SkillLoopConfig
from argus.reviewer import Reviewer, ReviewerConfig
from argus.verticals._base import load_vertical_contract
from argus.verticals._data_domain import DataDomain

_NO_KNOBS: dict[str, str] = {}

_BUILT_INS = (
    "argus_maintenance",
    "kernel_engineering",
    "learning",
    "math",
    "math_synth",
    "research",
    "software",
)


@pytest.fixture(autouse=True)
def _clear_round_knobs(monkeypatch):
    for knob in ROUND_POLICY_KNOBS.values():
        monkeypatch.delenv(knob, raising=False)


# ---------------------------------------------------------------------------
# Defaults and fallback
# ---------------------------------------------------------------------------


def test_default_policy_is_the_pre_policy_behaviour() -> None:
    assert DEFAULT_ROUND_POLICY == RoundPolicy(
        stall_threshold=4,
        no_progress_threshold=2,
        soft_round_limit=12,
        hard_escalate_rounds=24,
    )
    defaults = SupervisedConfig()
    assert (
        defaults.stall_threshold,
        defaults.no_progress_threshold,
        defaults.soft_round_limit,
        defaults.hard_escalate_rounds,
    ) == (4, 2, 12, 24)


def test_a_vertical_that_declares_nothing_gets_the_default() -> None:
    assert resolve_round_policy(None, env=_NO_KNOBS, persisted={}) == DEFAULT_ROUND_POLICY


def test_a_partial_declaration_keeps_the_default_for_the_rest() -> None:
    declared = parse_round_policy("v", {"soft_round_limit": 0})
    policy = resolve_round_policy(declared, env=_NO_KNOBS, persisted={})
    assert policy.soft_round_limit == 0
    assert policy.stall_threshold == DEFAULT_ROUND_POLICY.stall_threshold
    assert policy.hard_escalate_rounds == DEFAULT_ROUND_POLICY.hard_escalate_rounds


def test_no_built_in_vertical_is_tighter_than_the_default() -> None:
    def looser_or_equal(value: int, default: int) -> bool:
        return value == 0 or value >= default

    for name in _BUILT_INS:
        policy = resolve_round_policy(
            load_vertical_contract(name, scoped=False).round_policy,
            env=_NO_KNOBS,
            persisted={},
        )
        for field_name, default in DEFAULT_ROUND_POLICY.as_dict().items():
            assert looser_or_equal(getattr(policy, field_name), default), (name, field_name)


# ---------------------------------------------------------------------------
# Per-vertical resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["kernel_engineering", "argus_maintenance", "math"])
def test_verification_verticals_turn_round_count_guards_off(name: str) -> None:
    policy = load_vertical_contract(name, scoped=False).round_policy
    assert policy is not None
    assert policy.soft_round_limit == 0
    assert policy.hard_escalate_rounds >= 200
    # The Reviewer's own stall judgement stays the primary stop.
    assert policy.stall_threshold > DEFAULT_ROUND_POLICY.stall_threshold


def test_research_keeps_guards_but_far_out() -> None:
    policy = load_vertical_contract("research", scoped=False).round_policy
    assert policy is not None
    assert policy.soft_round_limit >= 48
    assert policy.hard_escalate_rounds >= 96


def test_bounded_software_work_keeps_the_default() -> None:
    policy = load_vertical_contract("software", scoped=False).round_policy
    assert resolve_round_policy(policy, env=_NO_KNOBS, persisted={}) == DEFAULT_ROUND_POLICY


def test_contract_refuses_a_malformed_policy() -> None:
    contract = load_vertical_contract("software", scoped=False)
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=contract.stage_order,
        CHECKLIST_ITEMS=contract.checklist_items,
        completion_gate="none",
    )
    for bad in ({"soft_round_limit": -1}, {"rounds": 3}, {"stall_threshold": "4"}, [4]):
        provider.ROUND_POLICY = bad
        with pytest.raises(VerticalContractError):
            vertical_contract("probe", provider)


def test_profiled_contract_keeps_the_vertical_policy() -> None:
    contract = load_vertical_contract("research", scoped=False)
    for profile in contract.workflow_profiles:
        assert contract.for_profile(profile).round_policy == contract.round_policy


def test_data_domain_declares_policy_and_reads_fail_open() -> None:
    domain = DataDomain(
        {"name": "probe", "stages": ["work"], "round_policy": {"soft_round_limit": 0}}
    )
    assert domain.ROUND_POLICY == RoundPolicy(soft_round_limit=0)
    broken = DataDomain(
        {"name": "probe", "stages": ["work"], "round_policy": ["soft_round_limit"]}
    )
    assert broken.ROUND_POLICY is None


def test_data_domain_keeps_valid_fields_and_warns_once_per_bad_field(caplog) -> None:
    raw = {
        "soft_round_limit": 0,
        "stall_threshold": "x",
        "hard_escalate_rounds": -3,
        "rounds": 5,
    }
    with caplog.at_level("WARNING", logger="argus.core.round_policy"):
        first = DataDomain({"name": "probe_once", "stages": ["work"], "round_policy": raw})
        DataDomain({"name": "probe_once", "stages": ["work"], "round_policy": raw})
    assert first.ROUND_POLICY == RoundPolicy(soft_round_limit=0)
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 3
    for field_name in ("stall_threshold", "hard_escalate_rounds", "rounds"):
        assert sum(f"round_policy.{field_name}:" in message for message in warnings) == 1


def test_data_domain_policy_reaches_the_mission(tmp_path) -> None:
    domains = tmp_path / "research" / "DOMAINS"
    domains.mkdir(parents=True)
    (domains / "probe.json").write_text(
        json.dumps({
            "name": "probe",
            "stages": ["work"],
            "round_policy": {"hard_escalate_rounds": 0, "stall_threshold": 9},
        }),
        encoding="utf-8",
    )
    loop = SimpleNamespace(config=SkillLoopConfig())
    policy = SkillLoop._resolve_round_policy(loop, "probe", tmp_path)
    assert policy.hard_escalate_rounds == 0
    assert policy.stall_threshold == 9
    assert policy.soft_round_limit == DEFAULT_ROUND_POLICY.soft_round_limit


# ---------------------------------------------------------------------------
# Precedence: operator knob > explicit mission config > vertical > default
# ---------------------------------------------------------------------------


def test_mission_resolution_uses_the_active_vertical(tmp_path) -> None:
    loop = SimpleNamespace(config=SkillLoopConfig())
    policy = SkillLoop._resolve_round_policy(loop, "kernel_engineering", tmp_path)
    assert policy == resolve_round_policy(
        load_vertical_contract("kernel_engineering", scoped=False).round_policy,
        env=_NO_KNOBS,
        persisted={},
    )
    assert SkillLoop._resolve_round_policy(loop, "", tmp_path) == DEFAULT_ROUND_POLICY


def test_explicit_mission_config_wins_over_the_vertical(tmp_path) -> None:
    loop = SimpleNamespace(config=SkillLoopConfig(soft_round_limit=5, stall_threshold=0))
    policy = SkillLoop._resolve_round_policy(loop, "kernel_engineering", tmp_path)
    assert policy.soft_round_limit == 5
    assert policy.stall_threshold == 0


def test_operator_knob_wins_over_everything(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SOFT_ROUND_LIMIT", "3")
    monkeypatch.setenv("ARGUS_SKILL_STALL_THRESHOLD", "0")
    loop = SimpleNamespace(config=SkillLoopConfig(soft_round_limit=5))
    policy = SkillLoop._resolve_round_policy(loop, "research", tmp_path)
    assert policy.soft_round_limit == 3
    assert policy.stall_threshold == 0
    research = load_vertical_contract("research", scoped=False).round_policy
    assert policy.hard_escalate_rounds == research.hard_escalate_rounds


def test_unusable_operator_knob_declares_nothing_and_warns(caplog) -> None:
    env = {"ARGUS_SKILL_HARD_ESCALATE_ROUNDS": "lots", "ARGUS_SKILL_SOFT_ROUND_LIMIT": "-4"}
    with caplog.at_level("WARNING", logger="argus.core.round_policy"):
        assert operator_round_policy(env=env, persisted={}) == RoundPolicy()
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "ARGUS_SKILL_HARD_ESCALATE_ROUNDS" in warned
    assert "ARGUS_SKILL_SOFT_ROUND_LIMIT" in warned


def test_vertical_knob_value_hands_control_back_silently(caplog) -> None:
    env = {"ARGUS_SKILL_SOFT_ROUND_LIMIT": "vertical"}
    with caplog.at_level("WARNING", logger="argus.core.round_policy"):
        assert operator_round_policy(env=env, persisted={}) == RoundPolicy()
    assert not caplog.records


def test_explain_names_the_source_of_every_value() -> None:
    policy, sources = explain_round_policy(
        RoundPolicy(soft_round_limit=0),
        explicit=RoundPolicy(stall_threshold=7),
        vertical="kernel_engineering",
        env={"ARGUS_SKILL_HARD_ESCALATE_ROUNDS": "9"},
        persisted={"ARGUS_SKILL_NO_PROGRESS_THRESHOLD": "3"},
    )
    assert policy == RoundPolicy(
        stall_threshold=7,
        no_progress_threshold=3,
        soft_round_limit=0,
        hard_escalate_rounds=9,
    )
    assert sources["soft_round_limit"] == "vertical 'kernel_engineering' round_policy"
    assert sources["stall_threshold"] == "mission loop config"
    assert sources["hard_escalate_rounds"].endswith("(env)")
    assert sources["no_progress_threshold"].endswith("(persisted)")


def test_round_knobs_are_cockpit_editable_with_validation() -> None:
    from argus.core.knobs import cockpit_editable_names, normalize_cockpit_knob_value

    for knob in ROUND_POLICY_KNOBS.values():
        assert knob in cockpit_editable_names()
        assert normalize_cockpit_knob_value(knob, " 0 ") == "0"
        assert normalize_cockpit_knob_value(knob, "250") == "250"
        assert normalize_cockpit_knob_value(knob, "Vertical") == "vertical"
        for bad in ("-1", "lots", "2.5"):
            with pytest.raises(ValueError):
                normalize_cockpit_knob_value(knob, bad)


def test_persisted_cockpit_knob_applies() -> None:
    persisted = {"ARGUS_SKILL_HARD_ESCALATE_ROUNDS": "7"}
    policy = resolve_round_policy(None, env=_NO_KNOBS, persisted=persisted)
    assert policy.hard_escalate_rounds == 7


def test_parse_rejects_negative_and_non_integer() -> None:
    with pytest.raises(RoundPolicyError):
        parse_round_policy("v", {"no_progress_threshold": True})
    with pytest.raises(RoundPolicyError):
        parse_round_policy("v", {"hard_escalate_rounds": -1})


# ---------------------------------------------------------------------------
# No count-based guard ends a mission while the Reviewer reports progress
# ---------------------------------------------------------------------------


def _progress_review(forward_progress: bool | None) -> ReviewDecision:
    report = {} if forward_progress is None else {"forward_progress": forward_progress}
    return ReviewDecision(
        status="continue",
        reason="Still open.",
        next_action="Keep going.",
        planner_report=report,
    )


def test_classify_never_ends_on_counts_while_progress_is_reported() -> None:
    status, reason = RoundSettlementMixin._classify(
        blocked_on_healthy_work=True,
        review=_progress_review(True),
        no_progress_streak=50,
        no_progress_threshold=2,
        semantic_stall_streak=0,
        stall_threshold=4,
        round_index=500,
        max_rounds=0,
        hard_escalate_rounds=24,
        soft_limit_stalled=False,
        soft_round_limit=12,
    )
    assert (status, reason) == (None, "")


def test_empty_turns_still_end_a_mission_without_reported_progress() -> None:
    for forward_progress in (None, False):
        status, _reason = RoundSettlementMixin._classify(
            review=_progress_review(forward_progress),
            no_progress_streak=2,
            no_progress_threshold=2,
            round_index=3,
            max_rounds=0,
        )
        assert status == "no_progress"


def test_zero_disables_the_empty_turn_guard() -> None:
    status, _reason = RoundSettlementMixin._classify(
        review=_progress_review(None),
        no_progress_streak=9,
        no_progress_threshold=0,
        round_index=10,
        max_rounds=0,
    )
    assert status is None


def _review_action(status: str, forward_progress: bool | None) -> tuple[str, dict]:
    payload: dict[str, object] = {
        "review": "Done." if status == "done" else "Still open.\nDo the next step.",
    }
    if forward_progress is not None:
        payload["forward_progress"] = forward_progress
    return ("approve_review" if status == "done" else "revise_review"), payload


def test_progressing_mission_runs_past_every_round_guard(tmp_path) -> None:
    """Tight guards, many rounds: reported progress keeps the mission alive."""
    backend = MemoryBackend()
    total = 9
    for index in range(1, total + 1):
        backend.queue(
            f"engineer-r{index}",
            CannedResponse(message=f"round {index} moved the work forward"),
        )
        done = index == total
        # Alternate true/false so the two-verdict window always holds one true
        # verdict and the explicit no-progress streak never reaches two.
        progress = True if done or index % 2 else False
        backend.queue(
            "reviewer",
            CannedResponse(review_action=_review_action("done" if done else "continue", progress)),
        )
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )

    status, rounds, _final, _reason, _thread = engineer.run(
        objective="Keep verifying until the system is green.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            stall_threshold=2,
            soft_round_limit=2,
            hard_escalate_rounds=3,
            decision_progress_timeout_seconds=0,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert len(rounds) == total


def test_progress_claim_without_running_work_does_not_excuse_empty_turns() -> None:
    status, _reason = RoundSettlementMixin._classify(
        review=_progress_review(True),
        no_progress_streak=2,
        no_progress_threshold=2,
        round_index=3,
        max_rounds=0,
        blocked_on_healthy_work=False,
    )
    assert status == "no_progress"


def _empty_engineer_run(tmp_path, *, rounds: int, final_done: bool):
    backend = MemoryBackend()
    for index in range(1, rounds + 1):
        backend.queue(f"engineer-r{index}", CannedResponse(message=""))
        done = final_done and index == rounds
        backend.queue(
            "reviewer",
            CannedResponse(review_action=_review_action("done" if done else "continue", True)),
        )
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    return engineer.run(
        objective="Wait for the verification job and report.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            no_progress_threshold=2,
            decision_progress_timeout_seconds=0,
            background_subagent_advisory=False,
        ),
        workdir=tmp_path,
    )


def test_empty_engineer_with_healthy_background_work_keeps_running(
    monkeypatch, tmp_path
) -> None:
    from argus.engineer import external_work
    from argus.engineer.external_work import ExternalWorkState, ExternalWorkStatus

    healthy = ExternalWorkStatus(work_id="verify", state=ExternalWorkState.RUNNING_HEALTHY)
    monkeypatch.setattr(external_work, "scan_external_work", lambda *_a, **_k: [healthy])

    status, rounds, *_rest = _empty_engineer_run(tmp_path, rounds=5, final_done=True)

    assert status == "done"
    assert len(rounds) == 5


def test_empty_engineer_without_background_work_ends_despite_progress_claims(
    tmp_path,
) -> None:
    status, rounds, _final, reason, _thread = _empty_engineer_run(
        tmp_path, rounds=6, final_done=False
    )

    assert status == "no_progress"
    assert len(rounds) == 2
    assert "no effective output for 2 consecutive rounds" in reason
