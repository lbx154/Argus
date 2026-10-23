

from argus.manager.domain_intake import handle_intake, read_intake


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
        fields["options"] = ["Cultural education", "Date conversion"]
    return handle_intake(root, message, {"action": action, **fields}, route="simple",
                         self_mode="reply", known_verticals=("research", "software"))


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


def test_manager_can_prepare_after_explicit_consent_when_requirements_are_already_known(tmp_path):
    turn(tmp_path, "For a cultural education handout, compare documented calendar conventions and cite sources", "offer")
    ready = turn(tmp_path, "Build this workflow using those requirements", "prepare", name="calendar_conventions")
    assert ready["route"] == "complex"
    assert read_intake(tmp_path)["consented"] is True
    assert not (tmp_path / "research").exists()


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
