"""Card references quoted from the Atlas map, through the Manager front door.

F1: an operator message containing ``[[Argus引用 {...}]]`` lines reaches the
Manager with a bounded, secret-redacted context block appended after the
operator's own words, while the persisted/visible operator turn carries a
readable inline line instead of raw JSON.

F3: when such a message is routed to TASK creation, the created backlog item
depends on the referenced tasks that can still satisfy a dependency (done or
live). Referenced tasks in a terminal-not-done state are never attached —
``memory._cascade_blocked`` would immediately skip the new item.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.manager import config_intent, dispatch, front_door
from argus_skill.webapi import manager_bridge, manager_state

pytest.importorskip("fastapi")


def _marker(task_id: str, title: str = "Coverage study", **overrides) -> str:
    ref = {
        "source": "live:s",
        "task_id": task_id,
        "task_title": title,
        "event_ids": [],
    }
    ref.update(overrides)
    return "[[Argus引用 " + json.dumps(ref, ensure_ascii=False) + "]]"


def _make_project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()})
        + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


def _seed_tasks(life: Path) -> None:
    memory = LifeMemory.open(life)
    memory.backlog.add(
        BacklogItem(
            id="task-done",
            ts=1,
            title="Compare coverage",
            objective="Run 100 seeds",
            status="done",
        )
    )
    memory.backlog.add(
        BacklogItem(
            id="task-bad",
            ts=2,
            title="Broken step",
            objective="obj",
            status="failed",
        )
    )


# ---------------------------------------------------------------------------
# F1 — the chat path sees the expanded block; the journal stays readable
# ---------------------------------------------------------------------------


def test_chat_turn_expands_references_for_the_manager(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-ref-chat"
    life = _make_project(tmp_path, sid)
    _seed_tasks(life)
    (life / "events.jsonl").write_text(
        json.dumps({
            "type": "round.main.completed",
            "item_id": "task-done",
            "event_id": "ev-1",
            "summary": "覆盖率 91%，密钥 AKIAABCDEFGHIJKLMNOP。",
            "ts": 3.0,
        }) + "\n",
        encoding="utf-8",
    )
    manager_state._STATES.clear()
    seen: dict[str, str] = {}

    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, None, "simple"),
    )

    def triage(mem, body, chat_state, **kwargs):
        seen["body"] = body
        return "我看过这条记录了。"

    monkeypatch.setattr(front_door, "manager_triage", triage)

    result = manager_bridge.manager_message(
        sid,
        "这一步的结论可信吗？\n" + _marker("task-done", event_ids=["ev-1"]),
        global_root=tmp_path,
    )

    assert result == {"kind": "chat", "reply": "我看过这条记录了。"}
    # The Manager received the operator's words plus the expanded block,
    # appended after the message (the routing body may carry the bounded
    # conversation-context wrapper around the operator's words).
    assert "这一步的结论可信吗？" in seen["body"]
    assert "## 操作员引用的地图节点" in seen["body"]
    assert seen["body"].index("这一步的结论可信吗？") < seen["body"].index(
        "## 操作员引用的地图节点"
    )
    assert "《Compare coverage》" in seen["body"]
    assert "覆盖率 91%" in seen["body"]
    assert "AKIAABCDEFGHIJKLMNOP" not in seen["body"]
    # The persisted operator turn is readable: inline line, no raw JSON,
    # and no context block.
    transcript = [
        json.loads(line)
        for line in (life / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    operator_turns = [row for row in transcript if row.get("role") == "operator"]
    assert len(operator_turns) == 1
    assert "[[Argus引用" not in operator_turns[0]["text"]
    assert "（引用：《Compare coverage》）" in operator_turns[0]["text"]
    assert "## 操作员引用的地图节点" not in operator_turns[0]["text"]


def test_message_without_marker_is_untouched(tmp_path: Path, monkeypatch) -> None:
    sid = "s-ref-plain"
    _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    seen: dict[str, str] = {}

    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, None, "simple"),
    )

    def triage(mem, body, chat_state, **kwargs):
        seen["body"] = body
        return "好的。"

    monkeypatch.setattr(front_door, "manager_triage", triage)

    result = manager_bridge.manager_message(sid, "普通问题", global_root=tmp_path)

    assert result == {"kind": "chat", "reply": "好的。"}
    assert seen["body"] == "普通问题"


# ---------------------------------------------------------------------------
# F3 — task creation attaches the referenced tasks as dependencies
# ---------------------------------------------------------------------------


class _StagedResearchManager:
    """Minimal Manager double: staged workflow → operator-priority item."""

    def decide_vertical(self, text, **_kwargs):
        return SimpleNamespace(
            execution_task=text,
            workflow_mode="staged",
            vertical="research",
            research_target_level="publishable",
            target_venue="ICLR",
        )

    def commit_vertical_decision(self, text, decision, **_kwargs):
        return SimpleNamespace(
            execution_task=decision.execution_task,
            workflow_mode=decision.workflow_mode,
            vertical=decision.vertical,
            kind="research",
            stages=["research", "plan", "run", "draft"],
            headline=lambda: "research staged workflow",
        )


def test_task_from_reference_bearing_message_carries_deps(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-ref-task"
    life = _make_project(tmp_path, sid)
    _seed_tasks(life)
    manager_state._STATES.clear()

    def classify(_mem, _text, chat_state, **_kwargs):
        chat_state["_frontdoor_lifetime"] = "bounded"
        return None, None, "complex"

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *_args, **_kwargs: SimpleNamespace(manager=_StagedResearchManager()),
    )

    text = (
        "在这一步的基础上扩展实验\n"
        + _marker("task-done")
        + "\n"
        + _marker("task-bad", title="Broken step")
    )
    result = manager_bridge.manager_message(sid, text, global_root=tmp_path)

    assert result["kind"] == "task"
    assert result["item"] is not None
    queued = {
        row.id: row for row in LifeMemory.open(life).backlog.history()
    }
    item = queued[result["item"]["id"]]
    # The done task attaches (immediately satisfied); the failed one must not
    # (it would cascade-skip the new item).
    assert item.deps == ["task-done"]
    # The agent-visible objective carries the expansion block.
    assert "## 操作员引用的地图节点" in item.objective
    assert "[[Argus引用" not in item.objective


def test_task_referencing_only_failed_tasks_attaches_nothing(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-ref-task-failed"
    life = _make_project(tmp_path, sid)
    _seed_tasks(life)
    manager_state._STATES.clear()

    def classify(_mem, _text, chat_state, **_kwargs):
        chat_state["_frontdoor_lifetime"] = "bounded"
        return None, None, "complex"

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *_args, **_kwargs: SimpleNamespace(manager=_StagedResearchManager()),
    )

    text = "重试这一步\n" + _marker("task-bad", title="Broken step")
    result = manager_bridge.manager_message(sid, text, global_root=tmp_path)

    assert result["kind"] == "task"
    queued = {row.id: row for row in LifeMemory.open(life).backlog.history()}
    assert queued[result["item"]["id"]].deps == []


def test_bounded_direct_dispatch_attaches_reference_deps(
    tmp_path: Path, monkeypatch,
) -> None:
    """The non-continuous (bounded/direct) persist path also attaches deps."""
    from argus_skill.life.memory import MemoryBundle

    sid = "s-ref-direct"
    life = _make_project(tmp_path, sid)
    _seed_tasks(life)
    mem = MemoryBundle.for_cwd(fingerprint=sid, global_root=tmp_path)
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    monkeypatch.setattr(dispatch, "_resolve_manager_workdir", lambda _mem: workdir)

    def handoff(mem_arg, body, chat_state, persist, *, root_task_id=None,
                prepare_persist=None, validate_persist=None, prepared_handoff=None):
        prepare_persist(body)
        validate_persist(body)
        return persist(body, None)

    monkeypatch.setattr(front_door, "manager_bounded_handoff", handoff)

    prepared = SimpleNamespace(
        decision=SimpleNamespace(workflow_mode="direct"),
        lifetime="bounded",
        continuous=False,
        open_ended=False,
        failed=lambda exc: None,
    )
    item, _alive, _pid = dispatch.enqueue_mission(
        mem,
        "扩展这个实验",
        {"config": {"continuous": False}},
        root_task_id="new-item-1",
        prepared_handoff=prepared,
        reference_deps=["task-done", "task-bad", "missing-id", "new-item-1"],
    )

    assert item.id == "new-item-1"
    assert item.deps == ["task-done"]
