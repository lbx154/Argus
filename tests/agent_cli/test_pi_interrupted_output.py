from argus.agent_cli._event_consumers import _OpenCodeWriteState
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.runner_backend import BACKEND_PI


def test_pi_partial_output_remains_captured_before_interrupt() -> None:
    runner = AgentCliRunner(agent_bin="pi", backend=BACKEND_PI)
    messages: list[str] = []
    write_state = _OpenCodeWriteState()
    event = {
        "type": "message_update",
        "usage": {
            "input": 11,
            "output": 3,
            "cacheRead": 0,
            "cacheWrite": 0,
            "reasoning": 0,
        },
        "assistantMessageEvent": {
            "type": "text_delta",
            "contentIndex": 0,
            "delta": "partial-before-interrupt",
        },
    }

    state = runner._consume_event(
        event=event,
        thread_id="pi-session",
        agent_messages=messages,
        write_state=write_state,
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )

    assert state == ("pi-session", False, False, None)
    assert messages == ["partial-before-interrupt"]
    assert not runner._retain_json_event(event)


def test_pi_authoritative_receipts_replace_streamed_attempts_without_duplicates() -> None:
    runner = AgentCliRunner(agent_bin="pi", backend=BACKEND_PI)
    messages: list[str] = []
    write_state = _OpenCodeWriteState()
    state = ("pi-session", False, False, None)

    def consume(event: dict) -> None:
        nonlocal state
        state = runner._consume_event(
            event=event,
            thread_id=state[0],
            agent_messages=messages,
            write_state=write_state,
            turn_completed=state[1],
            turn_failed=state[2],
            fatal_error=state[3],
        )

    consume(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 0,
                "delta": "speculative work\nSTATUS=done",
            },
        }
    )
    consume(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [],
                "stopReason": "pending",
                "errorMessage": "retrying",
            },
        }
    )
    assert messages == []

    consume(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 0,
                "delta": "stale recovered draft",
            },
        }
    )
    consume(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "authoritative recovery"},
                    {"type": "text", "text": "STATUS=blocked"},
                ],
                "stopReason": "stop",
            },
        }
    )

    assert messages == ["authoritative recovery\nSTATUS=blocked"]

    consume({
        "type": "message_update",
        "assistantMessageEvent": {"type": "text_delta", "delta": "next draft"},
    })
    consume({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "authoritative recovery\nSTATUS=blocked"}],
            "stopReason": "stop",
        },
    })
    assert messages == ["authoritative recovery\nSTATUS=blocked"]


def test_pi_message_update_usage_snapshots_remain_filtered() -> None:
    runner = AgentCliRunner(agent_bin="pi", backend=BACKEND_PI)
    for usage in (
        None,
        {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
            "reasoning": 0,
        },
        {
            "input": 11,
            "output": 3,
            "cacheRead": 0,
            "cacheWrite": 0,
            "reasoning": 0,
        },
    ):
        event = {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 0,
                "delta": "streamed",
            },
        }
        if usage is not None:
            event["usage"] = usage
        assert not runner._retain_json_event(event)


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
