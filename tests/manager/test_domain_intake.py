from types import SimpleNamespace

from argus.life.router import classify_front_door
from argus.manager.domain_intake import handle_intake, intake_prompt, read_intake


def turn(root, message, action, **fields):
    return handle_intake(root, message, {"action": action, **fields}, route="simple",
                         self_mode="reply", known_verticals=("research", "software"))


def test_unknown_domain_requires_opt_in_and_clarification_before_preparing(tmp_path):
    first = turn(tmp_path, "Interpret a traditional calendar date", "offer")
    assert "one agent" in first["reply"]
    assert read_intake(tmp_path)["consented"] is False
    assert not (tmp_path / "research").exists()

    question = turn(tmp_path, "Yes, develop it", "prepare", name="calendar_interpretation")
    assert "What should it produce" in question["reply"]
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
    assert decisions == [{"action": "ask", "name": "", "question": "What output should it produce?"}]


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
