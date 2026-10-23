from __future__ import annotations

from types import SimpleNamespace

from argus.core.knob_store import write_persisted_knob
from argus.core.knobs import cockpit_editable_names, format_config_help
from argus.reviewer import Reviewer
from argus.roles.prompts.engineer import build_mission_prompt
from argus.roles.prompts.manager import build_stage_decision_prompt
from argus.roles.task_contract import EXPERIMENT_EVIDENCE_CONTRACT
from tests.role_rules import lists, rule

RETIRED_KNOB = "ARGUS_SKILL_EVIDENCE_POLICY"


def test_every_role_gets_the_one_evidence_contract_and_old_policy_is_ignored(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "config"))
    monkeypatch.setenv(RETIRED_KNOB, "strict")
    assert write_persisted_knob(RETIRED_KNOB, "progress-first")
    assert RETIRED_KNOB not in cockpit_editable_names()
    assert RETIRED_KNOB not in format_config_help()
    for compact_team in (False, True):
        assert lists(build_mission_prompt(
            task="Repair the failing setup step.", skill_text="", next_action=None, compact_team=compact_team,
        ), "long-runs-and-evidence/SKILL.md")
    assert lists(Reviewer(runner=None)._build_prompt(
        objective="Repair the failing setup step.", operator_messages=[],
        planner_review_instruction="", round_index=1, session_id=None,
        main_summary="Repaired and checked the failing step.", main_error=None,
        prior_checkpoint={},
    ), "checking-the-work/SKILL.md")
    for role, name in (("engineer", "long-runs-and-evidence/SKILL.md"), ("reviewer", "checking-the-work/SKILL.md")):
        assert "readiness never replaces" in rule(role, name)
    prompts = [
        build_stage_decision_prompt(
            current_stage="implement", next_stage="verify", earlier_stages=[],
            checklist_md="Check this stage's actual deliverable.",
            review=SimpleNamespace(status="done", reason="Current evidence accepted."),
        ),
    ]
    assert all(prompt.count(EXPERIMENT_EVIDENCE_CONTRACT) == 1 for prompt in prompts)
    assert "required independent validation" in EXPERIMENT_EVIDENCE_CONTRACT
