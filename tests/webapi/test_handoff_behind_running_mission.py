"""A team hand-off never times out behind a running mission.

Live trial s-efe9deac (2026-10-26): the Manager's reply to the person was
written at 01:33:47, but its team hand-off waited for the pipeline lock a
running research mission held, failed at 01:48:47 with
``ManagerPipelineWaitTimeout``, and the person read an English canned "I
couldn't safely prepare that request, so nothing was queued…" instead of the
reply. The reply shows at once; the hand-off is queued durably and applied at
the mission's next safe point; a failure is told in the conversation's
language with what happens next.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from argus.core.transcript import read_turns
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import dispatch, front_door
from argus.manager._session_ops import manager_pipeline_lock
from argus.webapi import manager_bridge, manager_state
from argus.webapi.manager_handoff_queue import QUEUE_DIR, queued_handoffs
from tests.hand_off import manager_runner, ticket


class _Manager:
    def decide_vertical(self, text, **kwargs):
        return SimpleNamespace(assumptions=(), execution_task=text, session_title="")

    def commit_vertical_decision(self, text, decision, **kwargs):
        return SimpleNamespace(execution_task=decision.execution_task)


def _runner(reply: str):
    return lambda *_a: manager_runner(_Manager(), hand=lambda text: ticket(text), reply=reply)


def _project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()}) + "\n", encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


def _hold_pipeline(life: Path) -> tuple[threading.Event, threading.Thread]:
    """A running mission: another holder keeps the pipeline until released."""
    taken, release = threading.Event(), threading.Event()

    def run() -> None:
        with manager_pipeline_lock(life):
            taken.set()
            release.wait(30)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert taken.wait(5)
    return release, thread


def _until(check, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_the_reply_shows_at_once_and_the_handoff_applies_at_the_next_safe_point(tmp_path, monkeypatch) -> None:
    # Before the fix this wait was the whole story: it timed out (here after 1 s).
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_HANDOFF_WAIT_SECONDS", "1")
    sid = "s-queued01"
    life = _project(tmp_path, sid)
    manager_state._STATES.clear()
    monkeypatch.setattr(front_door, "_ensure_manager_runner", _runner("好的，从核查过的路线里选一条，进入实验。"))
    enqueued: list[str] = []

    def enqueue(mem, body, chat_state, **kwargs):
        item = mem.backlog.add(BacklogItem.new(title="选定路线并开始实验", objective=body))
        enqueued.append(body)
        return item, True, 123

    monkeypatch.setattr(dispatch, "enqueue_mission", enqueue)
    release, holder = _hold_pipeline(life)
    try:
        started = time.monotonic()
        result = manager_bridge.manager_message(sid, "不要再反复否决选题了，选一条进入实验。", global_root=tmp_path)
        assert time.monotonic() - started < 5, "the reply never waits behind the running mission"
        assert result["kind"] == "chat" and result["queued_handoff"] is True
        assert result["reply"].startswith("好的，从核查过的路线里选一条，进入实验。")
        assert "安全交接点" in result["reply"]
        assert "nothing was queued" not in result["reply"] and "couldn't" not in result["reply"]
        # Shown in the conversation now, and the ticket waits durably.
        assert any("好的，从核查过的路线里选一条" in str(turn.get("text")) for turn in read_turns(life))
        [record] = queued_handoffs(life)
        assert record["status"] == "queued" and (life / QUEUE_DIR).is_dir()
        assert enqueued == []
    finally:
        release.set()
        holder.join(5)

    # The safe point: the holder let go, the hand-off goes in.
    assert _until(lambda: enqueued and not queued_handoffs(life))
    assert [item.title for item in LifeMemory.open(life).backlog.active()] == ["选定路线并开始实验"]
    assert _until(lambda: any("已排进团队的工作" in str(turn.get("text")) for turn in read_turns(life)))


def test_a_failed_queued_handoff_is_told_in_the_conversations_language(tmp_path, monkeypatch) -> None:
    sid = "s-queued02"
    life = _project(tmp_path, sid)
    manager_state._STATES.clear()
    monkeypatch.setattr(front_door, "_ensure_manager_runner", _runner("好的。"))

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(dispatch, "enqueue_mission", broken)
    release, holder = _hold_pipeline(life)
    try:
        result = manager_bridge.manager_message(sid, "加一个导出功能。", global_root=tmp_path)
        assert result["queued_handoff"] is True
    finally:
        release.set()
        holder.join(5)

    assert _until(lambda: any("没能交给团队" in str(turn.get("text")) for turn in read_turns(life)))
    failed = [turn for turn in read_turns(life) if "没能交给团队" in str(turn.get("text"))]
    assert "再发一次" in failed[-1]["text"] and "disk full" not in failed[-1]["text"]
    assert queued_handoffs(life) == []


def test_with_nothing_running_the_handoff_is_not_queued(tmp_path, monkeypatch) -> None:
    sid = "s-queued03"
    life = _project(tmp_path, sid)
    manager_state._STATES.clear()
    monkeypatch.setattr(front_door, "_ensure_manager_runner", _runner("好的。"))
    monkeypatch.setattr(
        dispatch, "enqueue_mission",
        lambda mem, body, chat_state, **kw: (SimpleNamespace(id="t1", title="t", objective=body, status="pending",
                                                             priority=0, tags=[], deps=[]), True, 1),
    )
    result = manager_bridge.manager_message(sid, "加一个导出功能。", global_root=tmp_path)
    assert result["kind"] == "task"
    assert not (life / QUEUE_DIR).exists()
