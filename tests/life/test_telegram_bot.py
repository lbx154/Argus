from __future__ import annotations

import json
from types import SimpleNamespace

from argus_skill.life.memory import LifeMemory
from argus_skill.life.telegram_bot import _CommandRouter
from argus_skill.manager import front_door
from argus_skill.manager.front_door import ManagerHandoffError


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
        lambda chat_state, mem: SimpleNamespace(manager=_Manager()),
    )


def test_add_enqueues_only_manager_execution_task(tmp_path, monkeypatch) -> None:
    router, life_dir, replies = _router(tmp_path)
    captured = {}

    def fake_execution(
        mem, text, state, persist, *, root_task_id=None, **kwargs,
    ):
        captured.update(text=text, root_task_id=root_task_id)
        return persist("draft the MRAM paper", None)

    monkeypatch.setattr(front_door, "manager_bounded_handoff", fake_execution)
    raw = "MRAM: draft the MRAM paper; Manager owns the right sidebar"
    router._cmd_add(raw)

    item = LifeMemory.open(life_dir).backlog.all()[0]
    assert item.title == "draft the MRAM paper"
    assert item.objective == "draft the MRAM paper"
    assert item.manager_decision == {"routed": True}
    assert captured == {
        "text": "draft the MRAM paper; Manager owns the right sidebar",
        "root_task_id": item.id,
    }
    assert raw not in (life_dir / "backlog.jsonl").read_text()
    assert replies and "draft the MRAM paper" in replies[-1]
    assert item.id not in replies[-1]
    assert "ID:" not in replies[-1]


def test_free_text_manager_self_reply_is_not_enqueued(
    tmp_path, monkeypatch,
) -> None:
    router, life_dir, replies = _router(tmp_path)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda mem, text, state: "在的，有什么需要我处理？",
    )

    router.dispatch("在吗")

    assert LifeMemory.open(life_dir).backlog.all() == []
    assert replies == ["在的，有什么需要我处理？"]


def test_continuous_persists_only_manager_execution_task(
    tmp_path, monkeypatch,
) -> None:
    router, life_dir, _replies = _router(tmp_path)
    _install_manager(monkeypatch, lambda text: "study MRAM continuously")
    raw = "study MRAM continuously; Manager decides the right sidebar"

    router._cmd_continuous(f"start {raw}")

    cfg = json.loads((life_dir / "continuous.json").read_text())
    assert cfg["enabled"] is True
    assert cfg["objective"] == "study MRAM continuously"
    assert raw not in (life_dir / "continuous.json").read_text()


def test_continuous_reenable_cleans_stored_legacy_objective(
    tmp_path, monkeypatch,
) -> None:
    from argus_skill.daemon.life_worker import write_continuous_config

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
    router.dispatch("/add write paper; Manager owns the sidebar")

    assert LifeMemory.open(life_dir).backlog.all() == []
    assert replies and "这条命令暂时无法完成" in replies[-1]
    assert "safe handoff unavailable" not in replies[-1]


def test_free_text_reports_manager_handoff_failure(
    tmp_path, monkeypatch,
) -> None:
    router, life_dir, replies = _router(tmp_path)

    def fail_handoff(*args, **kwargs):
        raise ManagerHandoffError("safe handoff unavailable")

    monkeypatch.setattr(front_door, "manager_bounded_handoff", fail_handoff)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: None,
    )
    router.dispatch("write paper; Manager owns the sidebar")

    assert LifeMemory.open(life_dir).backlog.all() == []
    assert replies and "暂时无法安排这项任务" in replies[-1]
    assert "safe handoff unavailable" not in replies[-1]


def test_status_keeps_ids_pids_and_raw_enums_out_of_the_narrative(
    tmp_path, monkeypatch,
) -> None:
    from argus_skill.daemon.state import ContinuousConfigState, DaemonStatus
    from argus_skill.life.memory import BacklogItem

    router, life_dir, replies = _router(tmp_path)
    memory = LifeMemory.open(life_dir)
    item = memory.backlog.add(
        BacklogItem.new(title="检查结果", objective="运行验收测试")
    )
    memory.backlog.mark_running(item.id)
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda _life_dir: DaemonStatus(
            alive=True,
            pid=4242,
            started_at_iso=None,
            uptime_seconds=60,
            life_dir=life_dir,
            global_daily_cap_usd=0.0,
        ),
    )
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_continuous_state",
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


def test_poller_does_not_dispatch_when_offset_persistence_fails(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.life import telegram_bot

    dispatched: list[str] = []
    api_calls = 0
    stopped = False

    def api_call(*args, **kwargs):
        nonlocal api_calls, stopped
        api_calls += 1
        if api_calls == 1:
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 7,
                        "message": {"chat": {"id": "1"}, "text": "/status"},
                    }
                ],
            }
        stopped = True
        return {"ok": True, "result": []}

    poller = telegram_bot.TelegramPoller(
        life_dir=tmp_path,
        token="token",
        chat_id="1",
    )
    poller._stop = SimpleNamespace(
        is_set=lambda: stopped,
        wait=lambda timeout: None,
    )
    monkeypatch.setattr(telegram_bot, "_read_offset", lambda _life_dir: 0)
    monkeypatch.setattr(telegram_bot, "_api_call", api_call)
    monkeypatch.setattr(telegram_bot, "_write_offset", lambda *_args: False)
    monkeypatch.setattr(_CommandRouter, "dispatch", lambda _self, text: dispatched.append(text))

    poller._poll_loop()

    assert dispatched == []
    assert api_calls == 2
