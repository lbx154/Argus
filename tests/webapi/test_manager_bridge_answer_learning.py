"""After a researched chat answer the bridge schedules answer learning off-thread.

The reply is already on its way; these tests check when the bridge declines
to schedule anything and, when it does, what it hands to the learning step.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.life import reflection
from argus.webapi import manager_bridge, manager_state
from argus.webapi.manager_state import _chat_state_for

SID = "s-answer"
RESEARCH_REPLY = (
    "Two sources agree: https://example.org/notes and https://example.org/issues both "
    "describe the same widening of coverage."
)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    (root / "projects" / SID).mkdir(parents=True)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "1")
    manager_state._STATES.clear()
    yield root
    manager_state._STATES.clear()


def _prime(sid: str, *, backend: Any, workdir: Path | None) -> dict[str, Any]:
    state = _chat_state_for(sid)
    state["manager_runner"] = SimpleNamespace(_backend=backend)
    if workdir is not None:
        state["manager_runner_workdir"] = str(workdir)
    return state


def _schedule(home: Path, **overrides: Any) -> threading.Thread | None:
    kwargs: dict[str, Any] = dict(
        life_dir=home / "projects" / SID,
        global_root=home,
        operator_text="现在 torch.compile 覆盖到哪一步了?",
        reply=RESEARCH_REPLY,
    )
    kwargs.update(overrides)
    return manager_bridge._schedule_answer_learning(SID, **kwargs)


def test_nothing_is_scheduled_when_the_switch_is_off(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "0")
    _prime(SID, backend=object(), workdir=home)

    assert _schedule(home) is None


def test_only_an_empty_reply_is_not_scheduled(home: Path) -> None:
    _prime(SID, backend=object(), workdir=home)

    assert _schedule(home, reply="   ", operator_text="ok") is None


def test_nothing_is_scheduled_without_a_manager_backend(home: Path) -> None:
    _chat_state_for(SID)  # a session with no runner yet

    assert _schedule(home) is None

    _prime(SID, backend=None, workdir=home)
    assert _schedule(home) is None


def test_a_researched_answer_is_handed_to_reflection_off_thread(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "workspace"
    state_file = workdir / ".argus" / "PIPELINE_STATE.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({"vertical": "research"}), encoding="utf-8")
    backend = object()
    _prime(SID, backend=backend, workdir=workdir)
    captured: dict[str, Any] = {}
    seen_thread: dict[str, str] = {}

    def _reflect(**kwargs):
        captured.update(kwargs)
        seen_thread["name"] = threading.current_thread().name
        return {"skipped": "", "created": [], "updated": []}

    monkeypatch.setattr(reflection, "reflect_after_answer", _reflect)

    thread = _schedule(home)

    assert thread is not None
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert seen_thread["name"] == "argus-answer-learning"
    assert captured["runner_backend"] is backend
    assert captured["global_root"] == home
    assert captured["life_dir"] == home / "projects" / SID
    assert captured["project_id"] == SID
    assert captured["vertical"] == "research"
    assert captured["operator_text"] == "现在 torch.compile 覆盖到哪一步了?"
    assert captured["reply"] == RESEARCH_REPLY
    assert callable(captured["emit"])

    # The sink it was given writes into this project's event stream.
    captured["emit"]({
        "type": "knowledge.learned", "kind": "learned", "scope": "global",
        "path": "pages/surveys/x.md", "title": "x",
    })
    events = [
        json.loads(line)
        for line in (home / "projects" / SID / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["type"] == "knowledge.learned" for event in events)


def test_a_second_answer_waits_while_the_first_is_still_learning(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prime(SID, backend=object(), workdir=None)
    release = threading.Event()
    started = threading.Event()

    def _reflect(**_kwargs):
        started.set()
        release.wait(5)
        return {"skipped": "", "created": [], "updated": []}

    monkeypatch.setattr(reflection, "reflect_after_answer", _reflect)

    first = _schedule(home)
    assert first is not None
    assert started.wait(5)
    try:
        assert _schedule(home) is None
    finally:
        release.set()
        first.join(timeout=5)
    assert _schedule(home) is not None


def test_answer_learning_failure_stays_off_the_reply(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prime(SID, backend=object(), workdir=None)

    def _explode(**_kwargs):
        raise RuntimeError("provider went away")

    monkeypatch.setattr(reflection, "reflect_after_answer", _explode)

    thread = _schedule(home)

    assert thread is not None
    thread.join(timeout=10)
    assert not thread.is_alive()
