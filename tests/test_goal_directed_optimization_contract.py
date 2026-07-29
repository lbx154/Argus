from __future__ import annotations

import json

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.roles.prompts.planner import _EXTERNAL_TARGET_CONTRACT
from argus_skill.roles.prompts.engineer import MISSION, mission_request
from argus_skill.roles.prompts.registry import resolve_role_prompt
from argus_skill.skills.skill_prompts import Prompts
from argus_skill.skills.store import Skill, SkillStore
from argus_skill.skills.vertical_select import persist_vertical


def test_planner_contract_prioritizes_external_target_over_local_incumbent() -> None:
    assert "Operator success" in _EXTERNAL_TARGET_CONTRACT
    assert "current local" in _EXTERNAL_TARGET_CONTRACT
    assert "TASK_IMPACT_SCORE=1..5" in _EXTERNAL_TARGET_CONTRACT
    assert "Public task-specific" in _EXTERNAL_TARGET_CONTRACT
    assert "reject semantic duplicates" in _EXTERNAL_TARGET_CONTRACT
    assert "overrides incompatible vertical" in _EXTERNAL_TARGET_CONTRACT


def test_external_gate_suppresses_incompatible_speedrun_banner(
    tmp_path,
    monkeypatch,
) -> None:
    persist_vertical(tmp_path, "speedrun")
    without_gate = resolve_role_prompt(mission_request(tmp_path))
    assert "INVENT" in without_gate.role_banner

    monkeypatch.setenv(
        "ARGUS_SKILL_EXTERNAL_COMPLETION_GATE",
        "MLE_MEDAL_GATE.json:satisfied",
    )
    with_gate = resolve_role_prompt(mission_request(tmp_path))

    assert with_gate.operation == MISSION
    assert with_gate.role_banner == ""
    assert with_gate.stage_order


def test_skill_matcher_sees_and_uses_negative_reuse_evidence() -> None:
    prompt = Prompts.skill_match(
        "improve the primary benchmark score",
        [
            {
                "candidate_id": "bad-skill",
                "skill_id": "bad-skill",
                "name": "Bad Skill",
                "description": "optimizes a secondary metric",
                "category": "optimization",
                "successful_reuses": 1,
                "failed_reuses": 3,
            }
        ],
    )
    assert "1 successful, 3 ineffective" in prompt
    assert "ineffective uses exceed successful uses" in prompt
    assert "cannot override the operator task" in prompt


def test_repeatedly_ineffective_skill_is_quarantined_before_matching(tmp_path) -> None:
    skill = Skill(
        name="Poisoned optimization",
        description="polishes a secondary metric",
        category="optimization",
        content="# Poisoned optimization\n",
        successful_reuses=1,
        failed_reuses=2,
    )
    backend = MemoryBackend()
    backend.queue(
        "matcher",
        CannedResponse(message=json.dumps({"matched": []})),
    )
    store = SkillStore(tmp_path, runner=backend, matcher_model="m")
    store.save(skill)
    events: list[dict] = []

    matched, _tokens = store.find_relevant(
        "improve the primary benchmark score",
        on_event=events.append,
        force_empty_match=True,
    )

    assert matched is None
    assert any("quarantined 1" in event.get("text", "") for event in events)
