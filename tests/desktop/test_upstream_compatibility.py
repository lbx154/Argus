"""Selected upstream protocol fixes without weakening the local no-replay policy."""
from __future__ import annotations

from argus.adapters.agent_cli_backend._result import _raw_backend_stop_kind
from argus.agent_cli._event_consumers import EventConsumerMixin


def consume(event, *, messages=None):
    return EventConsumerMixin._consume_copilot_event(
        event=event, thread_id="parent", agent_messages=messages if messages is not None else [],
        turn_completed=False, turn_failed=False, fatal_error=None,
    )


def test_copilot_query_retains_http_status_and_authentication_failures_are_permanent():
    state = consume({"type": "session.error", "data": {
        "errorType": "query", "statusCode": 400, "message": "invalid api key",
    }})
    assert state[2] and "HTTP 400" in state[3]
    assert _raw_backend_stop_kind(fatal_error=state[3], exit_code=1) == "permanent_error"
    assert _raw_backend_stop_kind(fatal_error="HTTP 503 unavailable", exit_code=1) == "transient_error"
    assert _raw_backend_stop_kind(fatal_error="HTTP 429 rate limited", exit_code=1) == "provider_cooldown"
