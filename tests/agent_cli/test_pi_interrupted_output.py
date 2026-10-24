from argus.agent_cli._event_consumers import _OpenCodeWriteState
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.runner_backend import BACKEND_PI


def test_pi_error_update_keeps_stream_open_for_the_authoritative_snapshot() -> None:
    runner = AgentCliRunner(agent_bin="pi", backend=BACKEND_PI)
    messages: list[str] = []
    write_state = _OpenCodeWriteState()
    state = (None, False, False, None)
    events = [
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": "draft"},
        },
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "error", "errorMessage": "failed"},
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "authoritative partial"}],
                "stopReason": "error",
                "errorMessage": "failed",
            },
        },
    ]
    for event in events:
        state = runner._consume_event(
            event=event, thread_id=state[0], agent_messages=messages,
            write_state=write_state, turn_completed=state[1], turn_failed=state[2],
            fatal_error=state[3],
        )

    assert state == (None, False, True, "failed")
    assert messages == ["authoritative partial"]
