from __future__ import annotations

import json

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.roles.prompts.planner import _EXTERNAL_TARGET_CONTRACT
from argus_skill.skills.skill_prompts import Prompts
from argus_skill.skills.store import Skill, SkillStore


def test_planner_contract_prioritizes_external_target_over_local_incumbent() -> None:
    assert "Operator success" in _EXTERNAL_TARGET_CONTRACT
    assert "current local" in _EXTERNAL_TARGET_CONTRACT
    assert "TASK_IMPACT_SCORE=1..5" in _EXTERNAL_TARGET_CONTRACT
    assert "Public task-specific" in _EXTERNAL_TARGET_CONTRACT


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
