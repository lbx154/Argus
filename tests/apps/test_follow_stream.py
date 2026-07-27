from __future__ import annotations

import json
from argparse import Namespace
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


def test_follow_progress_render_redacts_raw_secret() -> None:
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345678901"
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
