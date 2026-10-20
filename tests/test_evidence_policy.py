from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.knob_store import write_persisted_knob
from argus.core.knobs import (
    cockpit_editable_names,
    format_config_help,
    normalize_cockpit_knob_value,
    resolve_evidence_policy,
)
from argus.core.round_policy import RoundPolicy, explain_round_policy
from argus.reviewer import Reviewer
from argus.roles.prompts.engineer import build_mission_prompt
from argus.roles.prompts.manager import build_stage_decision_prompt
from argus.roles.task_contract import experiment_evidence_contract

KNOB = "ARGUS_SKILL_EVIDENCE_POLICY"


def test_public_settings_consumer_persists_and_validates_policy(monkeypatch, tmp_path):
    from argus.webapi.mission_items import set_operator_config

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "config"))
    monkeypatch.setenv(KNOB, "standard")
    response = set_operator_config(KNOB, "progress-first")
    assert response["value"] == "progress-first"
    assert response["restart_required"] is True
    assert resolve_evidence_policy() == "progress-first"
    assert resolve_evidence_policy(env={}) == "progress-first"
    with pytest.raises(ValueError, match="progress-first, standard, or strict"):
        set_operator_config(KNOB, "anything")
    assert resolve_evidence_policy(env={}) == "progress-first"


@pytest.mark.parametrize("mode", ["progress-first", "standard", "strict"])
def test_saved_policy_reaches_configuration_and_all_roles(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(KNOB, raising=False)
    assert KNOB in cockpit_editable_names()
    assert normalize_cockpit_knob_value(KNOB, mode.upper()) == mode
    assert write_persisted_knob(KNOB, mode)
    assert resolve_evidence_policy() == mode
    assert f"= {mode} (persisted)" in format_config_help()
    contract = experiment_evidence_contract()
    prompts = [
        build_mission_prompt(task="Repair the failing setup step.", skill_text="", next_action=None),
        build_mission_prompt(
            task="Repair the failing setup step.", skill_text="", next_action=None, compact_team=True,
        ),
        build_stage_decision_prompt(
            current_stage="implement", next_stage="verify", earlier_stages=[],
            checklist_md="Check this stage's actual deliverable.",
            review=SimpleNamespace(status="done", reason="Current evidence accepted."),
        ),
        Reviewer(runner=None)._build_prompt(
            objective="Repair the failing setup step.", operator_messages=[],
            planner_review_instruction="", round_index=1, session_id=None,
            main_summary="Repaired and checked the failing step.", main_error=None,
            prior_checkpoint={},
        ),
    ]
    assert all(prompt.count(contract) == 1 for prompt in prompts)
    assert "required independent validation" in contract
    monkeypatch.setenv(KNOB, "strict" if mode != "strict" else "standard")
    assert resolve_evidence_policy() != mode


@pytest.mark.parametrize("bad", ["loose", "false", "progress_first"])
def test_invalid_policy_fails_explicitly(bad):
    with pytest.raises(ValueError, match="progress-first, standard, or strict"):
        normalize_cockpit_knob_value(KNOB, bad)
    with pytest.raises(ValueError, match="progress-first, standard, or strict"):
        resolve_evidence_policy(env={KNOB: bad}, persisted={})
    with pytest.raises(ValueError, match="progress-first, standard, or strict"):
        explain_round_policy(env={}, persisted={KNOB: bad})


def test_profile_preserves_disabled_guards_and_explicit_choices():
    policy, sources = explain_round_policy(
        RoundPolicy(stall_threshold=10, soft_round_limit=0, hard_escalate_rounds=100),
        env={KNOB: "progress-first"}, persisted={},
    )
    assert policy == RoundPolicy(2, 2, 0, 100)
    assert sources["stall_threshold"] == "evidence policy progress-first"
    policy, sources = explain_round_policy(
        explicit=RoundPolicy(stall_threshold=5, soft_round_limit=8),
        env={KNOB: "progress-first", "ARGUS_SKILL_STALL_THRESHOLD": "0"}, persisted={},
    )
    assert policy.stall_threshold == 0
    assert policy.soft_round_limit == 8
    assert sources["soft_round_limit"] == "mission loop config"
    assert sources["stall_threshold"].endswith("(env)")


@pytest.mark.parametrize(("mode", "progress", "expected_rounds"), [
    ("progress-first", False, 2), ("standard", False, 4), ("strict", False, 4),
    ("progress-first", True, 6),
])
def test_saved_policy_controls_real_skill_loop_without_accepting_failed_work(
    monkeypatch, tmp_path, mode, progress, expected_rounds,
):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(KNOB, raising=False)
    assert write_persisted_knob(KNOB, mode)
    backend = MemoryBackend()
    for index in range(1, 7):
        backend.queue(f"engineer-r{index}", CannedResponse(message=f"Setup step result {index}."))
        done = progress and index == 6
        backend.queue("reviewer", CannedResponse(review_action=(
            "approve_review" if done else "revise_review",
            {"review": "Setup accepted." if done else "The same setup error remains.",
             "forward_progress": progress},
        )))
    loop = SkillLoop(
        skills_dir=tmp_path / "skills", engineer_runner=backend, reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="fixture", reviewer_model="fixture",
            workflow_mode="direct", active_vertical="software",
            role_session_policy="fresh", max_rounds=8, session_id="evidence-policy-fixture",
            require_independent_review=True, require_post_task_learning=False,
            wiki_enabled=False, auto_init_wiki=False,
        ),
    )
    result = loop.run("Repair the setup error, then check the affected step.", workdir=tmp_path)
    assert result.successful is progress
    turns = [(label, prompt) for label, prompt, _ in backend.history if label.startswith("engineer-r")]
    assert len(turns) == expected_rounds
    assert f"Evidence policy {mode}:" in turns[0][1]
    if not progress:
        assert result.status == "no_progress"
