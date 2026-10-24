from __future__ import annotations

import json
from types import SimpleNamespace

from argus.life.memory import LifeMemory
from argus.life.telegram_bot import _CommandRouter
from argus.manager import front_door
from argus.manager.front_door import ManagerHandoffError
from tests.hand_off import manager_runner, replying, ticket


def test_detect_active_layer_requires_explicit_agent_layer() -> None:
    router = _CommandRouter.__new__(_CommandRouter)
    mem = SimpleNamespace(
        journal=SimpleNamespace(
            tail=lambda n: [
                SimpleNamespace(kind="planner_done", extra={}),
                SimpleNamespace(kind="mission_started", extra={}),
            ]
        )
    )

    assert router._detect_active_layer(mem) == ""


def test_detect_active_layer_reads_explicit_agent_layer() -> None:
    router = _CommandRouter.__new__(_CommandRouter)
    mem = SimpleNamespace(
        journal=SimpleNamespace(
            tail=lambda n: [
                SimpleNamespace(
                    kind="mission_started",
                    extra={"agent_layer": "engineer"},
                ),
            ]
        )
    )

    assert router._detect_active_layer(mem) == "👷 Engineer · 执行"


def _router(tmp_path):
    life_dir = tmp_path / "projects" / "s-telegram01"
    life_dir.mkdir(parents=True)
    router = _CommandRouter(life_dir=life_dir, token="unused", chat_id="1")
    replies = []
    router._reply = replies.append
    return router, life_dir, replies


def _install_manager(monkeypatch, execution_for) -> None:
    class _Manager:
        def decide_vertical(self, text, **kwargs):
            return SimpleNamespace(execution_task=execution_for(text))

        def commit_vertical_decision(self, text, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda chat_state, mem: manager_runner(_Manager()),
    )


def _ticket_for(monkeypatch, goal: str) -> None:
    """/add is a Manager turn: its ticket, not the raw text, is what gets queued."""
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda chat_state, mem: manager_runner(
        hand=lambda text: ticket(goal, workflow_mode="direct")))


def test_add_enqueues_only_manager_execution_task(tmp_path, monkeypatch) -> None:
    router, life_dir, replies = _router(tmp_path)
    captured = {}

    def fake_execution(
        mem, text, state, persist, *, root_task_id=None, **kwargs,
    ):
        captured.update(text=text, root_task_id=root_task_id)
        return persist("draft the MRAM paper", None)

    monkeypatch.setattr(front_door, "manager_bounded_handoff", fake_execution)
    _ticket_for(monkeypatch, "draft the MRAM paper")
    raw = "MRAM: draft the MRAM paper; Manager owns the right sidebar"
    router._cmd_add(raw)

    item = LifeMemory.open(life_dir).backlog.all()[0]
    assert item.title == "draft the MRAM paper"
    assert item.objective == "draft the MRAM paper"
    assert item.manager_decision == {"routed": True}
    assert captured["root_task_id"] == item.id
    assert captured["text"] == raw
    assert raw not in (life_dir / "backlog.jsonl").read_text()
    assert replies and "draft the MRAM paper" in replies[-1]
    assert item.id not in replies[-1]
    assert "ID:" not in replies[-1]


def test_continuous_reenable_cleans_stored_legacy_objective(
    tmp_path, monkeypatch,
) -> None:
    from argus.daemon.life_worker import write_continuous_config

    router, life_dir, _replies = _router(tmp_path)
    raw = "study MRAM; Manager owns the sidebar"
    write_continuous_config(life_dir, enabled=False, objective=raw)
    seen = {}

    def clean_handoff(text):
        seen["text"] = text
        return "study MRAM"

    _install_manager(monkeypatch, clean_handoff)
    router._cmd_continuous("start")

    cfg = json.loads((life_dir / "continuous.json").read_text())
    assert seen["text"] == raw
    assert cfg["enabled"] is True
    assert cfg["objective"] == "study MRAM"


def test_add_does_not_enqueue_raw_text_when_manager_handoff_fails(
    tmp_path, monkeypatch,
) -> None:
    router, life_dir, replies = _router(tmp_path)

    def fail_handoff(*args, **kwargs):
        raise ManagerHandoffError("safe handoff unavailable")

    monkeypatch.setattr(front_door, "manager_bounded_handoff", fail_handoff)
    _ticket_for(monkeypatch, "write paper")
    router.dispatch("/add write paper; Manager owns the sidebar")

    assert LifeMemory.open(life_dir).backlog.all() == []
    assert replies, replies
    assert "safe handoff unavailable" not in replies[-1]


def test_status_keeps_ids_pids_and_raw_enums_out_of_the_narrative(
    tmp_path, monkeypatch,
) -> None:
    from argus.daemon.state import ContinuousConfigState, DaemonStatus
    from argus.life.memory import BacklogItem

    router, life_dir, replies = _router(tmp_path)
    memory = LifeMemory.open(life_dir)
    item = memory.backlog.add(
        BacklogItem.new(title="检查结果", objective="运行验收测试")
    )
    memory.backlog.mark_running(item.id)
    monkeypatch.setattr(
        "argus.daemon.life_worker.read_daemon_status",
        lambda _life_dir: DaemonStatus(
            alive=True,
            pid=4242,
            started_at_iso=None,
            uptime_seconds=60,
            life_dir=life_dir,
            ),
    )
    monkeypatch.setattr(
        "argus.daemon.life_worker.read_continuous_state",
        lambda _life_dir: ContinuousConfigState(),
    )

    router._cmd_status("")

    text = replies[-1]
    assert "检查结果" in text
    assert item.id not in text
    assert "4242" not in text and "PID" not in text
    assert "进行中：0 项待办 · 1 项执行中 · 0 项已暂停" in text
    assert "active:" not in text
    assert "pending" not in text
    assert "running" not in text
    assert "paused" not in text


# -- a reply to a parked question ------------------------------------------


def test_free_text_manager_turn_reads_the_canvas_like_the_web_chat(tmp_path, monkeypatch) -> None:
    from argus.webapi import canvas as store

    router, life_dir, _replies = _router(tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(front_door, "manager_turn", replying(lambda _m, text, _s, **_k: seen.append(text) or "好的。"))
    router.dispatch("普通问题")
    store.add_card(life_dir, 0, kind="idea", text="基线要用 3 个种子")
    router.dispatch("接下来做什么？")

    assert seen[0] == "普通问题"
    assert seen[1].startswith("接下来做什么？") and store.CONTEXT_HEADER in seen[1]
    assert "基线要用 3 个种子" in seen[1]
