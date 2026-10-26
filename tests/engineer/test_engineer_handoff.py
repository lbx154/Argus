"""An Engineer turn and a Reviewer verdict go where their ``hand_off`` names; nothing else routes."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.round_routing import engineer_actions
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig
from argus.roles.prompts import engineer as engineer_prompts
from tests.hand_off import hand_off

_ACCEPTED = ("reviewer", CannedResponse(action=hand_off("done", "Holds.")))
_KEEP = {"question": "Keep the old date format?", "options": [{"label": "Keep it"}, {"label": "Switch to ISO"}]}


def _run(tmp_path: Path, *responses: tuple[str, CannedResponse], review: bool = True):
    backend = MemoryBackend()
    for label, response in responses:
        backend.queue(label, response)
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    status, rounds, _final, _reason, _tid = engineer.run(
        objective="repair the parser",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4, require_independent_review=review),
        workdir=tmp_path,
    )
    return status, rounds, [label for label, _prompt, _options in backend.history]


@pytest.mark.parametrize("to, status", [("planner", "planner"), ("manager", "manager")])
def test_the_engineer_hands_straight_to_the_planner_or_the_manager(tmp_path: Path, to: str, status: str) -> None:
    """Neither accepts work, so no review stands between the Engineer and them."""
    result, rounds, labels = _run(tmp_path, ("engineer-r1", CannedResponse(
        message="→ done", action=hand_off(to, "The task assumes a GPU this host lacks.", "No GPU here."),
    )))
    assert (labels, result) == (["engineer-r1"], status)
    review = rounds[0].review
    assert (review.status, review.review_source) == (status, "engineer_handoff")
    assert (review.reason, review.next_action) == ("The task assumes a GPU this host lacks.", "No GPU here.")


@pytest.mark.parametrize("message", ["Built the parser.\n→ done", "Fixed.\nDecision:\nMILESTONE_STATUS=done"])
def test_a_turn_without_a_handoff_goes_to_the_reviewer_never_done(tmp_path: Path, message: str) -> None:
    result, _rounds, labels = _run(tmp_path, ("engineer-r1", CannedResponse(message=message)), _ACCEPTED, review=False)
    assert (result, labels) == ("done", ["engineer-r1", "reviewer"])


def test_done_is_refused_where_review_is_required() -> None:
    actions = engineer_actions(SupervisedConfig(require_independent_review=True))
    with pytest.raises(ValueError, match="independent review"):
        actions.dispatch(*hand_off("done"))
    with pytest.raises(ValueError, match="to=operator goes with ask"):
        actions.dispatch(*hand_off("operator"))
    assert engineer_actions(SupervisedConfig(require_independent_review=False)).dispatch(*hand_off("done"))


def test_an_operator_question_parks_with_its_options(tmp_path: Path) -> None:
    result, rounds, labels = _run(tmp_path, ("engineer-r1", CannedResponse(
        action=hand_off("operator", "Two parsers fit.", ask=_KEEP),
    )))
    assert (result, labels) == ("operator", ["engineer-r1"])
    review = rounds[0].review
    assert (review.status, review.review_source, review.reason) == ("operator", "engineer_handoff", "Two parsers fit.")
    assert review.operator_question == "Keep the old date format?"
    assert [(option["id"], option["label"]) for option in review.operator_options] == [
        ("option-1", "Keep it"), ("option-2", "Switch to ISO"),
    ]


@pytest.mark.parametrize("operator", [True, False])
@pytest.mark.parametrize("include_static,compact_team", [(True, False), (False, False), (True, True)])
def test_every_engineer_prompt_asks_for_the_handoff_once_and_no_footer(
    monkeypatch: pytest.MonkeyPatch, include_static: bool, compact_team: bool, operator: bool,
) -> None:
    monkeypatch.setattr("argus.core.autonomy.operator_available", lambda: operator)
    monkeypatch.setattr(engineer_prompts, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer_prompts, "native_shell_summary", lambda: "")
    prompt = engineer_prompts.build_mission_prompt(
        task="Repair the parser.",
        skill_text="",
        next_action=None if include_static else "Handle empty input.",
        include_static=include_static,
        compact_team=compact_team,
    )
    assert prompt.count(engineer_prompts.engineer_handoff_rule()) == 1
    for legacy in (
        "→", "MILESTONE_STATUS", "NEXT_OWNER", "OPERATOR_NEED", "OPERATOR_OPTIONS",
        "ASSUMPTION", "id::label", "wait_for", "Decision:",
    ):
        assert legacy not in prompt
