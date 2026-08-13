"""Warm-copilot ACP client (agent_cli.copilot_acp) — offline via a fake process.

The client speaks JSON-RPC 2.0 / ndjson over a subprocess's stdio. Here a
``_FakeAcpProc`` stands in for ``copilot --acp``: its stdin parses each request
and a scripted responder enqueues the matching responses/notifications onto
stdout, so the whole exchange runs with no real copilot and no network.
"""

from __future__ import annotations

import json
import queue
import threading
import time

import pytest

from argus_skill.agent_cli import copilot_acp
from argus_skill.agent_cli.copilot_acp import CopilotAcpClient


class _Opt:
    model = None
    working_dir = None
    external_interrupt_reason_provider = None
    on_agent_message = None


class _FakeAcpProc:
    """Minimal Popen stand-in driven by a ``script(request, proc) -> [responses]``
    responder. stdin.write parses ndjson requests; stdout iterates the responses."""

    def __init__(self, script) -> None:
        self._q: "queue.Queue" = queue.Queue()
        self._script = script
        self._alive = True
        self.session_seq = 0
        self.written: list[dict] = []
        self.stdin = self._Stdin(self)
        self.stdout = self._Stdout(self)

    def poll(self):
        return None if self._alive else 0

    def _on_write(self, obj: dict) -> None:
        self.written.append(obj)
        for resp in self._script(obj, self) or []:
            self._q.put(json.dumps(resp) + "\n")

    def eof(self) -> None:
        self._alive = False
        self._q.put(None)  # closes stdout → reader loop ends

    class _Stdin:
        def __init__(self, p) -> None:
            self.p = p

        def write(self, s: str) -> None:
            for line in s.splitlines():
                line = line.strip()
                if line:
                    self.p._on_write(json.loads(line))

        def flush(self) -> None:
            pass

    class _Stdout:
        def __init__(self, p) -> None:
            self.p = p

        def __iter__(self):
            return self

        def __next__(self):
            item = self.p._q.get()
            if item is None:
                raise StopIteration
            return item


def _init_ok(req):
    return {
        "jsonrpc": "2.0",
        "id": req["id"],
        "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}},
    }


def _session_ok(req, sid="sess-1"):
    return {"jsonrpc": "2.0", "id": req["id"], "result": {"sessionId": sid}}


def _happy_script(req, proc):
    m = req.get("method")
    if m == "initialize":
        return [_init_ok(req)]
    if m == "session/new":
        return [_session_ok(req)]
    if m == "session/prompt":
        sid = req["params"]["sessionId"]

        def upd(text):
            return {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            }

        return [
            upd("CONFIG: NONE"),
            upd("\nROUTE: SELF"),
            # a server→client permission request the client must auto-allow
            {
                "jsonrpc": "2.0",
                "id": 9001,
                "method": "session/request_permission",
                "params": {
                    "options": [
                        {"optionId": "allow", "kind": "allow_always", "name": "Allow"},
                        {"optionId": "deny", "kind": "reject_once", "name": "Deny"},
                    ]
                },
            },
            {"jsonrpc": "2.0", "id": req["id"], "result": {"stopReason": "end_turn"}},
        ]
    return []


def _read_only_info_script(req, proc):
    m = req.get("method")
    if m == "initialize":
        return [_init_ok(req)]
    if m == "session/new":
        return [_session_ok(req)]
    if m == "session/prompt":
        sid = req["params"]["sessionId"]

        def upd(text):
            return {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            }

        return [
            upd("Info: Disabled tools: bash, create, edit"),
            upd("SELF_"),
            upd("ACP_OK"),
            {"jsonrpc": "2.0", "id": req["id"], "result": {"stopReason": "end_turn"}},
        ]
    return []


def _content_filter_script(req, proc):
    method = req.get("method")
    if method == "initialize":
        return [_init_ok(req)]
    if method == "session/new":
        return [_session_ok(req)]
    if method == "session/prompt":
        sid = req["params"]["sessionId"]
        notice = (
            "The model returned no content because the response was blocked "
            "by content filtering."
        )
        return [
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": notice},
                    },
                },
            },
            {"jsonrpc": "2.0", "id": req["id"], "result": {"stopReason": "end_turn"}},
        ]
    return []


def _multi_session_script(req, proc):
    """Dynamic sessions + a small model multiplier for continuity tests."""
    m = req.get("method")
    if m == "initialize":
        return [_init_ok(req)]
    if m == "session/new":
        proc.session_seq += 1
        return [
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "sessionId": f"sess-{proc.session_seq}",
                    "models": {
                        "currentModelId": "small",
                        "availableModels": [
                            {
                                "modelId": "small",
                                "_meta": {"copilotUsage": "0.33x"},
                            }
                        ],
                    },
                },
            }
        ]
    if m == "session/load":
        return [
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {"sessionId": req["params"]["sessionId"]},
            }
        ]
    if m == "session/prompt":
        sid = req["params"]["sessionId"]
        prompt_text = req["params"]["prompt"][0]["text"]
        tool_updates = []
        if "use tool" in prompt_text:
            tool_updates = [
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": sid,
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "tool-1",
                            "title": "Reading state.json",
                            "kind": "read",
                            "status": "pending",
                            "rawInput": {"path": "state.json"},
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": sid,
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "tool-1",
                            "status": "completed",
                        },
                    },
                },
            ]
        return [
            *tool_updates,
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "ok"},
                    },
                },
            },
            {"jsonrpc": "2.0", "id": req["id"], "result": {"stopReason": "end_turn"}},
        ]
    return []


def test_acp_happy_path_maps_to_agent_run_result(monkeypatch) -> None:
    proc = _FakeAcpProc(_happy_script)
    popen_kwargs: dict[str, object] = {}

    def fake_popen(*_args, **kwargs):
        popen_kwargs.update(kwargs)
        return proc

    monkeypatch.setattr(copilot_acp.subprocess, "Popen", fake_popen)

    client = CopilotAcpClient("copilot-bin")
    blocks: list[str] = []
    r = client.run_prompt(
        prompt="classify this",
        resume_thread_id=None,
        options=_Opt(),
        run_label="manager-frontdoor-classify",
        on_block=blocks.append,
    )

    assert r.exit_code == 0
    assert r.turn_completed is True and r.turn_failed is False
    assert r.thread_id == "sess-1"
    assert r.agent_messages == ["CONFIG: NONE\nROUTE: SELF"]  # chunks accumulated
    # on_block fired with the growing accumulated text (last = full reply)
    assert blocks and blocks[-1] == "CONFIG: NONE\nROUTE: SELF"
    # handshake order: initialize → session/new → session/prompt
    methods = [w.get("method") for w in proc.written if w.get("method")]
    assert methods[:3] == ["initialize", "session/new", "session/prompt"]
    # permission auto-allowed
    perm = [w for w in proc.written if w.get("id") == 9001]
    assert perm and perm[0]["result"]["outcome"]["optionId"] == "allow"
    assert popen_kwargs["encoding"] == "utf-8"
    assert popen_kwargs["errors"] == "replace"


def test_content_filter_notice_is_a_permanent_failure_not_agent_output(
    monkeypatch,
) -> None:
    proc = _FakeAcpProc(_content_filter_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")
    blocks: list[str] = []

    result = client.run_prompt(
        prompt="trigger filter",
        resume_thread_id=None,
        options=_Opt(),
        run_label="planner",
        on_block=blocks.append,
    )

    assert result.exit_code != 0
    assert result.turn_completed is False and result.turn_failed is True
    assert result.stop_kind == "permanent_error"
    assert "content filtering" in str(result.fatal_error)
    assert result.agent_messages == []
    assert blocks == []


def test_acp_warm_reuse_skips_new_handshake(monkeypatch) -> None:
    proc = _FakeAcpProc(_happy_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    client.run_prompt(
        prompt="a", resume_thread_id=None, options=_Opt(), run_label="manager-frontdoor-classify"
    )
    client.run_prompt(
        prompt="b", resume_thread_id=None, options=_Opt(), run_label="manager-frontdoor-classify"
    )

    inits = [w for w in proc.written if w.get("method") == "initialize"]
    news = [w for w in proc.written if w.get("method") == "session/new"]
    prompts = [w for w in proc.written if w.get("method") == "session/prompt"]
    assert len(inits) == 1  # initialized ONCE (warm)
    assert len(news) == 1  # front-door session reused (recycle default high)
    assert len(prompts) == 2  # both prompts ran on the warm process


def test_read_only_transport_info_is_not_part_of_manager_reply(monkeypatch) -> None:
    proc = _FakeAcpProc(_read_only_info_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin", read_only=True)

    result = client.run_prompt(
        prompt="status",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
    )

    assert result.exit_code == 0
    assert result.agent_messages == ["SELF_ACP_OK"]


def test_prewarm_starts_lean_process_and_session_without_model_turn(
    monkeypatch,
) -> None:
    proc = _FakeAcpProc(_happy_script)
    commands: list[list[str]] = []

    def _popen(cmd, *args, **kwargs):
        commands.append(cmd)
        return proc

    monkeypatch.setattr(copilot_acp.subprocess, "Popen", _popen)
    client = CopilotAcpClient("copilot-bin", "fast-model", "low", lean=True)

    client.prewarm("/workspace", front_door_session=True)

    methods = [w.get("method") for w in proc.written if w.get("method")]
    assert methods == ["initialize", "session/new"]
    assert commands == [
        [
            "copilot-bin",
            "--acp",
            "--model",
            "fast-model",
            "--reasoning-effort",
            "low",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--available-tools=",
        ]
    ]


def test_new_session_rejects_a_different_selected_model(monkeypatch) -> None:
    proc = _FakeAcpProc(_multi_session_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin", "requested-model", "xhigh")
    client._ensure_started()

    with pytest.raises(RuntimeError, match="selected 'small', expected 'requested-model'"):
        client._new_session("/workspace")

    assert "sess-1" not in client._sessions


def test_manager_chat_is_isolated_then_resumed_on_same_process(monkeypatch) -> None:
    proc = _FakeAcpProc(_multi_session_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    classify_1 = client.run_prompt(
        prompt="classify a",
        resume_thread_id=None,
        options=_Opt(),
        run_label="manager-frontdoor-classify",
    )
    chat_1 = client.run_prompt(
        prompt="reply a",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
    )
    chat_2 = client.run_prompt(
        prompt="reply b",
        resume_thread_id=chat_1.thread_id,
        options=_Opt(),
        run_label="simple-1",
    )
    classify_2 = client.run_prompt(
        prompt="classify b",
        resume_thread_id=None,
        options=_Opt(),
        run_label="manager-frontdoor-classify",
    )

    assert classify_1.thread_id == classify_2.thread_id == "sess-1"
    assert chat_1.thread_id == chat_2.thread_id == "sess-2"
    assert chat_1.thread_id != classify_1.thread_id
    methods = [w.get("method") for w in proc.written]
    assert methods.count("initialize") == 1
    assert methods.count("session/new") == 2
    assert methods.count("session/load") == 0
    prompt_sids = [
        w["params"]["sessionId"] for w in proc.written if w.get("method") == "session/prompt"
    ]
    assert prompt_sids == ["sess-1", "sess-2", "sess-2", "sess-1"]


def test_manager_chat_none_means_intentional_session_rotation(monkeypatch) -> None:
    proc = _FakeAcpProc(_multi_session_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    first = client.run_prompt(
        prompt="reply a",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
    )
    second = client.run_prompt(
        prompt="handoff + reply b",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
    )

    assert first.thread_id == "sess-1"
    assert second.thread_id == "sess-2"


def test_acp_reports_cumulative_premium_usage_for_budget_meter(monkeypatch) -> None:
    proc = _FakeAcpProc(_multi_session_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    first = client.run_prompt(
        prompt="reply a",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
    )
    second = client.run_prompt(
        prompt="reply b",
        resume_thread_id=first.thread_id,
        options=_Opt(),
        run_label="simple-1",
    )

    assert first.json_events[-1]["usage"]["premiumRequests"] == 0.33
    assert second.json_events[-1]["usage"]["premiumRequests"] == 0.66


def test_acp_tool_updates_are_forwarded_as_progress_events(monkeypatch) -> None:
    proc = _FakeAcpProc(_multi_session_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")
    emitted: list[str] = []

    result = client.run_prompt(
        prompt="use tool",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
        emit=emitted.append,
    )

    structured = [json.loads(line) for line in emitted if line.startswith("{")]
    assert result.turn_completed
    assert [event["type"] for event in structured] == ["tool.call", "tool.result"]
    assert structured[0]["data"]["name"] == "Reading state.json"
    assert structured[1]["data"]["content"] == "Reading state.json (completed)"
    assert result.tool_activity_observed is True


def test_manager_prompt_has_independent_long_idle_timeout(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_COPILOT_ACP_TIMEOUT_S", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_COPILOT_ACP_MANAGER_TIMEOUT_S", raising=False)

    assert copilot_acp._prompt_timeout("manager-frontdoor-classify") == 60.0
    assert copilot_acp._prompt_timeout("simple-1") == 300.0
    assert copilot_acp._prompt_timeout("chat-1") == 300.0

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP_TIMEOUT_S", "17")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP_MANAGER_TIMEOUT_S", "240")
    assert copilot_acp._prompt_timeout("manager-frontdoor-classify") == 17.0
    assert copilot_acp._prompt_timeout("simple-1") == 240.0


def test_acp_filters_chunked_cancel_notice_from_reply_and_stream(monkeypatch) -> None:
    def _script(req, proc):
        method = req.get("method")
        if method == "initialize":
            return [_init_ok(req)]
        if method == "session/new":
            return [_session_ok(req)]
        if method != "session/prompt":
            return []
        sid = req["params"]["sessionId"]

        def _chunk(text: str) -> dict:
            return {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            }

        return [
            _chunk("《滕王阁序》正文"),
            _chunk("\n\nInfo: Operation "),
            _chunk("cancelled by user"),
            {"jsonrpc": "2.0", "id": req["id"], "result": {"stopReason": "end_turn"}},
        ]

    proc = _FakeAcpProc(_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")
    emitted: list[str] = []
    blocks: list[str] = []

    result = client.run_prompt(
        prompt="write it",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
        emit=emitted.append,
        on_block=blocks.append,
    )

    assert result.last_agent_message == "《滕王阁序》正文"
    assert "Info: Operation" not in "".join(emitted)
    assert blocks and all("Info: Operation" not in block for block in blocks)


def test_keyboard_interrupt_cancels_and_rotates_acp_session(monkeypatch) -> None:
    prompt_count = 0

    def _script(req, proc):
        nonlocal prompt_count
        method = req.get("method")
        if method == "initialize":
            return [_init_ok(req)]
        if method == "session/new":
            proc.session_seq += 1
            return [_session_ok(req, sid=f"sess-{proc.session_seq}")]
        if method == "session/cancel":
            return []
        if method != "session/prompt":
            return []
        prompt_count += 1
        if prompt_count == 1:
            raise KeyboardInterrupt
        sid = req["params"]["sessionId"]
        return [
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "recovered"},
                    },
                },
            },
            {"jsonrpc": "2.0", "id": req["id"], "result": {"stopReason": "end_turn"}},
        ]

    proc = _FakeAcpProc(_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    with pytest.raises(KeyboardInterrupt):
        client.run_prompt(
            prompt="first",
            resume_thread_id=None,
            options=_Opt(),
            run_label="simple-1",
        )

    second = client.run_prompt(
        prompt="second",
        resume_thread_id="sess-1",
        options=_Opt(),
        run_label="simple-1",
    )

    assert second.thread_id == "sess-2"
    assert second.last_agent_message == "recovered"
    assert any(row.get("method") == "session/cancel" for row in proc.written)
    prompt_sids = [
        row["params"]["sessionId"] for row in proc.written if row.get("method") == "session/prompt"
    ]
    assert prompt_sids == ["sess-1", "sess-2"]


def test_acp_soft_idle_heartbeat_resets_on_real_event_and_stops(monkeypatch) -> None:
    """ACP must honor the same idle callback as the one-shot CLI watchdog."""

    def _delayed_script(req, proc):
        method = req.get("method")
        if method == "initialize":
            return [_init_ok(req)]
        if method == "session/new":
            return [_session_ok(req)]
        if method != "session/prompt":
            return []

        sid = req["params"]["sessionId"]

        def _later() -> None:
            time.sleep(0.035)
            proc._q.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": sid,
                            "update": {
                                "sessionUpdate": "tool_call",
                                "toolCallId": "tool-quiet-reset",
                                "title": "Reading status",
                            },
                        },
                    }
                )
                + "\n"
            )
            # Leave enough post-tool quiet time for two 20ms watchdog samples
            # even on Windows' coarser thread scheduler.
            time.sleep(0.08)
            proc._q.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": sid,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "ok"},
                            },
                        },
                    }
                )
                + "\n"
            )
            proc._q.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": req["id"],
                        "result": {"stopReason": "end_turn"},
                    }
                )
                + "\n"
            )

        threading.Thread(target=_later, daemon=True).start()
        return []

    proc = _FakeAcpProc(_delayed_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")
    snapshots = []
    options = _Opt()
    options.watchdog_soft_idle_seconds = 0.02
    options.watchdog_hard_idle_seconds = 0
    options.inactivity_callback = lambda snapshot: snapshots.append(snapshot)

    result = client.run_prompt(
        prompt="wait then answer",
        resume_thread_id=None,
        options=options,
        run_label="simple-1",
    )

    assert result.turn_completed
    assert len(snapshots) >= 2  # once before and once after the real tool event
    assert all(snapshot.idle_seconds < 0.1 for snapshot in snapshots)
    count_at_completion = len(snapshots)
    time.sleep(0.04)
    assert len(snapshots) == count_at_completion  # completion stops heartbeats


def test_acp_emits_staged_idle_alerts_before_cancelling(monkeypatch) -> None:
    def _silent_script(req, _proc):
        if req.get("method") == "initialize":
            return [_init_ok(req)]
        if req.get("method") == "session/new":
            return [_session_ok(req)]
        return []

    proc = _FakeAcpProc(_silent_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")
    emitted: list[str] = []
    options = _Opt()
    options.watchdog_soft_idle_seconds = 0.02
    options.watchdog_stalled_idle_seconds = 0.04
    options.watchdog_hard_idle_seconds = 0.06
    options.inactivity_callback = None

    result = client.run_prompt(
        prompt="remain silent",
        resume_thread_id=None,
        options=options,
        run_label="simple-1",
        emit=emitted.append,
    )

    event_types = [
        json.loads(item)["type"] for item in emitted if item.startswith("{") and "watchdog." in item
    ]
    assert event_types == [
        "watchdog.no_progress_warning",
        "watchdog.likely_stalled",
        "watchdog.terminated",
    ]
    assert "hard idle timeout" in str(result.fatal_error).lower()
    assert any(row.get("method") == "session/cancel" for row in proc.written)


def test_acp_timeout_tracks_inactivity_not_total_turn_time(monkeypatch) -> None:
    """A healthy tool-heavy turn may outlive the ACP idle timeout."""
    monkeypatch.setattr(copilot_acp, "_prompt_timeout", lambda _label: 0.2)

    def _active_script(req, proc):
        method = req.get("method")
        if method == "initialize":
            return [_init_ok(req)]
        if method == "session/new":
            return [_session_ok(req)]
        if method != "session/prompt":
            return []

        sid = req["params"]["sessionId"]

        def _later() -> None:
            for index in range(5):
                time.sleep(0.05)
                proc._q.put(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": sid,
                                "update": {
                                    "sessionUpdate": "tool_call_update",
                                    "toolCallId": f"tool-{index}",
                                    "status": "in_progress",
                                },
                            },
                        }
                    )
                    + "\n"
                )
            proc._q.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": req["id"],
                        "result": {"stopReason": "end_turn"},
                    }
                )
                + "\n"
            )

        threading.Thread(target=_later, daemon=True).start()
        return []

    proc = _FakeAcpProc(_active_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    started = time.monotonic()
    result = client.run_prompt(
        prompt="keep working",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
    )

    assert time.monotonic() - started > 0.2
    assert result.turn_completed
    assert not any(row.get("method") == "session/cancel" for row in proc.written)


def test_acp_idle_timeout_cancels_an_unresponsive_turn(monkeypatch) -> None:
    monkeypatch.setattr(copilot_acp, "_prompt_timeout", lambda _label: 0.05)
    prompt_id: dict[str, int] = {}

    def _stalled_script(req, proc):
        method = req.get("method")
        if method == "initialize":
            return [_init_ok(req)]
        if method == "session/new":
            return [_session_ok(req)]
        if method == "session/prompt":
            prompt_id["value"] = req["id"]
            return []
        if method == "session/cancel":
            return [
                {
                    "jsonrpc": "2.0",
                    "id": prompt_id["value"],
                    "result": {"stopReason": "cancelled"},
                }
            ]
        return []

    proc = _FakeAcpProc(_stalled_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    result = client.run_prompt(
        prompt="never answers",
        resume_thread_id=None,
        options=_Opt(),
        run_label="simple-1",
    )

    assert result.turn_failed
    assert "ACP prompt idle timeout after 0.05s" in (result.fatal_error or "")
    assert any(row.get("method") == "session/cancel" for row in proc.written)


def test_acp_crash_midturn_is_failure(monkeypatch) -> None:
    def _crash_script(req, proc):
        m = req.get("method")
        if m == "initialize":
            return [_init_ok(req)]
        if m == "session/new":
            return [_session_ok(req)]
        if m == "session/prompt":
            proc.eof()  # process dies before answering the prompt
            return []
        return []

    proc = _FakeAcpProc(_crash_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    r = client.run_prompt(
        prompt="x", resume_thread_id=None, options=_Opt(), run_label="manager-frontdoor-classify"
    )
    assert r.exit_code != 0
    assert r.turn_completed is False and r.turn_failed is True
    assert r.fatal_error


def test_acp_fresh_session_mode(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP_SESSION_MODE", "fresh")
    proc = _FakeAcpProc(_happy_script)
    monkeypatch.setattr(copilot_acp.subprocess, "Popen", lambda *a, **k: proc)
    client = CopilotAcpClient("copilot-bin")

    client.run_prompt(
        prompt="a", resume_thread_id=None, options=_Opt(), run_label="manager-frontdoor-classify"
    )
    client.run_prompt(
        prompt="b", resume_thread_id=None, options=_Opt(), run_label="manager-frontdoor-classify"
    )
    news = [w for w in proc.written if w.get("method") == "session/new"]
    assert len(news) == 2  # a fresh session per call in fresh mode


def test_acp_registry_isolates_manager_scopes() -> None:
    first = copilot_acp.get_client(
        "copilot-bin",
        "model",
        "low",
        scope="manager:s-a",
    )
    same = copilot_acp.get_client(
        "copilot-bin",
        "model",
        "low",
        scope="manager:s-a",
    )
    second = copilot_acp.get_client(
        "copilot-bin",
        "model",
        "low",
        scope="manager:s-b",
    )

    assert first is same
    assert first is not second

    copilot_acp.close_clients_for_scope("manager:s-a")
    replaced = copilot_acp.get_client(
        "copilot-bin",
        "model",
        "low",
        scope="manager:s-a",
    )
    assert replaced is not first
    assert (
        copilot_acp.get_client(
            "copilot-bin",
            "model",
            "low",
            scope="manager:s-b",
        )
        is second
    )
