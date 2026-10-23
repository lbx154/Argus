"""A Manager ticket that names a domain Argus does not know authors it.

The route comes from the ticket (``Manager.adopt_route``); committing it writes
the candidate domain, or, in ask mode, waits for the operator's confirmation.
"""
from __future__ import annotations

import json

from argus.manager import Manager
from argus.skills import stage_machine as sc
from argus.skills import vertical_select as vs
from argus.verticals import _data_domain as dd
from argus.verticals._data_domain import CANDIDATE_DOMAIN_STAGES

_TASK = "Build and evaluate the MuJoCo controller."


def _ticket(vertical: str, task: str = _TASK, **fields) -> dict:
    return {"vertical": vertical, "workflow_mode": "staged", "execution_task": task,
            "summary": "novel", "assumptions": [], **fields}


def _domains(root) -> list[str]:
    return sorted(path.name for path in (root / "research" / "DOMAINS").glob("*.json") if path.name != "INDEX.json")


def test_a_ticket_for_a_new_domain_authors_and_commits_it(tmp_path) -> None:
    mgr = Manager(project_root=tmp_path)

    division = mgr.commit_vertical_decision(_TASK, mgr.adopt_route(_ticket("robotics_sim")))

    assert (division.vertical, division.pending_confirmation) == ("robotics_sim", False)
    payload = json.loads((tmp_path / "research" / "DOMAINS" / "robotics_sim.json").read_text())
    assert payload["status"] == "candidate"
    assert payload["stages"] == list(CANDIDATE_DOMAIN_STAGES)
    assert payload["purpose"] == "novel"
    assert payload["require_independent_review"] is True
    assert division.learned_vertical_status == "candidate"
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"
    assert sc.current_stage(tmp_path) == CANDIDATE_DOMAIN_STAGES[0]


def test_ask_mode_writes_the_new_domain_only_once_confirmed(tmp_path) -> None:
    mgr = Manager(project_root=tmp_path)

    division = mgr.commit_vertical_decision(_TASK, mgr.adopt_route(_ticket("robotics_sim")), ask_on_new_domain=True)

    assert division.pending_confirmation is True and division.proposed_domain is not None
    assert not (tmp_path / "research" / "DOMAINS").exists()
    committed = mgr.commit_domain(division.task, division.proposed_domain)
    assert (committed.vertical, committed.execution_task) == ("robotics_sim", _TASK)
    assert vs.resolve_vertical(tmp_path) == "robotics_sim"


def test_a_ticket_reuses_a_candidate_domain_without_authoring_another(tmp_path) -> None:
    dd.write_data_domain(tmp_path, "embodied_eval_campaign", stages=["runtime_gate", "task_coverage", "evaluation"],
                         status="candidate", purpose="RoboTwin runtime, task coverage, and paired evaluation",
                         require_independent_review=True)
    mgr = Manager(project_root=tmp_path)
    task = "Continue the RoboTwin evaluation campaign."

    division = mgr.commit_vertical_decision(task, mgr.adopt_route(_ticket("embodied_eval_campaign", task)))

    assert division.vertical == "embodied_eval_campaign"
    assert _domains(tmp_path) == ["embodied_eval_campaign.json"]


def test_an_existing_data_domain_keeps_its_runtime_stages(tmp_path) -> None:
    dd.write_data_domain(tmp_path, "regulated_localization", stages=["translate"], status="formal",
                         purpose="regulated product localization")
    mgr = Manager(project_root=tmp_path)
    task = "Localize and release the regulated product UI."

    division = mgr.commit_vertical_decision(task, mgr.adopt_route(_ticket("regulated_localization", task)))

    assert division.stages == ["translate"]
    assert dd.load_data_domain("regulated_localization", tmp_path).STAGE_ORDER == ["translate"]
    assert sc.current_stage(tmp_path) == "translate"
    assert _domains(tmp_path) == ["regulated_localization.json"]
