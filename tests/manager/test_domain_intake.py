from types import SimpleNamespace

import pytest

from argus.life.router import classify_front_door
from argus.manager.domain_intake import handle_intake, intake_prompt, read_intake


def test_legacy_pending_question_has_stable_actionable_card(tmp_path):
    import json

    from argus.manager.domain_intake import intake_answer, intake_card

    legacy = {"phase": "offered", "request": "给我分析一下这个日历", "answers": [], "last_question": "旧文本问题"}
    (tmp_path / "domain-intake.json").write_text(json.dumps(legacy))
    card = intake_card(read_intake(tmp_path))
    assert card == intake_card(read_intake(tmp_path))
    assert card["title"] == "选择处理方式"
    assert intake_answer(legacy, {"id": card["id"], "option_id": "direct", "note": "说明不确定性"}) == ("直接做\n说明不确定性", "skip")
    assert not (tmp_path / "backlog.jsonl").exists()


CAPABILITY_PURPOSE = "Explain traditional calendar conventions with source-backed cultural context"
CAPABILITY_BRIEF = """Scope: cultural education and calendar conventions; exclude personal predictions.
Inputs: date and calendar convention; request the convention if missing.
Outputs: cited explanation with uncertainty. Method: consult primary calendar references.
Checks: verify a second date and a missing-convention case against documented examples."""


def turn(root, message, action, **fields):
    if action == "prepare":
        fields.setdefault("purpose", CAPABILITY_PURPOSE)
        fields.setdefault("brief", CAPABILITY_BRIEF)
        fields.setdefault("title", "Build a reusable calendar workflow")
        fields.setdefault("summary", "Explain traditional calendar conventions with cited sources and reuse checks.")
    if action == "ask" and "options" not in fields:
        fields["options"] = [{"label": "Cultural education", "description": "Cite conventions and uncertainty"},
                             {"label": "Date conversion", "description": "Calendar table with sources"}]
    return handle_intake(root, message, {"action": action, **fields}, route="simple",
                         self_mode="reply", known_verticals=("research", "software"))


def test_unknown_domain_requires_opt_in_and_clarification_before_preparing(tmp_path):
    first = turn(tmp_path, "Interpret a traditional calendar date", "offer")
    assert "one agent" in first["reply"]
    assert read_intake(tmp_path)["consented"] is False
    assert not (tmp_path / "research").exists()

    question = turn(tmp_path, "Yes, develop it", "ask", question="Explain conventions or convert dates?")
    assert question["reply"] == "Explain conventions or convert dates?"
    assert read_intake(tmp_path)["phase"] == "clarifying"
    assert not (tmp_path / "research").exists()

    ready = turn(tmp_path, "For cultural education; show conventions and uncertainty", "prepare", name="calendar_interpretation")
    assert ready["route"] == "complex"
    assert ready["decision"].choice == "new"
    assert ready["decision"].vertical == "calendar_interpretation"
    assert ready["decision"].require_independent_review
    assert "First fetch relevant primary references" in ready["task"]
    assert "For cultural education" in ready["task"]
    assert "Reviewer Skills" in ready["task"]
    assert not (tmp_path / "research").exists(), "Only normal atomic task dispatch may create the domain"


def test_decline_executes_the_original_request_without_creating_a_domain(tmp_path):
    turn(tmp_path, "Original requested outcome", "offer")
    declined = turn(tmp_path, "Just do it directly", "skip")
    assert declined["route"] == "simple" and declined["self_mode"] == "reply"
    assert "Original requested outcome" in declined["task"]
    assert "decision" not in declined
    assert not (tmp_path / "research").exists()
    assert read_intake(tmp_path)["phase"] == "declined"


def test_cancel_does_not_execute_the_old_task_and_unrelated_chat_does_not_opt_in(tmp_path):
    turn(tmp_path, "Original task", "offer")
    assert turn(tmp_path, "What is a specialist workflow?", "none") is None
    assert read_intake(tmp_path)["consented"] is False
    assert turn(tmp_path, "Forget that; fix this file", "cancel") is None
    assert read_intake(tmp_path)["phase"] == "cancelled"


def test_classifier_cannot_prepare_a_new_domain_before_an_offer(tmp_path):
    result = turn(tmp_path, "A new unknown task", "prepare", name="unknown")
    assert "reply" in result and "decision" not in result
    assert read_intake(tmp_path)["consented"] is False


def test_pending_context_and_domain_decision_share_the_existing_classifier_call(tmp_path):
    turn(tmp_path, "Original task", "offer")
    prompts, decisions = [], []

    def run(prompt):
        prompts.append(prompt)
        return SimpleNamespace(exit_code=0, last_agent_message=(
            "ROUTE=SELF\nSELF_MODE=REPLY\nDOMAIN_ACTION=ASK\n"
            "DOMAIN_NAME=\nDOMAIN_QUESTION=What output should it produce?"
        ))

    result = classify_front_door("Yes", run_exec=run, domain_sink=decisions.append,
                                domain_prompt=intake_prompt({"research": "Scientific inquiry"}, read_intake(tmp_path)))
    assert len(prompts) == 1 and result[2] == "simple"
    assert "Original task" in prompts[0] and "Scientific inquiry" in prompts[0]
    assert decisions == [{"action": "ask"}]


def test_approved_domain_reuses_the_normal_dispatch_without_another_classifier(tmp_path):
    from argus.manager.front_door import prepare_manager_execution_task

    turn(tmp_path, "Original task", "offer")
    turn(tmp_path, "Yes", "ask", question="What should it produce?")
    ready = turn(tmp_path, "Cultural interpretation with explicit limits", "prepare", name="calendar")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Approved domain was classified a second time")

    state = {"_approved_domain_decision": ready["decision"]}
    prepared = prepare_manager_execution_task(
        SimpleNamespace(project_root=tmp_path), ready["task"], state,
        ensure_runner=lambda *_: SimpleNamespace(manager=SimpleNamespace(decide_vertical=unexpected)),
    )
    assert prepared.decision is ready["decision"]
    assert "_approved_domain_decision" not in state


def test_ask_without_manager_options_never_replaces_the_card(tmp_path):
    from argus.manager.domain_intake import IntakeDialogueError
    turn(tmp_path, "Interpret my calendar date", "offer")
    previous = read_intake(tmp_path)
    with pytest.raises(IntakeDialogueError):
        turn(tmp_path, "Build it", "ask", question="Who is this for?", options=[])
    assert read_intake(tmp_path) == previous


def test_model_choices_keep_their_meaning_and_enforce_required_details(tmp_path):
    from argus.manager.domain_intake import intake_answer, intake_card
    turn(tmp_path, "Interpret this date", "offer")
    turn(tmp_path, "Build it", "ask", question="Which level of precision?", options=[
        {"label": "Use the date only", "description": "Discuss broad cultural conventions."},
        {"id": "build", "label": "Supply the birth time", "description": "Use hour-level conventions.", "requires_note": True},
    ])
    state = read_intake(tmp_path)
    card = intake_card(state)
    assert [o["id"] for o in card["options"]] == ["option-1", "option-2"]
    text, action = intake_answer(state, {"id": card["id"], "option_id": "option-1"})
    assert action == "dialogue" and "broad cultural conventions" in text
    with pytest.raises(ValueError):
        intake_answer(state, {"id": card["id"], "option_id": "option-2"})
    text, action = intake_answer(state, {"id": card["id"], "option_id": "option-2", "note": "09:00"})
    assert action == "dialogue" and "09:00" in text


def test_manager_can_prepare_after_explicit_consent_when_requirements_are_already_known(tmp_path):
    turn(tmp_path, "For a cultural education handout, compare documented calendar conventions and cite sources", "offer")
    ready = turn(tmp_path, "Build this workflow using those requirements", "prepare", name="calendar_conventions")
    assert ready["route"] == "complex"
    assert read_intake(tmp_path)["consented"] is True
    assert not (tmp_path / "research").exists()


def test_preparing_only_a_name_cannot_claim_to_create_a_reusable_vertical(tmp_path):
    from argus.manager.domain_intake import IntakeDialogueError
    turn(tmp_path, "Explain this calendar date", "offer")
    before = read_intake(tmp_path)
    with pytest.raises(IntakeDialogueError):
        handle_intake(tmp_path, "Build it", {"action": "prepare", "name": "calendar"},
                      route="simple", self_mode="reply", known_verticals=())
    assert read_intake(tmp_path) == before


def test_reusable_contract_does_not_embed_the_validation_example(tmp_path):
    turn(tmp_path, "Interpret my date 2005-07-31", "offer")
    ready = turn(tmp_path, "Build a reusable calendar explanation vertical", "prepare", name="calendar")
    proposal = ready["decision"].proposal
    assert proposal.rationale == CAPABILITY_PURPOSE
    assert proposal.capability_brief == CAPABILITY_BRIEF
    assert "2005-07-31" not in proposal.capability_brief
    assert "different-input example" in ready["task"]
    assert "A good answer to the first request alone is insufficient" in ready["task"]
    assert ready["objective"].startswith("Build a reusable vertical:")
    assert read_intake(tmp_path)["brief"] == CAPABILITY_BRIEF
