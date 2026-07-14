"""Tests for ``adapters.stream_progress.make_stream_progress_callback``.

The callback wraps a sink so codex/copilot/claude stream-json lines
become structured ``engineer.progress`` events. These tests cover:

* Stream lines are always forwarded to ``sink.handle_stream_line``
  (audit-trail invariant).
* Engineer-role and ``main``-role stdout JSON ``item.completed`` events
  emit ``engineer.progress`` (LoopEngine uses ``main`` as the
  run_label; the legacy SkillLoop uses ``engineer``).
* User-visible hierarchy roles (reviewer / critic / planner) emit
  progress, but matcher / distiller do NOT — their stdout is protocol
  traffic.
* Stderr is never converted to progress events.
"""
from __future__ import annotations

import json
from typing import Any

from argus_skill.adapters.stream_progress import make_stream_progress_callback


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.streams: list[tuple[str, str]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def handle_stream_line(self, stream: str, line: str) -> None:
        self.streams.append((stream, line))


def _item_completed_line(text: str, kind: str = "agent_message") -> str:
    return json.dumps({
        "type": "item.completed",
        "item": {"id": "item_0", "type": kind, "text": text},
    })


def test_main_stdout_emits_engineer_progress() -> None:
    """LoopEngine-mode stream label ``main.stdout`` must emit progress."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    line = _item_completed_line("Hello from main agent.")

    cb("main.stdout", line)

    # raw forwarded
    assert sink.streams == [("main.stdout", line)]
    # cooked event emitted
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev["type"] == "engineer.progress"
    assert ev["text"] == "Hello from main agent."
    assert ev["kind"] == "agent_message"


def test_engineer_stdout_still_works() -> None:
    """Legacy SkillLoop label ``engineer.stdout`` must keep working."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    line = _item_completed_line("hi", kind="reasoning")

    cb("engineer.stdout", line)
    assert any(e["type"] == "engineer.progress" and e["kind"] == "reasoning"
               for e in sink.events)


def test_reviewer_critic_and_planner_stdout_emit_layered_progress() -> None:
    """All operator-visible L1-L4 roles should stream to follow/Telegram."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("reviewer.stdout", _item_completed_line("{\"status\":\"done\"}"))
    cb("critic.cycle1.stdout", _item_completed_line("{\"stop\":true}"))
    cb("planner.cycle1.stdout", _item_completed_line("{\"project_done\":false}"))

    layers = [e.get("agent_layer") for e in sink.events]
    assert layers == ["reviewer", "critic", "planner"]


def test_matcher_and_distiller_stdout_do_not_emit_progress() -> None:
    """Protocol/maintenance agents' JSON output must stay hidden."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("matcher.stdout", _item_completed_line("[]"))
    cb("distiller.stdout", _item_completed_line("## Title"))

    # Stream lines forwarded for audit
    assert len(sink.streams) == 2
    # No progress events
    assert sink.events == []


def test_stderr_never_emits_progress() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("main.stderr", _item_completed_line("warning"))
    cb("engineer.stderr", _item_completed_line("warning"))
    assert sink.events == []
    assert len(sink.streams) == 2  # both still forwarded


def test_main_final_report_subroles_emit_progress() -> None:
    """``main-final-report.stdout`` is a codex follow-up; surface it too."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("main-final-report.stdout", _item_completed_line("writing report"))
    assert any(e["type"] == "engineer.progress" for e in sink.events)


def test_non_item_completed_lines_do_not_emit() -> None:
    """thread.started / turn.started / turn.completed are noise."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("main.stdout", json.dumps({"type": "thread.started"}))
    cb("main.stdout", json.dumps({"type": "turn.completed"}))
    assert sink.events == []


def test_command_execution_progress_carries_existing_result_metadata() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    line = json.dumps({
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "command_execution",
            "command": "pytest -q tests/foo.py",
            "status": "failed",
            "exit_code": 1,
            "aggregated_output": "FAILED tests/foo.py::test_x\nassert 1 == 2",
        },
    })

    cb("main.stdout", line)

    assert sink.events[-1]["kind"] == "command_execution"
    assert sink.events[-1]["status"] == "failed"
    assert sink.events[-1]["exit_code"] == 1
    assert "FAILED tests/foo.py::test_x" in sink.events[-1]["output_excerpt"]


# ---------------------------------------------------------------------------
# Copilot dialect — incremental message_delta + final assistant.message
# ---------------------------------------------------------------------------

def test_copilot_message_delta_accumulates() -> None:
    """assistant.message_delta events should accumulate per messageId."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(
        sink,
        min_delta_interval_s=0,
        min_delta_chars=0,
    )

    def delta(content: str, mid: str = "m1") -> str:
        return json.dumps({
            "type": "assistant.message_delta",
            "data": {"messageId": mid, "deltaContent": content},
        })

    cb("main.stdout", delta("Hello, "))
    cb("main.stdout", delta("how "))
    cb("main.stdout", delta("are you?"))

    progress = [e for e in sink.events if e["type"] == "engineer.progress"]
    assert len(progress) == 3
    # Each successive event carries the accumulated text.
    assert progress[0]["text"] == "Hello,"
    assert progress[1]["text"] == "Hello, how"
    assert progress[2]["text"] == "Hello, how are you?"
    # All marked replace=True so the renderer can update in place.
    assert all(e.get("replace") is True for e in progress)
    # All carry the same message_id so the renderer can group them.
    assert all(e.get("message_id") == "m1" for e in progress)


def test_copilot_assistant_message_final_clears_buffer() -> None:
    """assistant.message (final) emits the full text once and clears
    the buffer, so a subsequent delta with the same messageId starts
    fresh (corner case: pipeline replays).
    """
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)

    cb("main.stdout", json.dumps({
        "type": "assistant.message_delta",
        "data": {"messageId": "m1", "deltaContent": "draft"},
    }))
    cb("main.stdout", json.dumps({
        "type": "assistant.message",
        "data": {"messageId": "m1", "content": "Final answer."},
    }))
    cb("main.stdout", json.dumps({
        "type": "assistant.message_delta",
        "data": {"messageId": "m1", "deltaContent": "second"},
    }))

    progress = [e for e in sink.events if e["type"] == "engineer.progress"]
    # 1: delta "draft", 2: final "Final answer.", 3: delta "second" (NOT "Final answer.second")
    assert progress[0]["text"] == "draft"
    assert progress[1]["text"] == "Final answer."
    assert progress[2]["text"] == "second"


def test_copilot_result_clears_actor_buffers() -> None:
    """A 'result' event ends the turn and resets buffers for that actor."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)

    cb("main.stdout", json.dumps({
        "type": "assistant.message_delta",
        "data": {"messageId": "abandoned", "deltaContent": "partial"},
    }))
    cb("main.stdout", json.dumps({"type": "result"}))
    # Even with the same messageId, accumulation should restart.
    cb("main.stdout", json.dumps({
        "type": "assistant.message_delta",
        "data": {"messageId": "abandoned", "deltaContent": "fresh"},
    }))

    progress = [e for e in sink.events if e["type"] == "engineer.progress"]
    assert progress[0]["text"] == "partial"
    assert progress[1]["text"] == "fresh"  # buffer cleared by 'result'


def test_copilot_buffers_isolated_per_callback() -> None:
    """Two callback instances must not cross-talk via shared globals."""
    sink_a = _RecordingSink()
    sink_b = _RecordingSink()
    cb_a = make_stream_progress_callback(sink_a)
    cb_b = make_stream_progress_callback(sink_b)

    cb_a("main.stdout", json.dumps({
        "type": "assistant.message_delta",
        "data": {"messageId": "m1", "deltaContent": "from-A"},
    }))
    cb_b("main.stdout", json.dumps({
        "type": "assistant.message_delta",
        "data": {"messageId": "m1", "deltaContent": "from-B"},
    }))

    progress_a = [e for e in sink_a.events if e["type"] == "engineer.progress"]
    progress_b = [e for e in sink_b.events if e["type"] == "engineer.progress"]
    assert progress_a[-1]["text"] == "from-A"
    assert progress_b[-1]["text"] == "from-B"


def test_copilot_tool_call_and_result_emit_progress() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)

    cb("main.stdout", json.dumps({
        "type": "tool.call",
        "data": {"name": "bash", "arguments": "ls -la"},
    }))
    cb("main.stdout", json.dumps({
        "type": "tool.result",
        "data": {"content": "total 0\n..."},
    }))

    kinds = [e["kind"] for e in sink.events if e["type"] == "engineer.progress"]
    assert "tool_use" in kinds
    assert "tool_result" in kinds


def test_copilot_message_deltas_are_throttled_but_final_is_flushed() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(
        sink,
        min_delta_interval_s=60,
        min_delta_chars=50,
    )
    for _ in range(120):
        cb("main.stdout", json.dumps({
            "type": "assistant.message_delta",
            "data": {"messageId": "m1", "deltaContent": "x"},
        }))
    cb("main.stdout", json.dumps({
        "type": "assistant.message",
        "data": {"messageId": "m1", "content": "final answer"},
    }))

    progress = [e for e in sink.events if e["type"] == "engineer.progress"]
    assert [len(e["text"]) for e in progress[:-1]] == [1, 51, 101]
    assert progress[-1]["text"] == "final answer"


# ---------------------------------------------------------------------------
# StreamProgressRelay — the callback (and its copilot delta-accumulation buffer)
# MUST be reused across stdout lines. Regression: the runner rebuilt it per line,
# resetting the buffer every token, so copilot's per-token reply deltas were
# emitted standalone and the cockpit showed ONE WORD PER LINE.
# ---------------------------------------------------------------------------

def _delta_line(content: str, mid: str = "m1") -> str:
    return json.dumps({
        "type": "assistant.message_delta",
        "data": {"messageId": mid, "deltaContent": content},
    })


def test_relay_reuses_callback_so_deltas_accumulate() -> None:
    from argus_skill.adapters.stream_progress import StreamProgressRelay

    sink = _RecordingSink()
    relay = StreamProgressRelay(min_delta_interval_s=0, min_delta_chars=0)
    for tok in ("I", "'ll ", "verify"):
        relay(sink, None, "main.stdout", _delta_line(tok))

    texts = [e["text"] for e in sink.events if e["type"] == "engineer.progress"]
    # Accumulating: each fragment CONTAINS the previous, so the front-end's
    # mergeFragment replaces the row in place (one growing reply) instead of
    # newline-appending (one word per line).
    assert texts == ["I", "I'll", "I'll verify"]
    for prev, cur in zip(texts, texts[1:]):
        assert prev in cur


def test_rebuilding_callback_per_line_breaks_accumulation() -> None:
    # Documents the OLD bug: a FRESH callback per line loses the delta buffer, so
    # each token is emitted standalone (never containing the previous). Feeding
    # those to mergeFragment newline-appends them → one word per line.
    sink = _RecordingSink()
    for tok in ("I", "'ll ", "verify"):
        make_stream_progress_callback(sink)("main.stdout", _delta_line(tok))

    texts = [e["text"] for e in sink.events if e["type"] == "engineer.progress"]
    assert texts == ["I", "'ll", "verify"]  # just the tokens — no accumulation
    assert texts[0] not in texts[1]  # the breakage that produced one-word-per-line


def test_relay_rebuilds_on_sink_change() -> None:
    # A new mission (new sink) must start a FRESH accumulation buffer, never
    # leaking the previous message's text into the new one.
    from argus_skill.adapters.stream_progress import StreamProgressRelay

    relay = StreamProgressRelay()
    sink1 = _RecordingSink()
    relay(sink1, None, "main.stdout", _delta_line("first"))
    sink2 = _RecordingSink()
    relay(sink2, None, "main.stdout", _delta_line("second"))

    t2 = [e["text"] for e in sink2.events if e["type"] == "engineer.progress"]
    assert t2 == ["second"]  # fresh buffer — "first" never leaks in
