from __future__ import annotations

import json

from argus_skill.manager.self_maintenance import (
    build_maintenance_prompt,
    parse_maintenance_decision,
)


def test_repair_requires_observed_evidence_and_bounded_fields() -> None:
    decision = parse_maintenance_decision(
        json.dumps({
            "action": "repair",
            "reason": "planner wait never reaches Manager",
            "problem": "event waits bypass reconciliation",
            "title": "Repair event-wait reconciliation",
            "objective": "Route unchanged event waits through Manager.",
            "acceptance_check": "pytest -q tests/life/test_planner_dag_enqueue.py",
            "evidence_ids": ["event-1", "invented"],
            "affected_paths": [
                "argus_skill/life/supervisor/_planning_context.py",
                "tests/life/test_planner_dag_enqueue.py",
            ],
        }),
        valid_evidence_ids=["event-1"],
    )

    assert decision.action == "repair"
    assert decision.evidence_ids == ("event-1",)
    assert decision.error == ""


def test_speculative_repair_without_evidence_fails_closed() -> None:
    decision = parse_maintenance_decision(
        json.dumps({
            "action": "repair",
            "reason": "might be cleaner",
            "problem": "possible future inefficiency",
            "title": "Refactor prompts",
            "objective": "Clean up prompts.",
            "acceptance_check": "pytest",
            "evidence_ids": ["invented"],
            "affected_paths": ["argus_skill/loop.py"],
        }),
        valid_evidence_ids=["event-1"],
    )

    assert decision.action == "no_action"
    assert decision.error == "incomplete_repair"


def test_adoption_requires_bound_human_merged_update_evidence() -> None:
    decision = parse_maintenance_decision(
        json.dumps({
            "action": "adopt",
            "reason": "the merged liveness fix matches this daemon",
            "acceptance_check": "clean supervisor pass",
            "evidence_ids": ["update-1"],
        }),
        valid_evidence_ids=["update-1"],
    )

    assert decision.action == "adopt"
    assert decision.evidence_ids == ("update-1",)


def test_prompt_forbids_make_work_and_requires_measured_prompt_evidence() -> None:
    prompt = build_maintenance_prompt(
        [{"id": "event-1", "type": "round.start", "details": {}}],
        daemon_state={"stopped_by": "awaiting_external"},
        framework_root="/repo",
    )

    assert "Do not invent cleanup, speculative refactors" in prompt
    assert "measured token or prompt-block evidence" in prompt
    assert "independent Reviewer" in prompt
    assert "human-merged `framework.update_available`" in prompt
