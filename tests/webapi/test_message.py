"""POST /message — the Manager front-door endpoint (webapi).

The endpoint uses ``manager_triage``/``enqueue_mission`` via
``webapi.manager_bridge.manager_message``. Here we stub that bridge so the test
stays offline (no LLM call) and asserts the endpoint's contract: chat replies
pass through, task classifications lazily spawn the daemon, empty text 400s, and
an unknown project 404s.
"""
from __future__ import annotations

import errno
import json
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.session import (
    SessionMeta,
    read_session_meta,
    write_session_meta,
)
from argus.daemon import life_worker as daemon_worker
from argus.life.memory import Backlog, BacklogItem, LifeMemory
from argus.manager import Manager, dispatch, front_door
from argus.webapi import (
    daemon_lifecycle,
    manager_bridge,
    manager_dispatch,
    manager_pending_question,
    manager_state,
    mission_items,
    project_crud,
    project_state,
    server,
)
from argus.webapi.daemon_services import DaemonServices, ProjectDaemonStarter
from tests.hand_off import manager_runner, manager_settles, ticket

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def _make_project(root: Path, sid: str = "s-msgtest0") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _make_project(tmp_path)
    return TestClient(server.create_app(global_root=tmp_path))


def _client_with_starter(root: Path, start: ProjectDaemonStarter) -> TestClient:
    _make_project(root)
    return TestClient(server.create_app(
        global_root=root,
        daemon_services=DaemonServices(read_status=daemon_worker.read_daemon_status, start=start),
    ))


@pytest.fixture(autouse=True)
def _identity_manager_handoff(monkeypatch) -> None:
    _install_manager(monkeypatch, lambda text: text)


def _install_manager(monkeypatch, execution_for, *, session_title="", lifetime="standing") -> None:
    manager_state._STATES.clear()

    class _Manager:
        def decide_vertical(self, text, **kwargs):
            return SimpleNamespace(assumptions=(), execution_task=execution_for(text), session_title=session_title)

        def commit_vertical_decision(self, text, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(front_door, "_ensure_manager_runner",
                        lambda chat_state, mem: manager_runner(_Manager(), hand=lambda text: ticket(text, lifetime=lifetime)))


def test_message_chat_reply_passthrough(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_message",
        lambda sid, text, *, global_root=None, cancelled=None, defer_dispatch_ack=False: {"kind": "chat", "reply": "你好呀 👋"},
    )
    r = client.post("/api/projects/s-msgtest0/message", json={"text": "你好"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "chat"
    assert body["reply"] == "你好呀 👋"


@pytest.mark.parametrize("failed", [False, True])
def test_manager_tool_steps_become_work_records_on_the_turn_card(
    client: TestClient, tmp_path: Path, monkeypatch, failed: bool,
) -> None:
    from argus.core.mission_view import load_mission_view

    def tool_steps(turn):
        phase = turn["phase_cb"]
        phase("$ wc -l README.md", kind="command_execution",
              meta={"tool_name": "Count README lines", "call_id": "c1", "status": "running"})
        phase("↳ Count README lines · completed", kind="tool_result",
              meta={"call_id": "c1", "status": "completed", "output_excerpt": "1 README.md"})
        phase("⚙ view README.md", kind="tool_use", meta={"tool_name": "view", "call_id": "v1", "status": "running"})
        if not failed:
            phase("↳ view · completed", kind="tool_result", meta={"call_id": "v1", "status": "completed"})

    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(
        hand=None, reply="One line.", during=tool_steps,
        failure="The execution stopped before completion." if failed else ""))
    response = client.post("/api/projects/s-msgtest0/message", json={"text": "How many lines?"})
    assert response.status_code == 200
    life = tmp_path / "projects/s-msgtest0"
    events = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
    turn_id = next(event["message_id"] for event in events if event["type"] == "manager.turn.started")
    steps = [event for event in events if event["type"] == "engineer.progress" and event.get("turn_step")]
    # Every call is written when it opens and again when it closes, live, not
    # only as the reply's step list at the end of the turn.
    assert [(event["call_id"], event["status"]) for event in steps] == [
        ("c1", "running"), ("c1", "completed"), ("v1", "running"),
        ("v1", "interrupted" if failed else "completed"),
    ]
    assert {event["agent_layer"] for event in steps} == {"manager"}
    assert {event["item_id"] for event in steps} == {f"turn:{turn_id}"}
    reply_index = events.index(next(event for event in events if event["type"] == "ui.argus"))
    assert events.index(steps[1]) < reply_index
    work = [row for row in load_mission_view(life)["role_work"] if row["role"] == "manager"]
    assert [(row["id"], row["status"], row["kind"]) for row in work] == [
        (f"manager:{turn_id}:c1", "completed", "command_execution"),
        (f"manager:{turn_id}:v1", "interrupted" if failed else "completed", "tool_use"),
    ]
    assert work[0]["item_id"] == f"turn:{turn_id}"
    assert work[0]["detail"] == "$ wc -l README.md · 1 README.md"


def test_queued_manager_message_cannot_resurrect_deleted_project(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-delete-race"
    life = _make_project(tmp_path, sid)
    waiting = threading.Event()
    original_lock_for = manager_bridge._lock_for

    def marked_lock_for(project_sid: str):
        lock = original_lock_for(project_sid)
        if project_sid == sid:
            waiting.set()
        return lock

    monkeypatch.setattr(manager_bridge, "_lock_for", marked_lock_for)
    trash = tmp_path / "trash" / sid
    trash.parent.mkdir(parents=True)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with manager_state.manager_context_lock(sid):
            waiting.clear()
            future = pool.submit(
                manager_bridge.manager_message,
                sid,
                "hello",
                global_root=tmp_path,
            )
            assert waiting.wait(timeout=2)
            life.rename(trash)
        result = future.result(timeout=2)

    assert result["kind"] == "error"
    assert "no longer exists" in result["reply"]
    assert not life.exists()


def test_explicit_authorization_persists_current_blocker_and_never_dispatches(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-authorization"
    life = _make_project(tmp_path, sid)
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    from argus.manager.control_state import CampaignControlStore

    (life / "continuous.json").write_text(
        json.dumps({"objective": "repair terminal gate", "generation": 2}),
        encoding="utf-8",
    )
    store = CampaignControlStore(life, project_root=workdir)
    identity = store.campaign_identity()
    evidence = workdir / "research" / "RESULT.json"
    evidence.parent.mkdir()
    evidence.write_text('{"decision":"rejected"}', encoding="utf-8")
    validator = workdir / "tests" / "test_terminal_contract.py"
    validator.parent.mkdir()
    validator.write_text("def test_contract(): pass\n", encoding="utf-8")
    store.commit_revision(
        identity=identity,
        updates={
            "terminal_evidence": [{
                "failure_source": "validator_defect",
                "validator_id": "terminal-contract",
                "repair_paths": ["tests/test_terminal_contract.py"],
            }],
        },
        reason="Reviewer diagnosed validator defect",
    )
    manager_state._STATES.clear()

    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(hand=manager_settles(authorization=[
        "validator_repair",
        "acceptance_retry",
    ])))
    monkeypatch.setattr(
        manager_dispatch,
        "_authorization_workdir",
        lambda *_args, **_kwargs: workdir,
    )

    result = manager_bridge.manager_message(
        sid,
        "authorize validator repair and one acceptance retry",
        global_root=tmp_path,
        source_channel="vscode",
        source_message_id="message-42",
    )

    assert result["kind"] == "control"
    assert result["control"] == "authorization"
    assert result["allowed_actions"] == [
        "validator_repair",
        "acceptance_retry",
    ]
    assert result["reply"] == (
        "Authorization saved for the current blocker. "
        "The team can use it when the task resumes."
    )
    assert result["authorization_id"] not in result["reply"]
    event = store.get_authorization(result["authorization_id"])
    assert event is not None
    assert event["source_channel"] == "vscode"
    assert event["source_message_id"] == "message-42"
    assert event["validator_id"] == "terminal-contract"
    assert event["allowed_write_paths"] == ["tests/test_terminal_contract.py"]
    assert LifeMemory.open(life).backlog.all() == []


def test_validator_authorization_allows_exact_repair_only(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-authorization-parent"
    life = _make_project(tmp_path, sid)
    workdir = tmp_path / "workspace-parent"
    validator = workdir / "tests" / "test_terminal_contract.py"
    validator.parent.mkdir(parents=True)
    validator.write_text("def test_contract(): pass\n", encoding="utf-8")
    sibling = workdir / "tests" / "test_science_gate.py"
    sibling.write_text("def test_science(): pass\n", encoding="utf-8")
    from argus.manager.control_state import CampaignControlStore

    (life / "continuous.json").write_text(
        json.dumps({"objective": "repair terminal gate", "generation": 2}),
        encoding="utf-8",
    )
    store = CampaignControlStore(life, project_root=workdir)
    identity = store.campaign_identity()
    store.commit_revision(
        identity=identity,
        updates={
            "terminal_evidence": [{
                "failure_source": "validator_defect",
                "validator_id": "terminal-contract",
                "repair_paths": ["tests/test_terminal_contract.py"],
            }],
        },
        reason="Reviewer diagnosed validator defect",
    )
    manager_state._STATES.clear()

    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(hand=manager_settles(authorization=["validator_repair"])))
    monkeypatch.setattr(
        manager_dispatch,
        "_authorization_workdir",
        lambda *_args, **_kwargs: workdir,
    )

    result = manager_bridge.manager_message(
        sid,
        "authorize the exact validator repair",
        global_root=tmp_path,
    )

    assert result["control"] == "authorization"
    event = store.get_authorization(result["authorization_id"])
    assert event is not None
    assert event["allowed_write_paths"] == ["tests/test_terminal_contract.py"]


def test_authorization_without_current_blocker_fails_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-auth-no-blocker"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()

    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(hand=manager_settles(authorization=["resume_blocked_work"])))

    result = manager_bridge.manager_message(
        sid,
        "authorize resume",
        global_root=tmp_path,
    )

    assert result["control"] == "authorization_rejected"
    assert "no current Reviewer finding" in result["reply"]
    assert LifeMemory.open(life).backlog.all() == []


def test_pause_is_serialized_after_inflight_manager_turn(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-pause-serialized"
    life = _make_project(tmp_path, sid)
    entered = threading.Event()
    release = threading.Event()
    call_order: list[str] = []

    def turn(text):
        # A fresh Manager thread carries the recent conversation before the
        # new line, so the line being answered is the prompt's tail.
        if text.endswith("start another long-running task"):
            call_order.append("first-turn")
            entered.set()
            assert release.wait(timeout=3)
            return None
        call_order.append("pause-turn")
        assert text.endswith("停一下")
        return manager_settles(control={"kind": "pause", "quote": "停一下"})

    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(hand=turn, reply="First Manager turn completed inline."))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            manager_bridge.manager_message,
            sid,
            "start another long-running task",
            global_root=tmp_path,
        )
        assert entered.wait(timeout=2)
        paused_future = pool.submit(
            manager_bridge.manager_message,
            sid,
            "停一下",
            global_root=tmp_path,
        )
        time.sleep(0.05)
        assert not paused_future.done()
        release.set()
        first_result = first.result(timeout=3)
        paused = paused_future.result(timeout=3)

    assert first_result == {
        "kind": "chat",
        "reply": "First Manager turn completed inline.",
    }
    assert paused["kind"] == "control"
    assert paused["control"] == "pause"
    assert call_order == ["first-turn", "pause-turn"]
    assert LifeMemory.open(life).backlog.all() == []


def test_interrupted_manager_turn_reports_failure_without_enqueuing(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-solo-failed"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()

    def interrupted(turn):
        turn["sink"].handle_event({
            "type": "round.main.completed",
            "last_message": "I am building the real workbook.",
            "turn_completed": False,
        })

    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(
        hand=None, during=interrupted, failure="Forced restart after hard idle timeout (120s)"))
    result = manager_bridge.manager_message(sid, "Build the workbook.", global_root=tmp_path)

    assert result["kind"] == "error"
    assert result["success"] is False
    assert "did not finish within 120 seconds" in result["reply"]
    assert "hard idle timeout" not in result["reply"]
    assert result["reply"] != "I am building the real workbook."
    assert "delivery" not in result
    assert LifeMemory.open(life).backlog.all() == []


def test_cancel_during_handoff_stops_before_backlog_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-cancel-commit"
    life = _make_project(tmp_path, sid)
    cancelled = threading.Event()

    def handoff(mem, body, state, *, persist, **kwargs):
        cancelled.set()
        persist("safe task", None)
        return "safe task"

    monkeypatch.setattr(front_door, "manager_continuous_handoff", handoff)

    result = manager_bridge.manager_message(
        sid,
        "start a long task",
        global_root=tmp_path,
        cancelled=cancelled.is_set,
    )

    assert result["kind"] == "cancelled"
    assert LifeMemory.open(life).backlog.all() == []


def test_manager_steer_persists_high_priority_live_directive(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-manager-steer"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(title="current work", objective="finish current work"))
    assert memory.backlog.claim_next() is not None
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(hand=manager_settles(
        control={"kind": "steer", "quote": "停止形式检查", "directive": (
            "暂停当前形式化路线；先检索最接近的前人研究，再由 Planner "
            "根据来源证据安排下一节点。"
        )},
        lifetime="standing",
    )))

    result = manager_bridge.manager_message(
        sid,
        "停止形式检查，优先发明新的数学工具",
        global_root=tmp_path,
    )

    assert result["kind"] == "control"
    assert result["control"] == "steer"
    assert result["continuous"] is True
    assert "按你说的方向做" in result["reply"]
    assert "改成长期做下去" in result["reply"]
    from argus.apps._inbox import claim_inbox_message, release_inbox_claim

    claim = claim_inbox_message(life)
    assert claim is not None
    assert "Operator steering (standing)" in claim.text
    assert "检索最接近的前人研究" in claim.text
    assert "发明新的数学工具" not in claim.text
    release_inbox_claim(life, claim)
    from argus.manager.directive import load_active_manager_directive

    active = load_active_manager_directive(life)
    assert active is not None
    assert "检索最接近的前人研究" in active.text
    assert "发明新的数学工具" not in active.text
    from argus.daemon.state import read_continuous_state

    continuous = read_continuous_state(life)
    assert continuous.enabled is True
    assert continuous.open_ended is True


def test_message_task_lazily_spawns_daemon(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_message",
        lambda sid, text, *, global_root=None, cancelled=None, defer_dispatch_ack=False: {
            "kind": "task", "reply": None,
            "item": {"id": "x1", "title": "optimize kernel"}, "daemon_alive": False,
        },
    )
    spawned: dict[str, object] = {}
    client = _client_with_starter(
        tmp_path,
        lambda sid, *, global_root=None, resume_continuous=False, reclaim_idle=False:
            spawned.update(sid=sid, resume_continuous=resume_continuous)
            or {"alive": True, "pid": 4321},
    )
    r = client.post("/api/projects/s-msgtest0/message", json={"text": "optimize the matmul kernel fully"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "task"
    assert body["item"]["title"] == "optimize kernel"
    assert spawned.get("sid") == "s-msgtest0"  # lazy spawn fired
    assert spawned.get("resume_continuous") is False
    assert "daemon" in body
    assert body["daemon_alive"] is True
    assert body["daemon_pid"] == 4321


def test_manager_handoff_failure_persists_and_streams_error_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-handoff-failure"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()

    monkeypatch.setattr(dispatch, "enqueue_mission", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("provider quota reached")))
    fragments: list[tuple[str, dict]] = []

    result = manager_bridge.manager_message(
        sid,
        "why no reply",
        global_root=tmp_path,
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert result["kind"] == "error"
    assert "nothing was queued or executed" in result["reply"]
    assert "provider quota reached" not in result["reply"]
    assert LifeMemory.open(life).backlog.all() == []
    transcript = [
        json.loads(line)
        for line in (life / "transcript.jsonl").read_text().splitlines()
    ]
    assert transcript[-1]["role"] == "argus"
    assert transcript[-1]["text"] == result["reply"]
    assert any(
        kind == "delta" and payload.get("text") == result["reply"]
        for kind, payload in fragments
    )
    events = [
        json.loads(line)
        for line in (life / "events.jsonl").read_text().splitlines()
    ]
    assert any(
        event.get("type") == "ui.argus" and event.get("text") == result["reply"]
        for event in events
    )


def test_mission_write_failure_is_visible_and_keeps_pending_item(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-mission-write-failure"
    life = _make_project(tmp_path, sid)
    target = life / "backlog.jsonl"
    fragments: list[tuple[str, dict]] = []

    def fail_save(backlog: Backlog, _items) -> None:
        assert backlog.path == target
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(target))

    monkeypatch.setattr(Backlog, "_save", fail_save)
    result = manager_bridge.manager_message(
        sid,
        "preserve this mission",
        global_root=tmp_path,
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert result["kind"] == "error"
    assert str(target) in result["reply"]
    assert f"[Errno {errno.ENOSPC}] {os.strerror(errno.ENOSPC)}" in result["reply"]
    assert any(
        kind == "delta" and payload.get("text") == result["reply"]
        for kind, payload in fragments
    )
    pending = manager_state._STATES[sid]["_pending_missions"][-1]
    assert len(pending) == 1
    assert pending[0].status == "pending"
    assert pending[0].original_objective == "preserve this mission"
    assert result["item"]["id"] == pending[0].id
    assert result["item"]["status"] == "pending"
    assert LifeMemory.open(life).backlog.all() == []


def test_active_mission_team_message_uses_continuous_dispatch(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-active001"
    life = _make_project(tmp_path, sid)
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(
        title="current work",
        objective="finish current work",
    ))
    assert memory.backlog.claim_next() is not None
    manager_state._STATES.clear()
    seen: dict[str, object] = {}

    def enqueue(mem, body, chat_state, **kwargs):
        seen["enqueued"] = body
        chat_state.setdefault("config", {})["continuous"] = True
        return None, True, 123

    monkeypatch.setattr(dispatch, "enqueue_mission", enqueue)

    result = manager_bridge.manager_message(
        sid,
        "你怎么不动了？",
        global_root=tmp_path,
    )

    assert result["kind"] == "task"
    assert result["continuous"] is True
    assert seen["enqueued"] == "你怎么不动了？"
    assert len(memory.backlog.all()) == 1


def test_a_failed_manager_turn_never_falls_through_to_task_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-simple-fail"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(failure="The Manager turn did not complete."))
    monkeypatch.setattr(
        dispatch,
        "enqueue_mission",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("simple reply failure must never enqueue")
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        "请分析当前状态",
        global_root=tmp_path,
    )

    assert result["kind"] == "error"
    assert result["success"] is False
    assert result["error_code"] == "inline_reply_failed"
    assert LifeMemory.open(life).backlog.all() == []


def test_team_message_validates_lifecycle_before_workflow_and_enqueue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-standing-front-door"
    _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    manager_state._chat_state_for(sid).setdefault("config", {})["continuous"] = True
    seen: dict[str, object] = {}


    def enqueue(mem, body, chat_state, **kwargs):
        assert seen["order"] == ["lifecycle"]
        seen["order"].append("enqueue")
        seen["enqueued_body"] = body
        seen["root_task_id"] = kwargs.get("root_task_id")
        seen["continuous_at_enqueue"] = chat_state["config"]["continuous"]
        return None, False, None

    def resume(mem):
        seen["order"] = ["lifecycle"]
        seen["resumed"] = True
        return False

    monkeypatch.setattr(dispatch, "resume_done_lifecycle_for_team_dispatch", resume)
    monkeypatch.setattr(dispatch, "enqueue_mission", enqueue)

    result = manager_bridge.manager_message(
        sid,
        "keep researching this conjecture",
        global_root=tmp_path,
    )

    assert seen["enqueued_body"] == "keep researching this conjecture"
    assert seen["root_task_id"]
    assert seen["resumed"] is True
    assert seen["continuous_at_enqueue"] is True
    assert seen["order"] == ["lifecycle", "enqueue"]
    assert result["kind"] == "task"
    assert result["item"] is None
    assert result["continuous"] is True


def test_finite_staged_paper_request_runs_as_a_finite_campaign(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-staged-paper"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    decisions: list[str] = []

    class _StagedResearchManager:
        def decide_vertical(self, text, **_kwargs):
            decisions.append(text)
            return SimpleNamespace(
                assumptions=(),
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
                stages=[
                    "research", "plan", "benchmark", "run",
                    "analysis", "draft", "review", "submission",
                ],
                headline=lambda: "research staged workflow",
            )

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(
        _StagedResearchManager(), hand=lambda text: ticket(text, lifetime="bounded")))

    objective = "给我写个论文 iclr的 我要投稿"
    result = manager_bridge.manager_message(sid, objective, global_root=tmp_path)

    assert result["kind"] == "task"
    assert result["item"] is not None
    assert result["continuous"] is True
    assert decisions == [objective]
    queued = LifeMemory.open(life).backlog.all()
    assert len(queued) == 1
    assert queued[0].id == result["item"]["id"]
    assert queued[0].objective == objective
    assert "operator_priority" in queued[0].tags
    continuous = json.loads((life / "continuous.json").read_text(encoding="utf-8"))
    assert continuous["enabled"] is True
    assert continuous["open_ended"] is False
    assert continuous["objective"] == objective


def test_manager_decided_targeted_vertical_web_enqueue_enters_backlog(
    tmp_path: Path,
    monkeypatch,
    targeted_vertical,
) -> None:
    sid = "s-explicit-math"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    objective = "prove the bounded integer lemma"
    manager = Manager(project_root=life, runner=SimpleNamespace(run_exec=lambda **_k: (_ for _ in ()).throw(
        AssertionError("a ticket's route needs no routing call"))))
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda chat_state, mem: manager_runner(
        manager, hand=lambda text: ticket(text, vertical=targeted_vertical, workflow_mode="direct",
                                          research_target_level="exploratory")))
    monkeypatch.setattr(
        daemon_lifecycle,
        'start_project_daemon',
        lambda *args, **kwargs: {"alive": True},
    )
    client = TestClient(server.create_app(global_root=tmp_path))

    response = client.post(
        f"/api/projects/{sid}/message",
        json={"text": objective},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "task", payload
    assert payload["item"]["status"] == "pending"
    backlog = LifeMemory.open(life).backlog.all()
    assert len(backlog) == 1
    assert backlog[0].objective.startswith(objective)
    state = json.loads(
        (life / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == targeted_vertical
    assert state["research_target_level"] == "exploratory"


def test_message_empty_400(client: TestClient) -> None:
    assert client.post("/api/projects/s-msgtest0/message", json={"text": "  "}).status_code == 400


def test_message_unknown_project_404(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_message",
        lambda sid, text, *, global_root=None: {"kind": "chat", "reply": "x"},
    )
    assert client.post("/api/projects/s-nope/message", json={"text": "hi"}).status_code == 404


def test_explicit_pending_answer_continues_without_a_model_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path)
    mem = LifeMemory.open(life)
    blocked = BacklogItem.new(
        title="Choose paper format",
        objective="Write the camera-ready paper",
        tags=["paper"],
        iterate=False,
    )
    blocked.pending_question = "Should the appendix be included?"
    mem.backlog.add(blocked)
    started: list[str] = []
    monkeypatch.setattr(
        daemon_lifecycle,
        'start_project_daemon',
        lambda sid, *, global_root=None, resume_continuous=False, reclaim_idle=False:
            started.append(sid) or {"rc": 0},
    )
    client = TestClient(server.create_app(global_root=tmp_path))

    response = client.post(
        f"/api/projects/s-msgtest0/backlog/{blocked.id}/answer",
        json={"text": "Yes, include it after the references."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answered_item_id"] == blocked.id
    assert started == ["s-msgtest0"]
    (continuation,) = LifeMemory.open(life).backlog.all()
    assert continuation.id == payload["item"]["id"] == blocked.id
    assert continuation.pending_question == ""
    assert continuation.objective.startswith(
        "Authoritative Manager operator-answer decision:\n"
        "Yes, include it after the references."
    )
    assert "Operator response" in continuation.objective
    assert "include it after the references" in continuation.objective
    assert "Inherited blocked mission objective" in continuation.objective
    assert continuation.iterate is False
    assert continuation.tags == [
        "paper", "operator-reply", "manager-approved", "review:required",
    ]
    assert continuation.manager_decision == {
        "display_objective": "Write the camera-ready paper", "routed": True,
    }
    # The task's details read as the map does, not the roles' brief.
    detail = client.get(f"/api/projects/s-msgtest0/backlog/{blocked.id}").json()["item"]
    assert detail["objective"] == "Write the camera-ready paper"
    from argus.apps._inbox import claim_inbox_message, release_inbox_claim

    claim = claim_inbox_message(life)
    assert claim is not None and "MANAGER OPERATOR-ANSWER DECISION" in claim.text
    release_inbox_claim(life, claim)
    assert "life.operator_question.answered" in (
        life / "events.jsonl"
    ).read_text(encoding="utf-8")

    duplicate = client.post(
        f"/api/projects/s-msgtest0/backlog/{blocked.id}/answer",
        json={"text": "A duplicate answer."},
    )
    assert duplicate.status_code == 409
    assert len(LifeMemory.open(life).backlog.all()) == 1


def test_pending_answer_supersedes_conflicting_inherited_objective(
    tmp_path: Path,
) -> None:
    life = _make_project(tmp_path)
    mem = LifeMemory.open(life)
    blocked = BacklogItem.new(
        title="Render the paper figure",
        objective="Generate the core figure with image-2.",
        acceptance_check="The figure must come from image-2.",
        non_goals=["Do not use an editable deterministic renderer."],
    )
    blocked.pending_question = "Which renderer should the team use?"
    mem.backlog.add(blocked)

    continuation, _ = mem.backlog.resume_with_operator_reply(
        blocked.id,
        "Use the installed editable renderer instead.",
        manager_decision=(
            "Use the installed editable renderer. This supersedes the inherited "
            "image-2 requirement."
        ),
    )

    assert continuation is not None
    assert continuation.objective.startswith(
        "Authoritative Manager operator-answer decision:\n"
        "Use the installed editable renderer."
    )
    assert continuation.objective.index(
        "This supersedes the inherited image-2 requirement."
    ) < continuation.objective.index("Generate the core figure with image-2.")
    assert (
        "This decision supersedes every conflicting requirement in the inherited "
        "blocked mission objective below and every conflicting acceptance check "
        "or non-goal of this task."
    ) in continuation.objective
    # The task's own acceptance check and non-goals stay as they were written,
    # and the decision is not offered back as a revisable hypothesis.
    assert continuation.acceptance_check == "The figure must come from image-2."
    assert continuation.non_goals == ["Do not use an editable deterministic renderer."]
    assert continuation.plan_hypothesis == ""


def test_concurrent_pending_answers_resume_the_task_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path)
    blocked = BacklogItem.new(title="Blocked", objective="Original objective")
    blocked.pending_question = "Choose A or B?"
    LifeMemory.open(life).backlog.add(blocked)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda answer: manager_pending_question.manager_answer_pending_question(
                "s-msgtest0",
                blocked.id,
                answer,
                global_root=tmp_path,
            ),
            ["A", "B"],
        ))

    assert sum(bool(result and result.get("item")) for result in results) == 1
    assert sum(bool(result and result.get("error")) for result in results) == 1
    assert len(LifeMemory.open(life).backlog.all()) == 1


# ── streaming front-door: POST /message/stream (Server-Sent Events) ──────────

def _parse_sse(text: str) -> list[dict]:
    """Collect the JSON payloads of every ``data:`` frame in an SSE body."""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):].strip()))
    return out


def test_turn_emitter_reports_the_role_that_owns_a_phase(tmp_path: Path) -> None:
    from argus.webapi.manager_dispatch import _TurnEmitter

    frames: list[tuple[str, dict]] = []
    emitter = _TurnEmitter(
        life_dir=tmp_path,
        turn_id="turn-phase",
        fragment=lambda kind, payload: frames.append((kind, payload)),
    )

    emitter.phase("Planner is decomposing and signing off the task…", role="planner")

    assert frames == [
        (
            "phase",
            {
                "role": "planner",
                "label": "Planner is decomposing and signing off the task…",
            },
        )
    ]


def test_turn_emitter_persists_solo_delivery_metadata(tmp_path: Path) -> None:
    from argus.webapi.manager_dispatch import _TurnEmitter

    delivery = {
        "delivery_id": "delivery:solo:task_completed",
        "primary_target": {"path": "result.txt"},
    }
    emitter = _TurnEmitter(
        life_dir=tmp_path,
        turn_id="turn-solo",
        fragment=lambda *_args: None,
    )

    emitter.journal_and_respond(
        "Created result.txt.",
        {
            "kind": "chat",
            "mission_result": True,
            "success": True,
            "delivery_id": delivery["delivery_id"],
            "delivery": delivery,
        },
    )

    turn = json.loads(
        (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    event = json.loads(
        (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert turn["delivery"] == delivery
    assert event["delivery"] == delivery


def test_manager_stream_heartbeat_uses_real_silence_and_stops_on_done() -> None:
    """Fake-clock proof: real fragments reset quiet time; sentinel stops ticks."""
    class _FakeQueue:
        def __init__(self) -> None:
            self.values = [queue.Empty, {"type": "delta", "text": "hi"}, queue.Empty, None]

        def get(self, timeout=None):
            value = self.values.pop(0)
            if value is queue.Empty:
                raise queue.Empty
            return value

    ticks = iter([100.0, 110.0, 111.0, 121.0])
    frames = list(
        server._iter_manager_stream_items(
            _FakeQueue(),
            heartbeat_s=10.0,
            clock=lambda: next(ticks),
        )
    )

    assert [frame["type"] for frame in frames] == ["phase", "delta", "phase"]
    assert frames[0]["heartbeat"] is True and frames[0]["quiet_s"] == 10
    assert frames[2]["quiet_s"] == 10  # reset by the genuine delta at t=111


def test_manager_stream_heartbeat_preserves_the_active_phase_role() -> None:
    class _FakeQueue:
        def __init__(self) -> None:
            self.values = [
                {
                    "type": "phase",
                    "role": "planner",
                    "label": "Planner is decomposing and signing off the task…",
                },
                queue.Empty,
                None,
            ]

        def get(self, timeout=None):
            value = self.values.pop(0)
            if value is queue.Empty:
                raise queue.Empty
            return value

    ticks = iter([100.0, 101.0, 111.0])
    frames = list(
        server._iter_manager_stream_items(
            _FakeQueue(),
            heartbeat_s=10.0,
            clock=lambda: next(ticks),
        )
    )

    assert frames[1]["heartbeat"] is True
    assert frames[1]["role"] == "planner"
    assert frames[1]["label"].startswith("Planner · waiting")
    assert frames[1]["quiet_s"] == 10


def test_manager_stream_heartbeat_defaults_to_five_seconds(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S", raising=False)
    assert server._manager_stream_heartbeat_seconds() == 5.0
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S", "invalid")
    assert server._manager_stream_heartbeat_seconds() == 5.0


def test_message_stream_emits_phase_delta_done(client: TestClient, monkeypatch) -> None:
    """A streamed chat turn: the endpoint forwards each on_fragment(phase|delta)
    live, then a final ``done`` frame carrying the classification + reply."""
    def _streaming(
        sid, text, *, global_root=None, on_fragment=None, cancelled=None, defer_dispatch_ack=False,
    ):
        assert on_fragment is not None  # the stream endpoint MUST pass a sink
        on_fragment("phase", {"role": "manager", "label": "Manager · reading events.jsonl"})
        on_fragment("delta", {"text": "你好", "message_id": "m1"})
        on_fragment("delta", {"text": "需要帮忙吗?", "message_id": "m1"})
        return {"kind": "chat", "reply": "你好\n需要帮忙吗?"}

    monkeypatch.setattr("argus.webapi.manager_bridge.manager_message", _streaming)
    r = client.post("/api/projects/s-msgtest0/message/stream", json={"text": "你好"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(r.text)
    kinds = [f["type"] for f in frames]
    assert kinds == ["phase", "delta", "delta", "done"]
    assert frames[0]["label"].startswith("Manager")
    assert frames[1]["text"] == "你好" and frames[1]["message_id"] == "m1"
    assert frames[-1]["result"]["kind"] == "chat"
    assert "需要帮忙" in frames[-1]["result"]["reply"]


def test_message_stream_task_spawns_and_reports(tmp_path: Path, monkeypatch) -> None:
    """A streamed TEAM classification lazily spawns the executor (like /message)
    and the done frame carries the enqueued item."""
    def _streaming(
        sid, text, *, global_root=None, on_fragment=None, cancelled=None, defer_dispatch_ack=False,
    ):
        assert callable(cancelled) and not cancelled()
        return {"kind": "task", "reply": None,
                "item": {"id": "x9", "title": "optimize kernel"}, "daemon_alive": False}

    monkeypatch.setattr("argus.webapi.manager_bridge.manager_message", _streaming)
    spawned: dict[str, object] = {}
    client = _client_with_starter(
        tmp_path,
        lambda sid, *, global_root=None, resume_continuous=False, reclaim_idle=False:
            spawned.update(sid=sid, resume_continuous=resume_continuous)
            or {"alive": True, "pid": 9876},
    )
    r = client.post("/api/projects/s-msgtest0/message/stream", json={"text": "optimize the matmul kernel"})
    assert r.status_code == 200
    done = _parse_sse(r.text)[-1]
    assert done["type"] == "done"
    assert done["result"]["kind"] == "task"
    assert done["result"]["item"]["title"] == "optimize kernel"
    assert spawned.get("sid") == "s-msgtest0"  # lazy spawn fired on the stream path too
    assert spawned.get("resume_continuous") is False
    assert done["result"]["daemon_alive"] is True
    assert done["result"]["daemon_pid"] == 9876


def test_message_stream_standing_task_starts_continuous_executor(
    tmp_path: Path, monkeypatch,
) -> None:
    def _streaming(
        sid, text, *, global_root=None, on_fragment=None, cancelled=None, defer_dispatch_ack=False,
    ):
        return {
            "kind": "task",
            "reply": None,
            "item": None,
            "daemon_alive": False,
            "continuous": True,
        }

    monkeypatch.setattr("argus.webapi.manager_bridge.manager_message", _streaming)
    spawned: dict[str, object] = {}
    client = _client_with_starter(
        tmp_path,
        lambda sid, *, global_root=None, resume_continuous=False, reclaim_idle=False:
            spawned.update(sid=sid, resume_continuous=resume_continuous) or {"alive": True},
    )
    r = client.post(
        "/api/projects/s-msgtest0/message/stream",
        json={"text": "keep improving the benchmark until no weakness remains"},
    )
    assert r.status_code == 200
    assert spawned == {"sid": "s-msgtest0", "resume_continuous": True}


def test_message_stream_keeps_startup_exception_in_diagnostic(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_message",
        lambda sid, text, *, global_root=None, on_fragment=None, **kwargs: {
            "kind": "task",
            "reply": None,
            "item": {"id": "x1", "title": "repair parser"},
            "daemon_alive": False,
        },
    )
    client = _client_with_starter(
        tmp_path,
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("private startup traceback")
        ),
    )

    response = client.post(
        "/api/projects/s-msgtest0/message/stream",
        json={"text": "repair the parser"},
    )

    result = _parse_sse(response.text)[-1]["result"]
    assert result["daemon"]["error"] == "The background worker could not start."
    assert result["daemon"]["diagnostic"] == (
        "RuntimeError: private startup traceback"
    )
    assert "traceback" not in result["reply"]


def test_message_stream_keeps_ack_exception_in_diagnostic(
    client: TestClient, monkeypatch,
) -> None:
    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_message",
        lambda sid, text, *, global_root=None, on_fragment=None, **kwargs: {
            "kind": "task",
            "reply": None,
            "item": {"id": "x1", "title": "repair parser"},
            "daemon_alive": True,
        },
    )
    monkeypatch.setattr(
        "argus.webapi.manager_pending_question.record_task_dispatch_ack",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("private transcript path")
        ),
    )

    response = client.post(
        "/api/projects/s-msgtest0/message/stream",
        json={"text": "repair the parser"},
    )

    result = _parse_sse(response.text)[-1]["result"]
    assert result["ack_error"] == (
        "The task was queued, but its confirmation could not be saved."
    )
    assert result["ack_diagnostic"] == "RuntimeError: private transcript path"
    assert "private transcript path" not in result["ack_error"]


def test_message_stream_error_frame(client: TestClient, monkeypatch) -> None:
    """A triage crash surfaces as an ``error`` frame, not a wedged stream."""
    def _boom(sid, text, *, global_root=None, on_fragment=None, cancelled=None, defer_dispatch_ack=False):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("argus.webapi.manager_bridge.manager_message", _boom)
    r = client.post("/api/projects/s-msgtest0/message/stream", json={"text": "你好"})
    assert r.status_code == 200
    frames = _parse_sse(r.text)
    assert frames[-1]["type"] == "error"
    assert frames[-1]["error"] == "I couldn't finish handling that request."
    assert frames[-1]["diagnostic"] == "RuntimeError: kaboom"
    assert "kaboom" not in frames[-1]["error"]


def test_message_stream_empty_400(client: TestClient) -> None:
    assert client.post("/api/projects/s-msgtest0/message/stream", json={"text": " "}).status_code == 400


def test_create_daemon_mints_session_and_spawns(tmp_path: Path, monkeypatch) -> None:
    # With an objective: mint session + arm continuous + spawn (mock the fork).
    spawned: dict[str, object] = {}

    def fake_spawn(cfg, quiet=True):
        spawned["continuous"] = cfg.continuous
        spawned["continuous_objective"] = cfg.continuous_objective
        spawned["resume_continuous"] = cfg.resume_continuous
        return 0

    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', fake_spawn)
    client = TestClient(server.create_app(global_root=tmp_path))
    r = client.post("/api/daemons", json={"objective": "reproduce the recursive kernel task", "name": "kbench"})
    assert r.status_code == 200
    body = r.json()
    sid = body["sid"]
    assert sid.startswith("s-")
    assert body["spawned"] is True
    life_dir = tmp_path / "projects" / sid
    session = json.loads((life_dir / "session.json").read_text())
    assert session["cwd"] == str(life_dir)
    cont = json.loads((tmp_path / "projects" / sid / "continuous.json").read_text())
    assert cont.get("enabled") is True
    assert "recursive kernel" in cont.get("objective", "")
    assert spawned == {
        "continuous": False,
        "continuous_objective": "reproduce the recursive kernel task",
        "resume_continuous": True,
    }


def test_create_daemon_persists_only_manager_execution_handoff(
    tmp_path: Path, monkeypatch,
) -> None:
    spawned: dict[str, object] = {}
    _install_manager(monkeypatch, lambda text: "write the MRAM paper", session_title="MRAM paper")
    monkeypatch.setattr(
        daemon_worker,
        'spawn_detached_daemon_clean',
        lambda cfg, quiet=True: spawned.update(
            objective=cfg.continuous_objective,
        ) or 0,
    )
    raw = "write the MRAM paper; Manager owns the right sidebar"

    result = daemon_lifecycle.create_daemon(objective=raw, global_root=tmp_path)

    life_dir = tmp_path / "projects" / result["sid"]
    continuous = json.loads((life_dir / "continuous.json").read_text())
    session = json.loads((life_dir / "session.json").read_text())
    assert continuous["objective"] == "write the MRAM paper"
    assert session["objective"] == "write the MRAM paper"
    assert session["display_name"] == "MRAM paper"
    assert spawned["objective"] == "write the MRAM paper"
    assert raw not in (life_dir / "continuous.json").read_text()


def test_create_daemon_preserves_manual_rename_during_manager_handoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        daemon_worker,
        'spawn_detached_daemon_clean',
        lambda *_args, **_kwargs: 0,
    )

    def _handoff(sid, objective, *, global_root=None, name_session=False):
        assert name_session is True
        renamed = project_crud.update_project(
            sid,
            name="Operator title",
            global_root=global_root,
        )
        assert renamed is not None
        return "Manager-authored objective"

    monkeypatch.setattr(manager_dispatch, "manager_continuous_handoff", _handoff)

    result = daemon_lifecycle.create_daemon(
        objective="raw operator objective",
        global_root=tmp_path,
    )

    session = json.loads(
        (tmp_path / "projects" / result["sid"] / "session.json").read_text()
    )
    assert session["display_name"] == "Operator title"
    assert session["objective"] == "Manager-authored objective"


def test_create_daemon_normalizes_explicit_name(tmp_path: Path) -> None:
    result = daemon_lifecycle.create_daemon(
        name="  Concise\n  session   name  ",
        global_root=tmp_path,
    )
    session = json.loads(
        (tmp_path / "projects" / result["sid"] / "session.json").read_text()
    )
    assert session["display_name"] == "Concise session name"


def test_direct_task_names_an_idle_session_from_its_first_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_manager(monkeypatch, lambda text: text, session_title="Local task verification")
    monkeypatch.setattr(
        daemon_worker,
        'spawn_detached_daemon_clean',
        lambda *_args, **_kwargs: 0,
    )
    created = daemon_lifecycle.create_daemon(global_root=tmp_path)

    item = mission_items.enqueue_task(
        created["sid"],
        "first direct task",
        global_root=tmp_path,
    )

    assert item is not None
    session = json.loads(
        (tmp_path / "projects" / created["sid"] / "session.json").read_text()
    )
    display_name = session["display_name"].strip().casefold()
    assert display_name
    assert display_name == "local task verification"
    assert session["name_source"] == "agent"


def test_create_daemon_without_objective_is_idle(tmp_path: Path, monkeypatch) -> None:
    # No objective: creating a daemon is starting a conversation — mint an idle
    # session, DON'T arm continuous, DON'T spawn. The Manager writes objectives
    # later via /message (which lazily spawns).
    spawned: list[object] = []
    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', lambda cfg, quiet=True: spawned.append(1) or 0)
    client = TestClient(server.create_app(global_root=tmp_path))
    r = client.post("/api/daemons", json={})
    assert r.status_code == 200
    body = r.json()
    sid = body["sid"]
    assert sid.startswith("s-")
    assert body["spawned"] is False
    assert spawned == []  # no fork
    assert (tmp_path / "projects" / sid / "session.json").exists()
    assert not (tmp_path / "projects" / sid / "continuous.json").exists()  # no campaign armed


def test_create_daemon_never_overwrites_host_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"ARGUS_SKILL_MODEL": "gpt-host-default"}),
        encoding="utf-8",
    )

    daemon_lifecycle.create_daemon(global_root=tmp_path)

    assert json.loads(config.read_text())["ARGUS_SKILL_MODEL"] == "gpt-host-default"


def test_create_daemon_at_cap_returns_replacement_candidates(
    tmp_path: Path, monkeypatch,
) -> None:
    running = tmp_path / "projects" / "s-running01"
    running.mkdir(parents=True)
    (running / "session.json").write_text(json.dumps({
        "id": "s-running01",
        "display_name": "Existing campaign",
        "last_active": 1,
    }))

    def fake_status(path):
        path = Path(path)
        alive = path.name == "s-running01"
        return daemon_worker.DaemonStatus(
            alive=alive,
            pid=99 if alive else None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=path,
            pid_path=path / "daemon.pid",
        )

    monkeypatch.setattr(daemon_worker, 'read_daemon_status', fake_status)
    monkeypatch.setattr(project_state, "read_daemon_status", fake_status)
    monkeypatch.setattr(daemon_lifecycle, '_max_active_daemons', lambda config: 1)
    monkeypatch.setattr(daemon_lifecycle, '_active_daemon_count', lambda config: 1)
    client = TestClient(server.create_app(global_root=tmp_path))

    body = client.post(
        "/api/daemons",
        json={"objective": "new campaign"},
    ).json()

    assert body["spawned"] is False
    assert body["start"]["admission_required"] is True
    assert body["start"]["running_daemons"][0]["id"] == "s-running01"
    assert (tmp_path / "projects" / body["sid"] / "continuous.json").exists()


def test_fresh_idle_daemon_survives_concurrent_startup_gc(tmp_path: Path) -> None:
    """Regression: another user's daemon/REPL startup may run project GC in the
    gap between POST /api/daemons and this TUI's first snapshot. A freshly
    created empty session must survive that sweep."""
    from argus.core.project_gc import gc_stale_projects

    created = daemon_lifecycle.create_daemon(global_root=tmp_path)
    sid = created["sid"]
    assert gc_stale_projects(tmp_path, now=time.time() + 2) == []
    assert project_state.project_life_dir(sid, global_root=tmp_path) is not None


def test_web_daemon_config_uses_resolved_role_models_and_efforts(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "engineer-model")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "reviewer-model")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "xhigh")
    life_dir = tmp_path / "life"
    life_dir.mkdir()
    write_session_meta(
        tmp_path,
        SessionMeta(id=life_dir.name, cwd=str(life_dir), workdir=str(life_dir)),
    )
    cfg = daemon_lifecycle._worker_config_from_env(life_dir, tmp_path)
    assert cfg.project_workdir == life_dir.resolve()
    assert cfg.engineer_model == "engineer-model"
    assert cfg.reviewer_model == "reviewer-model"
    assert cfg.engineer_reasoning_effort == "high"
    assert cfg.reviewer_reasoning_effort == "xhigh"


def test_web_daemon_config_uses_persisted_session_workdir(tmp_path: Path) -> None:
    sid = "s-workdir1"
    life_dir = tmp_path / "projects" / sid
    workspace = tmp_path / "workspace"
    life_dir.mkdir(parents=True)
    workspace.mkdir()
    write_session_meta(
        tmp_path,
        SessionMeta(
            id=sid,
            cwd=str(life_dir),
            workdir=str(workspace),
            launch_cwd=str(workspace),
        ),
    )

    cfg = daemon_lifecycle._worker_config_from_env(life_dir, tmp_path)

    assert cfg.life_dir == life_dir
    assert cfg.project_workdir == workspace.resolve()


def test_web_daemon_config_does_not_migrate_legacy_launch_cwd(
    tmp_path: Path,
) -> None:
    sid = "s-legacy-workdir"
    life_dir = tmp_path / "projects" / sid
    launch = tmp_path / "old-launch"
    life_dir.mkdir(parents=True)
    launch.mkdir()
    write_session_meta(
        tmp_path,
        SessionMeta(id=sid, cwd=str(life_dir), launch_cwd=str(launch)),
    )

    cfg = daemon_lifecycle._worker_config_from_env(life_dir, tmp_path)

    assert cfg.project_workdir == life_dir.resolve()


def test_web_daemon_config_migrates_legacy_daemon_workdir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "legacy-workdir"
    life_dir = tmp_path / "projects" / sid
    workspace = tmp_path / "workspace"
    life_dir.mkdir(parents=True)
    workspace.mkdir()
    monkeypatch.setattr(
        daemon_worker,
        'read_daemon_status',
        lambda _path: SimpleNamespace(project_workdir=str(workspace)),
    )

    cfg = daemon_lifecycle._worker_config_from_env(life_dir, tmp_path)
    meta = read_session_meta(tmp_path, sid)

    assert cfg.project_workdir == workspace.resolve()
    assert meta is not None
    assert meta.workdir == str(workspace.resolve())


def test_web_daemon_config_repairs_incomplete_session_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "incomplete-workdir"
    life_dir = tmp_path / "projects" / sid
    workspace = tmp_path / "workspace"
    life_dir.mkdir(parents=True)
    workspace.mkdir()
    write_session_meta(
        tmp_path,
        SessionMeta(id=sid, display_name="Named before launch", created=123.0),
    )
    monkeypatch.setattr(
        daemon_worker,
        'read_daemon_status',
        lambda _path: SimpleNamespace(project_workdir=str(workspace)),
    )

    cfg = daemon_lifecycle._worker_config_from_env(life_dir, tmp_path)
    meta = read_session_meta(tmp_path, sid)

    assert cfg.project_workdir == workspace.resolve()
    assert meta is not None
    assert meta.workdir == str(workspace.resolve())
    assert meta.display_name == "Named before launch"
    assert meta.created == 123.0


def test_web_daemon_config_refuses_legacy_session_without_workdir(
    tmp_path: Path,
) -> None:
    sid = "legacy-without-workdir"
    life_dir = tmp_path / "projects" / sid
    life_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="no trustworthy workdir"):
        daemon_lifecycle._worker_config_from_env(life_dir, tmp_path)

    assert read_session_meta(tmp_path, sid) is None


def test_web_daemon_start_reports_legacy_session_without_workdir(
    tmp_path: Path,
) -> None:
    sid = "legacy-without-workdir"
    life_dir = tmp_path / "projects" / sid
    life_dir.mkdir(parents=True)

    result = daemon_lifecycle.start_project_daemon(sid, global_root=tmp_path)

    assert result is not None
    assert result["rc"] == 3
    assert "no trustworthy workdir" in result["error"]
    assert read_session_meta(tmp_path, sid) is None


def test_web_daemon_config_honors_persisted_runner_backend(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.setattr(
        "argus.core.knob_store.read_persisted_knobs",
        lambda: {"ARGUS_SKILL_RUNNER_BACKEND": "copilot"},
    )

    life_dir = tmp_path / "life"
    life_dir.mkdir()
    write_session_meta(
        tmp_path,
        SessionMeta(id=life_dir.name, cwd=str(life_dir), workdir=str(life_dir)),
    )
    cfg = daemon_lifecycle._worker_config_from_env(life_dir, tmp_path)

    assert cfg.backend == "copilot"


def test_changing_the_default_model_in_chat_is_one_reply_without_tools_or_mission(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.core.knob_store import read_persisted_knobs

    sid = "s-config-chat"
    life = _make_project(tmp_path, sid)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    for name in ("ARGUS_SKILL_MODEL", "ARGUS_SKILL_MANAGER_MODEL", "ARGUS_SKILL_PLANNER_MODEL",
                 "ARGUS_SKILL_ENGINEER_MODEL", "ARGUS_SKILL_REVIEWER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    manager_state._STATES.clear()
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: manager_runner(hand=manager_settles(
        intake={"type": "preference", "scope": "global"}, config=[{"knob": "model", "value": "house-model-3"}],
    )))
    monkeypatch.setattr(mission_items, "backend_model_ids", lambda _backend, _root=None: [
        "house-model-2", "house-model-3",
    ])
    result = manager_bridge.manager_message(sid, "把默认模型改成 house-model-3", global_root=tmp_path)

    assert result["kind"] == "chat", result
    assert result["reply"].count("\n") == 0 and "house-model-3" in result["reply"]
    assert read_persisted_knobs()["ARGUS_SKILL_MODEL"] == "house-model-3"
    assert LifeMemory.open(life).backlog.all() == []
    events = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
    assert not any("tool" in str(event.get("type", "")) or event.get("type") in {
        "life.manager.task.started", "life.planner.task_added", "life.mission.started",
    } for event in events[1:])
