"""Manager SELF streaming regressions."""

from __future__ import annotations

import json

import pytest

from argus_skill.manager.front_door import manager_triage


class _StreamingRunner:
    last_thread_id = "thread-1"

    def __init__(self, reply_kind: str) -> None:
        self.reply_kind = reply_kind

    def chat_reply_if_conversational(self, **kwargs) -> bool:  # noqa: ANN001
        sink = kwargs["sink"]
        sink.handle_event({
            "type": "engineer.progress",
            "kind": self.reply_kind,
            "text": "streamed Manager reply",
            "message_id": "reply-1",
        })
        sink.handle_event({
            "type": "round.main.completed",
            "last_message": "streamed Manager reply",
        })
        return True


class _SecretReplyRunner:
    last_thread_id = "thread-1"

    def chat_reply_if_conversational(self, **kwargs) -> bool:  # noqa: ANN001
        secret = "ghp_" + "A" * 36
        sink = kwargs["sink"]
        sink.handle_event({
            "type": "engineer.progress",
            "kind": "assistant_message",
            "text": f"streamed token {secret}",
            "message_id": "reply-1",
        })
        sink.handle_event({
            "type": "round.main.completed",
            "last_message": f"streamed token {secret}",
        })
        return True


class _LegacySecretProgressRunner:
    last_thread_id = "thread-1"

    def chat_reply_if_conversational(self, objective, sink, seed_thread_id):  # noqa: ANN001
        secret = "ghp_" + "A" * 36
        sink.handle_event({
            "type": "engineer.progress",
            "kind": "command_execution",
            "text": f"curl -H 'Authorization: token {secret}' https://api.github.com",
            "agent_layer": "manager",
        })
        sink.handle_event({
            "type": "round.main.completed",
            "last_message": "legacy fallback reply",
        })
        return True


class _NarratingRunner:
    last_thread_id = "thread-1"

    def chat_reply_if_conversational(self, **kwargs) -> bool:  # noqa: ANN001
        sink = kwargs["sink"]
        for text, message_id in (
            ("I am reading the logs.", "reply-progress-1"),
            ("I found the relevant file.", "reply-progress-2"),
            ("The final answer is concise.", "reply-final"),
        ):
            sink.handle_event({
                "type": "engineer.progress",
                "kind": "agent_message",
                "text": text,
                "message_id": message_id,
            })
        sink.handle_event({
            "type": "round.main.completed",
            "last_message": "The final answer is concise.",
        })
        return True


@pytest.mark.parametrize("reply_kind", ["assistant_message", "agent_message", "message"])
def test_manager_triage_streams_all_reply_progress_kinds(reply_kind: str) -> None:
    fragments: list[tuple[str, dict]] = []

    reply = manager_triage(
        object(),
        "status?",
        {},
        ensure_runner=lambda _chat_state, _mem: _StreamingRunner(reply_kind),
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert reply == "streamed Manager reply"
    assert fragments == [
        (
            "delta",
            {
                "text": "streamed Manager reply",
                "message_id": "reply-1",
                "fragment_mode": "snapshot",
                "live": True,
            },
        ),
        (
            "delta",
            {
                "text": "streamed Manager reply",
                "message_id": "reply-1",
                "fragment_mode": "snapshot",
            },
        ),
    ]


def test_manager_triage_redacts_direct_reply_progress_before_streaming() -> None:
    secret = "ghp_" + "A" * 36
    fragments: list[tuple[str, dict]] = []

    reply = manager_triage(
        object(),
        "status?",
        {},
        ensure_runner=lambda _chat_state, _mem: _SecretReplyRunner(),
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    payload = json.dumps({"reply": reply, "fragments": fragments}, ensure_ascii=False)
    assert secret not in payload
    assert "REDACTED" in payload


def test_manager_triage_streams_narration_live_then_the_authoritative_final_answer() -> None:
    fragments: list[tuple[str, dict]] = []

    reply = manager_triage(
        object(),
        "inspect it",
        {},
        ensure_runner=lambda _chat_state, _mem: _NarratingRunner(),
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert reply == "The final answer is concise."
    # Every assistant message is shown as it arrives, each one a snapshot of
    # its own message id, so the operator watches the text type in and later
    # narration replaces earlier narration rather than being glued to it.
    live = [
        (
            "delta",
            {"text": text, "message_id": message_id, "fragment_mode": "snapshot", "live": True},
        )
        for text, message_id in (
            ("I am reading the logs.", "reply-progress-1"),
            ("I found the relevant file.", "reply-progress-2"),
            ("The final answer is concise.", "reply-final"),
        )
    ]
    assert fragments == [
        *live,
        ("delta", {"text": reply, "message_id": "reply-final", "fragment_mode": "snapshot"}),
    ]


class _StructuredReplyRunner:
    last_thread_id = "thread-1"

    def chat_reply_if_conversational(self, **kwargs) -> bool:  # noqa: ANN001
        sink = kwargs["sink"]
        sink.handle_event({
            "type": "engineer.progress",
            "kind": "agent_message",
            "text": '{"reply": "half a JSON',
            "message_id": "reply-json",
        })
        sink.handle_event({
            "type": "round.main.completed",
            "last_message": '{"reply": "the whole answer"}',
        })
        return True


def test_manager_triage_does_not_stream_a_half_written_structured_reply() -> None:
    fragments: list[tuple[str, dict]] = []

    reply = manager_triage(
        object(),
        "inspect it",
        {},
        ensure_runner=lambda _chat_state, _mem: _StructuredReplyRunner(),
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert reply == "the whole answer"
    assert fragments == [
        ("delta", {"text": "the whole answer", "message_id": "reply-json", "fragment_mode": "snapshot"}),
    ]


class _ToolPhaseRunner:
    last_thread_id = "thread-1"

    def chat_reply_if_conversational(self, **kwargs) -> bool:  # noqa: ANN001
        phase_cb = kwargs["phase_cb"]
        phase_cb(
            "$ wc -l README.md",
            role="manager",
            kind="command_execution",
            detail="",
            meta={"tool_name": "Count README lines", "call_id": "t1", "tool_kind": "execute", "status": "running"},
        )
        phase_cb(
            "↳ Count README lines · completed",
            role="manager",
            kind="tool_result",
            detail="1 README.md",
            meta={"tool_name": "Count README lines", "call_id": "t1", "status": "completed", "output_excerpt": "1 README.md"},
        )
        kwargs["sink"].handle_event({"type": "round.main.completed", "last_message": "one line"})
        return True


def test_manager_triage_carries_tool_facts_on_phase_frames() -> None:
    fragments: list[tuple[str, dict]] = []

    manager_triage(
        object(),
        "count it",
        {},
        ensure_runner=lambda _chat_state, _mem: _ToolPhaseRunner(),
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    phases = [payload for kind, payload in fragments if kind == "phase" and payload.get("call_id")]
    assert phases == [
        {
            "role": "manager",
            "label": "$ wc -l README.md",
            "kind": "command_execution",
            "tool": "Count README lines",
            "call_id": "t1",
            "tool_kind": "execute",
            "status": "running",
        },
        {
            "role": "manager",
            "label": "↳ Count README lines · completed",
            "kind": "tool_result",
            "detail": "1 README.md",
            "tool": "Count README lines",
            "call_id": "t1",
            "status": "completed",
            "output": "1 README.md",
        },
    ]


def test_manager_triage_redacts_legacy_progress_phase_fallback() -> None:
    secret = "ghp_" + "A" * 36
    fragments: list[tuple[str, dict]] = []

    manager_triage(
        object(),
        "status?",
        {},
        ensure_runner=lambda _chat_state, _mem: _LegacySecretProgressRunner(),
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    payload = json.dumps(fragments, ensure_ascii=False)
    assert secret not in payload
    assert "REDACTED" in payload
