from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from argus_skill.apps.cli import _follow


class _Socket:
    def __init__(self, frames: list[str]) -> None:
        self.frames = frames

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def recv(self, *, timeout: float):
        assert timeout == 0.5
        if self.frames:
            return self.frames.pop(0)
        raise OSError("stream closed")


def _args() -> Namespace:
    return Namespace(
        life_dir="",
        web_host="0.0.0.0",
        web_port=8799,
    )


def test_follow_websocket_url_uses_project_and_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "projects" / "session-1"
    project.mkdir(parents=True)
    monkeypatch.setattr(
        _follow._core,
        "_resolve_project_bundle",
        lambda _args: SimpleNamespace(project=SimpleNamespace(root=project)),
    )
    monkeypatch.setenv("ARGUS_SKILL_WEB_TOKEN", "secret token")

    url = _follow._follow_websocket_url(_args())

    assert url.startswith(
        "ws://127.0.0.1:8799/api/projects/session-1/stream?"
    )
    assert "replay=40" in url
    assert "view=full" in url
    assert "token=secret+token" in url

    args = _args()
    args.web_host = "::1"
    assert _follow._follow_websocket_url(args).startswith(
        "ws://[::1]:8799/api/projects/session-1/stream?"
    )


def test_follow_websocket_streams_existing_event_frames(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "projects" / "session-1"
    project.mkdir(parents=True)
    monkeypatch.setattr(
        _follow._core,
        "_resolve_project_bundle",
        lambda _args: SimpleNamespace(project=SimpleNamespace(root=project)),
    )
    events: list[dict] = []
    socket = _Socket([
        json.dumps({
            "type": "engineer.progress",
            "agent_layer": "planner",
            "kind": "reasoning",
            "text": "selecting the next task",
        }),
    ])

    connected = _follow._stream_follow_websocket(
        _args(),
        events.append,
        connect_factory=lambda *_args, **_kwargs: socket,
    )

    assert connected is False
    assert events == [{
        "type": "engineer.progress",
        "agent_layer": "planner",
        "kind": "reasoning",
        "text": "selecting the next task",
    }]


def test_explicit_events_file_skips_websocket_project_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    events = tmp_path / "external" / "events.jsonl"
    events.parent.mkdir()
    events.touch()
    args = _args()
    args.life_dir = str(events)
    resolved = False

    def _unexpected(_args):
        nonlocal resolved
        resolved = True
        raise AssertionError("project bundle must not be used")

    monkeypatch.setattr(_follow._core, "_resolve_project_bundle", _unexpected)

    assert _follow._follow_websocket_url(args) == ""
    assert resolved is False


def test_follow_coalescer_commits_quiet_streamed_message() -> None:
    emitted: list[dict] = []
    coalescer = _follow._FollowCoalescer(
        emitted.append,
        idle_commit_after=0,
    )
    coalescer.feed({
        "type": "engineer.progress",
        "message_id": "message-1",
        "replace": True,
        "text": "current complete fragment",
    })

    coalescer.flush_idle()

    assert emitted == [{
        "type": "engineer.progress",
        "message_id": "message-1",
        "replace": True,
        "text": "current complete fragment",
    }]


def test_follow_coalescer_uses_latest_snapshot_even_when_shorter() -> None:
    emitted: list[dict] = []
    coalescer = _follow._FollowCoalescer(emitted.append)
    coalescer.feed({
        "type": "engineer.progress",
        "message_id": "message-1",
        "replace": True,
        "text": "draft answer with repeated repeated text",
    })
    coalescer.feed({
        "type": "engineer.progress",
        "message_id": "message-1",
        "replace": True,
        "text": "final answer",
    })

    coalescer.flush()

    assert emitted[-1]["text"] == "final answer"


def test_follow_progress_render_redacts_raw_secret() -> None:
    secret = "ghp_" + "A" * 36
    rendered = _follow._format_follow_event_body(
        {
            "type": "engineer.progress",
            "kind": "agent_message",
            "agent_layer": "engineer",
            "text": f"using token {secret}",
        },
        "engineer",
    )

    assert rendered is not None
    assert secret not in rendered
    assert "REDACTED" in rendered


def test_follow_progress_hides_structured_handoff_fields() -> None:
    rendered = _follow._format_follow_event_body(
        {
            "type": "engineer.progress",
            "kind": "agent_message",
            "agent_layer": "engineer",
            "text": "Artifact complete.\nNEXT_OWNER=reviewer\nOPERATOR_QUESTION=none",
        },
        "engineer",
    )

    assert rendered is not None
    assert rendered.endswith("💭 Artifact complete.")
    assert "NEXT_OWNER" not in rendered
    assert "OPERATOR_QUESTION" not in rendered


def test_follow_renderer_uses_one_process_then_falls_back_after_its_exit(
    monkeypatch,
    capsys,
) -> None:
    process = SimpleNamespace(
        stdin=StringIO(),
        stdout=StringIO("🚀 [Engineer] Shared renderer\n"),
        stderr=StringIO("render boom\nstack detail\n"),
        poll=lambda: 17,
        terminate=lambda: None,
        wait=lambda: 17,
    )
    spawns: list[tuple[list[str], dict]] = []

    def _spawn(command, **kwargs):
        spawns.append((command, kwargs))
        return process

    monkeypatch.setattr(_follow, "_bundle_path", lambda: Path("/bundle/argus.mjs"))
    monkeypatch.setattr(_follow.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(_follow.subprocess, "Popen", _spawn)
    renderer = _follow._FollowEventRenderer()

    first = renderer.render(
        {"type": "life.mission.started", "item_id": "task-1"},
        "engineer",
        mission_context={"item_id": "task-1", "title": "Shared renderer", "objective": ""},
    )
    second = renderer.render({"type": "life.manager.intent.started"}, "manager")
    third = renderer.render({"type": "life.manager.intent.started"}, "manager")

    assert first == "🚀 [Engineer] Shared renderer"
    assert second == third == "🧭 [Manager] Understanding the task…"
    assert len(spawns) == 1
    command, kwargs = spawns[0]
    assert command[-6:] == [
        "--locale", "zh-CN", "--unknown-event-policy", "greppable",
        "--density", "compact",
    ]
    assert kwargs["stdin"] is subprocess.PIPE
    sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert sent[0]["title"] == "Shared renderer"
    assert sent[1]["type"] == "life.manager.intent.started"
    notice = capsys.readouterr().err
    assert notice.count("using Python fallback for this follow session") == 1
    assert "renderer exited with status 17: render boom stack detail" in notice


def test_follow_renderer_notices_when_bundle_is_unavailable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(_follow, "_bundle_path", lambda: None)

    renderer = _follow._FollowEventRenderer()
    rendered = renderer.render({"type": "life.manager.intent.started"}, "manager")

    assert rendered == "🧭 [Manager] Understanding the task…"
    assert capsys.readouterr().err == (
        "argus-skill: semantic event renderer unavailable (TUI bundle not found); "
        "using Python fallback for this follow session\n"
    )


def test_follow_renderer_close_does_not_mask_a_late_broken_pipe() -> None:
    def _broken_close() -> None:
        raise BrokenPipeError("renderer exited")

    process = SimpleNamespace(
        stdin=SimpleNamespace(close=_broken_close),
        wait=lambda: 17,
    )
    renderer = _follow._FollowEventRenderer.__new__(_follow._FollowEventRenderer)
    renderer._process = process

    renderer.close()

    assert renderer._process is None
