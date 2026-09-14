"""Manager SELF failures remain structured failures, not successful chat receipts."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.manager.front_door import manager_triage


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))


class OutcomeRunner:
    last_thread_id = "fixture-thread"

    def __init__(self, events, outcome=None):
        self.events = events
        self.last_chat_outcome = outcome
        self.calls = 0

    def chat_reply_if_conversational(self, **kwargs):
        self.calls += 1
        for event in self.events:
            kwargs["sink"].handle_event(event)
        return True


def triage(runner, state=None, fragments=None):
    state = state if state is not None else {}
    return manager_triage(
        object(), "Synthetic request", state, route="simple",
        ensure_runner=lambda *_: runner,
        on_fragment=lambda kind, payload: fragments.append((kind, payload)) if fragments is not None else None,
    )


def test_failed_outcome_does_not_publish_partial_reply_or_stale_delivery():
    runner = OutcomeRunner([
        {"type": "round.main.completed", "exit_code": 0, "turn_completed": False, "last_message": "Partial model text"},
    ], SimpleNamespace(success=False, stop_reason="Synthetic provider stream failure", delivery={"summary": "not delivered"}))
    state = {"_self_delivery": {"summary": "old"}}
    fragments = []
    reply = triage(runner, state, fragments)
    assert "Synthetic provider stream failure" in reply
    assert "Partial model text" not in reply
    assert state["_self_failure"]["detail"] == "Synthetic provider stream failure"
    assert "_self_delivery" not in state
    assert not any("Partial model text" in str(payload) for _, payload in fragments)


def test_legacy_runner_explicit_failed_round_is_not_a_successful_chat():
    runner = OutcomeRunner([
        {"type": "round.main.completed", "exit_code": 1, "fatal_error": "Synthetic local failure", "last_message": "partial"},
    ])
    state = {}
    assert "Synthetic local failure" in triage(runner, state)
    assert state["_self_failure"]


def test_empty_handled_turn_is_a_structured_failure():
    state = {}
    reply = triage(OutcomeRunner([]), state)
    assert reply.startswith("[Manager reply unavailable]")
    assert reply.count("[Manager reply unavailable]") == 1
    assert state["_self_failure"]


def test_internal_type_error_is_not_retried_as_a_legacy_signature():
    class BrokenRunner:
        calls = 0
        def chat_reply_if_conversational(self, **kwargs):
            self.calls += 1
            raise TypeError("Synthetic error after execution began")
    runner = BrokenRunner()
    state = {}
    triage(runner, state)
    assert runner.calls == 1
    assert "Synthetic error after execution began" in state["_self_failure"]["detail"]


def test_legacy_signature_is_selected_before_calling_once():
    class LegacyRunner:
        calls = 0
        last_thread_id = "legacy-thread"
        def chat_reply_if_conversational(self, objective, sink, seed_thread_id):
            self.calls += 1
            sink.handle_event({"type": "round.main.completed", "last_message": "Legacy final reply"})
            return True
    runner = LegacyRunner()
    assert triage(runner) == "Legacy final reply"
    assert runner.calls == 1


def test_latest_successful_completion_replaces_an_earlier_round_reply():
    runner = OutcomeRunner([
        {"type": "round.main.completed", "exit_code": 0, "last_message": "Earlier round reply"},
        {"type": "round.main.completed", "exit_code": 0, "last_message": "Final round reply"},
    ], SimpleNamespace(success=True, delivery=None))
    state = {"_self_failure": {"detail": "stale prior failure"}}
    assert triage(runner, state) == "Final round reply"
    assert "_self_failure" not in state


def test_recovered_round_does_not_keep_a_stale_failure():
    runner = OutcomeRunner([
        {"type": "round.main.completed", "exit_code": 1, "fatal_error": "Earlier round failed", "last_message": "partial"},
        {"type": "round.main.completed", "exit_code": 0, "turn_completed": True, "last_message": "Recovered final reply"},
    ], SimpleNamespace(success=True, delivery=None))
    state = {}
    assert triage(runner, state) == "Recovered final reply"
    assert "_self_failure" not in state


def test_successful_prose_about_failures_is_not_reclassified_as_an_error():
    reply = "Documentation explains failures, insufficient quota, and request errors."
    runner = OutcomeRunner([{"type": "round.main.completed", "exit_code": 0, "last_message": reply}],
                           SimpleNamespace(success=True, delivery=None))
    state = {}
    assert triage(runner, state) == reply
    assert "_self_failure" not in state
