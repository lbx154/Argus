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

from argus.adapters.stream_progress import make_stream_progress_callback
from argus.life.event_log import JsonlEventSink


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


def test_reviewer_critic_and_planner_stdout_emit_layered_progress() -> None:
    """All operator-visible L1-L4 roles should stream to follow/Telegram."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("reviewer.stdout", _item_completed_line("{\"status\":\"done\"}"))
    cb("critic.cycle1.stdout", _item_completed_line("{\"stop\":true}"))
    cb("planner.cycle1.stdout", _item_completed_line("{\"project_done\":false}"))

    layers = [e.get("agent_layer") for e in sink.events]
    assert layers == ["reviewer", "critic", "planner"]


def test_stderr_never_emits_progress() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("main.stderr", _item_completed_line("warning"))
    cb("engineer.stderr", _item_completed_line("warning"))
    assert sink.events == []
    assert len(sink.streams) == 2  # both still forwarded


def test_non_item_completed_lines_do_not_emit() -> None:
    """thread.started / turn.started / turn.completed are noise."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("main.stdout", json.dumps({"type": "thread.started"}))
    cb("main.stdout", json.dumps({"type": "turn.completed"}))
    assert sink.events == []


def test_long_command_cuts_plumbing_paths_before_the_clip() -> None:
    # 2026-10-22: the clip left "--wheel-dir /…/state/copilot-h…", too cut
    # for any reader to shorten.
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    files = "/srv/state/copilot-home/session-state/3c6c665c-3b32-40b9-a852-1b29cd3bf47f/files"
    command = " && ".join(
        [f"python -m venv {files}/build-env"]
        + [f"{files}/build-env/bin/python -m pip wheel --wheel-dir {files}/wheels ."] * 4
    )
    cb("main.stdout", json.dumps({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "command_execution", "command": command},
    }))

    text = sink.events[-1]["text"]
    assert "copilot-home" not in text
    assert text.endswith("--wheel-dir …/wheels .")


# ---------------------------------------------------------------------------
# Copilot dialect — incremental message_delta + final assistant.message
# ---------------------------------------------------------------------------

def test_copilot_plaintext_reasoning_is_emitted_but_opaque_reasoning_is_not() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(
        sink,
        min_delta_interval_s=0,
        min_delta_chars=0,
    )

    cb("main.stdout", json.dumps({
        "type": "assistant.reasoning",
        "data": {"reasoningId": "r-empty", "content": ""},
    }))
    cb("main.stdout", json.dumps({
        "type": "assistant.reasoning_delta",
        "data": {"reasoningId": "r1", "deltaContent": "Check the "},
    }))
    cb("main.stdout", json.dumps({
        "type": "assistant.reasoning_delta",
        "data": {"reasoningId": "r1", "deltaContent": "smallest case."},
    }))

    progress = [e for e in sink.events if e["type"] == "engineer.progress"]
    assert [e["text"] for e in progress] == [
        "Check the",
        "Check the smallest case.",
    ]
    assert all(e["kind"] == "reasoning" for e in progress)
    assert all(e["message_id"] == "reasoning:r1" for e in progress)
    assert "r-empty" not in json.dumps(progress)


def test_copilot_message_deltas_are_not_persisted_as_prefixes(tmp_path) -> None:
    live = _RecordingSink()
    sink = JsonlEventSink(live, life_dir=tmp_path, verbosity="full")
    cb = make_stream_progress_callback(
        sink,
        min_delta_interval_s=0,
        min_delta_chars=0,
    )
    for content in ("Reviewer ", "verdict ", "is final."):
        cb("reviewer.stdout", json.dumps({
            "type": "assistant.message_delta",
            "data": {"messageId": "review-1", "deltaContent": content},
        }))
    cb("reviewer.stdout", json.dumps({
        "type": "assistant.message",
        "data": {
            "messageId": "review-1",
            "content": "Reviewer verdict is final.",
        },
    }))

    live_progress = [
        event for event in live.events
        if event["type"] == "engineer.progress"
    ]
    persisted = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]

    assert [event["text"] for event in live_progress] == [
        "Reviewer",
        "Reviewer verdict",
        "Reviewer verdict is final.",
        "Reviewer verdict is final.",
    ]
    assert [event["text"] for event in persisted] == [
        "Reviewer verdict is final.",
    ]
    assert persisted[0]["agent_layer"] == "reviewer"


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
    assert all(event["transient"] is True for event in progress)


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


def test_opencode_text_and_tool_events_emit_progress() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("main.stdout", json.dumps({
        "type": "tool_use",
        "part": {
            "tool": "bash",
            "state": {
                "status": "completed",
                "input": {"command": "pwd"},
                "output": "/repo\n",
                "metadata": {"exit": 0},
                "title": "Print working directory",
            },
        },
    }))
    cb("main.stdout", json.dumps({
        "type": "text",
        "part": {"text": "/repo"},
    }))

    progress = [e for e in sink.events if e["type"] == "engineer.progress"]
    assert [event["kind"] for event in progress] == [
        "command_execution",
        "agent_message",
    ]
    assert progress[0]["exit_code"] == 0
    assert progress[1]["text"] == "/repo"


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
    assert all(event["transient"] is True for event in progress[:-1])
    assert "transient" not in progress[-1]


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
    from argus.adapters.stream_progress import StreamProgressRelay

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


# --- Copilot tool execution + Manager visibility ---------------------------
# Three gates used to hide ALL of the Manager's live work from the cockpit:
#   1. ``_io_log._PROGRESS_STREAM_MARKERS`` did not forward Copilot's
#      ``tool.execution_*`` lines to the progress callback at all;
#   2. this module only understood the legacy ``tool.call`` / ``tool.result``
#      pair, so those lines parsed to nothing; and
#   3. the visible-role filter listed engineer/reviewer/planner but no Manager
#      run label, so the Manager's whole stream was dropped.
# Together they produced the "the CLI never shows me what it's doing" report.

def _tool_start_line(name: str, arguments: dict[str, Any], call_id: str = "c1") -> str:
    return json.dumps({
        "type": "tool.execution_start",
        "data": {"toolCallId": call_id, "toolName": name, "arguments": arguments},
    })


def _tool_complete_line(call_id: str = "c1", *, success: bool = True, **result: Any) -> str:
    return json.dumps({
        "type": "tool.execution_complete",
        "data": {"toolCallId": call_id, "success": success, "result": result},
    })


def test_successful_tool_completion_does_not_double_report() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("main.stdout", _tool_start_line("view", {"path": "/repo/a.py"}))
    cb("main.stdout", _tool_complete_line(success=True, content="ok"))

    events = [e for e in sink.events if e["type"] == "engineer.progress"]
    assert len(events) == 1, "a successful call is already reported at start"


def _complete_line(call_id: str, exit_code: int, content: str) -> str:
    return json.dumps({
        "type": "tool.execution_complete",
        "data": {"toolCallId": call_id, "success": True, "shellExecution": {"exitCode": exit_code},
                 "result": {"content": content}},
    })


def test_command_results_are_kept_in_host_memory_with_conflicts() -> None:
    from argus.core import command_record

    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    with command_record.capture("engineer-r2") as record:
        cb("engineer-r2.stdout", _tool_start_line("bash", {"command": "pytest -q"}, call_id="m1"))
        cb("engineer-r2.stdout", _complete_line("m1", 1, "FAILED tests/a.py::t - AssertionError\n1 failed"))
        # A second, different report for the same call is kept as a conflict.
        cb("engineer-r2.stdout", _complete_line("m1", 0, "1 passed"))
        # A result for a call whose start never appeared is kept, unverified.
        cb("engineer-r2.stdout", _complete_line("ghost", 0, "42 passed"))
        cb("engineer-r2.stdout", _tool_start_line("view", {"path": "/app/a.py"}, call_id="m2"))
        # Another round's stream is not this capture's.
        cb("engineer-r3.stdout", _tool_start_line("bash", {"command": "pytest"}, call_id="m3"))

    runs = {run.call_id: run for run in record.runs()}
    assert set(runs) == {"m1", "m2", "ghost"}
    assert runs["m1"].kind == "command" and runs["m1"].conflicting
    assert [result.exit_code for result in runs["m1"].results] == [1, 0]
    assert "FAILED tests/a.py::t" in runs["m1"].results[0].output
    assert runs["ghost"].unverified == "no start was reported for this call"
    assert runs["m2"].kind == "tool"
    # Outside a capture nothing is kept.
    cb("engineer-r2.stdout", _tool_start_line("bash", {"command": "pytest"}, call_id="m4"))
    assert "m4" not in {run.call_id for run in record.runs()}


def test_an_injected_result_cannot_quietly_replace_the_real_one() -> None:
    """A command can write CLI-shaped lines to the stream; the later real result is kept too."""
    from argus.core import command_record

    cb = make_stream_progress_callback(_RecordingSink())
    with command_record.capture("engineer-r1") as record:
        cb("engineer-r1.stdout", _tool_start_line("bash", {"command": "python -m pytest -q"}, call_id="call_real"))
        cb("engineer-r1.stdout", _complete_line("call_real", 0, "7 passed in 0.2s"))  # injected first
        cb("engineer-r1.stdout", _complete_line("call_real", 1, "1 failed, 6 passed"))  # the CLI's own

    [run] = record.runs()
    assert run.conflicting and run.failed
    assert [result.exit_code for result in run.results] == [0, 1]


def test_codex_item_ids_are_kept_apart_per_cli_process() -> None:
    from argus.core import command_record

    def item(event_type: str, exit_code: int | None = None) -> str:
        body = {"id": "item_0", "type": "command_execution", "command": "pytest -q"}
        if exit_code is not None:
            body.update(status="completed", exit_code=exit_code, aggregated_output=f"exit {exit_code}")
        return json.dumps({"type": event_type, "item": body})

    cb = make_stream_progress_callback(_RecordingSink())
    with command_record.capture("engineer-r1") as record:
        for exit_code in (1, 0):  # an auth replay starts a second CLI process
            cb("engineer-r1.stdout", json.dumps({"type": "thread.started", "thread_id": "t"}))
            cb("engineer-r1.stdout", item("item.started"))
            cb("engineer-r1.stdout", item("item.completed", exit_code))

    runs = record.runs()
    assert len(runs) == 2
    assert [run.exit_code for run in runs] == [1, 0]
    assert not any(run.conflicting or run.unverified for run in runs)


def test_codex_command_keeps_failure_and_summary_lines_not_its_banner() -> None:
    from argus.core import command_record

    output = "\n".join([
        "============ test session starts ============", "platform linux -- Python 3.12",
        "rootdir: /app", "collected 7 items", "tests/test_a.py F......", "=== FAILURES ===",
        "E   AssertionError: 3 != 2", "=== short test summary info ===",
        "FAILED tests/test_a.py::test_x - AssertionError", "1 failed, 6 passed in 0.4s",
    ])
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    with command_record.capture("engineer-r1") as record:
        cb("engineer-r1.stdout", json.dumps({"type": "item.completed", "item": {
            "id": "item_3", "type": "command_execution", "command": "pytest -q",
            "status": "completed", "exit_code": 1, "aggregated_output": output,
        }}))

    [event] = [e for e in sink.events if e["type"] == "engineer.progress"]
    assert "AssertionError: 3 != 2" in event["output_excerpt"]
    assert event["output_excerpt"].endswith("1 failed, 6 passed in 0.4s")
    assert "test session starts" not in event["output_excerpt"]
    [run] = record.runs()
    assert run.call_id.endswith(":item_3") and run.exit_code == 1
    assert "FAILED tests/test_a.py::test_x" in run.output


def test_skill_maintenance_streams_stay_hidden() -> None:
    """Matcher/distiller stdout is protocol traffic, not narratable work."""
    for label in ("matcher", "distiller", "scientist-1", "compaction_batch"):
        sink = _RecordingSink()
        cb = make_stream_progress_callback(sink)
        cb(f"{label}.stdout", _tool_start_line("view", {"path": "/repo/a.py"}))
        assert not [e for e in sink.events if e["type"] == "engineer.progress"], label


def test_cursor_tool_call_lifecycle_streams_start_without_success_duplicate() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    started = json.dumps({
        "type": "tool_call",
        "subtype": "started",
        "call_id": "cursor-1",
        "tool_call": {"shellToolCall": {"args": {"command": "git status"}}},
    })
    completed = json.dumps({
        "type": "tool_call",
        "subtype": "completed",
        "call_id": "cursor-1",
        "tool_call": {"shellToolCall": {"result": {"exitCode": 0}}},
    })

    cb("engineer.stdout", started)
    cb("engineer.stdout", completed)

    assert len(sink.events) == 1
    assert sink.events[0]["kind"] == "command_execution"
    assert sink.events[0]["text"] == "git status"
    assert sink.events[0]["status"] == "running"


def test_cursor_failed_tool_call_emits_definite_failure() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)
    cb("engineer.stdout", json.dumps({
        "type": "tool_call",
        "subtype": "started",
        "call_id": "cursor-2",
        "tool_call": {"shellToolCall": {"args": {"command": "false"}}},
    }))
    cb("engineer.stdout", json.dumps({
        "type": "tool_call",
        "subtype": "completed",
        "call_id": "cursor-2",
        "tool_call": {"shellToolCall": {"result": {"exitCode": 1, "output": "failed"}}},
    }))

    assert len(sink.events) == 2
    assert sink.events[-1]["status"] == "failed"
    assert sink.events[-1]["exit_code"] == 1


def _stream_line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False)


def test_tool_call_and_result_carry_call_id_kind_and_status() -> None:
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)

    cb("main.stdout", _stream_line({
        "type": "tool.call",
        "data": {"name": "Count README lines", "kind": "execute", "toolCallId": "t1",
                 "arguments": {"command": "wc -l README.md"}},
    }))
    cb("main.stdout", _stream_line({
        "type": "tool.result",
        "data": {"content": "Count README lines (completed)", "name": "Count README lines",
                 "status": "completed", "toolCallId": "t1", "output": "1 README.md"},
    }))

    progress = [event for event in sink.events if event.get("type") == "engineer.progress"]
    assert [event["kind"] for event in progress] == ["command_execution", "tool_result"]
    call, result = progress
    assert call["text"] == "wc -l README.md"
    assert call["action_summary"] == "Count README lines"
    assert (call["tool_name"], call["call_id"], call["tool_kind"], call["status"]) == (
        "Count README lines", "t1", "execute", "running",
    )
    assert (result["call_id"], result["status"], result["tool_name"], result["output_excerpt"]) == (
        "t1", "completed", "Count README lines", "1 README.md",
    )
