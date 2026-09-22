"""Behavioural receipts shared with the experimental TypeScript Pi adapter."""
import json
from pathlib import Path

import pytest

from argus.agent_cli._event_consumers import EventConsumerMixin, _OpenCodeWriteState

FIXTURES = json.loads(
    (Path(__file__).parents[2] / "packages/runtime/fixtures/pi-events.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture["id"])
def test_shared_pi_receipt(fixture: dict) -> None:
    messages: list[str] = []
    state = _OpenCodeWriteState()
    thread, completed, failed, error = None, False, False, None
    for event in fixture["events"]:
        thread, completed, failed, error = EventConsumerMixin._consume_pi_event(
            event=event,
            thread_id=thread,
            agent_messages=messages,
            write_state=state,
            turn_completed=completed,
            turn_failed=failed,
            fatal_error=error,
        )
    assert {
        "threadId": thread, "agentMessages": messages, "turnCompleted": completed,
        "turnFailed": failed, "fatalError": error,
    } == fixture["expected"]
