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


def test_a_failed_handoff_behind_a_running_mission_is_told_in_the_conversations_language(
    tmp_path: Path, monkeypatch,
) -> None:
    """Trial s-efe9deac: a failed hand-off replaced the reply with English
    "I couldn't safely prepare that request, so nothing was queued…"."""
    sid = "s-handoff-zh"
    life = _make_project(tmp_path, sid)
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(title="current work", objective="finish current work"))
    assert memory.backlog.claim_next() is not None

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(dispatch, "enqueue_mission", broken)
    result = manager_bridge.manager_message(sid, "加一个导出功能。", global_root=tmp_path)

    assert result["kind"] == "error"
    assert "没能交给团队" in result["reply"] and "再发一次" in result["reply"]
    assert "couldn't" not in result["reply"] and "disk full" not in result["reply"]


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
    # The team is working: a daemon runs this project.
    monkeypatch.setattr(front_door, "_daemon_running", lambda _life_dir: True)

    result = manager_bridge.manager_message(
        sid,
        "你怎么不动了？",
        global_root=tmp_path,
    )

    assert result["kind"] == "task"
    assert result["continuous"] is True
    # This fake Manager's ticket is its whole message: the operator's words,
    # then what Argus told it about the running work.
    assert str(seen["enqueued"]).startswith("你怎么不动了？\n\nThe team is working now")
    assert len(memory.backlog.all()) == 1


def test_manager_is_told_the_work_stopped_when_no_daemon_runs_it(
    tmp_path: Path, monkeypatch,
) -> None:
    """2026-10-29: a deploy restarted the runtime and stopped the daemon mid-round.

    The backlog still said "running", so the Manager was told the team was
    working and answered "团队正在选定视觉研究方向…" with no daemon running.
    """
    from argus.daemon.state import record_host_shutdown

    sid = "s-stopped01"
    life = _make_project(tmp_path, sid)
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(title="select a vision direction", objective="pick one"))
    assert memory.backlog.claim_next() is not None
    record_host_shutdown(life, pid=4242, started_at_iso="2026-10-29T00:00:00Z",
                         signum=15, campaign_generation=3)
    manager_state._STATES.clear()
    seen: dict[str, object] = {}

    def enqueue(mem, body, chat_state, **kwargs):
        seen["enqueued"] = body
        return None, True, 123

    monkeypatch.setattr(dispatch, "enqueue_mission", enqueue)

    manager_bridge.manager_message(sid, "继续做", global_root=tmp_path)

    told = str(seen["enqueued"])
    assert "The team is working now" not in told
    assert "No background worker (daemon) is running for this project" in told
    assert "host shutdown" in told
    assert "select a vision direction" in told


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


def test_a_finite_request_replacing_a_standing_campaign_runs_finite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Trial s-dacac579: the CVPR request was filed bounded but inherited the standing campaign."""
    from argus.daemon.state import compare_and_swap_continuous_config, read_continuous_state

    sid = "s-replace-standing"
    life = _make_project(tmp_path, sid)
    assert compare_and_swap_continuous_config(
        life, expected=read_continuous_state(life), enabled=True,
        objective="撰写一篇中文人工智能文献综述。", open_ended=True,
    )
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    _install_manager(monkeypatch, lambda text: text, lifetime="bounded")
    request = "写一篇cvpr水平的，质量要高，到时候给我pdf"

    result = manager_bridge.manager_message(sid, request, global_root=tmp_path)

    assert result["kind"] == "task"
    state = read_continuous_state(life)
    assert (state.enabled, state.open_ended, state.objective) == (True, False, request)


def test_work_carrying_a_standing_campaign_on_keeps_it_standing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus.daemon.state import compare_and_swap_continuous_config, read_continuous_state

    sid = "s-extend-standing"
    life = _make_project(tmp_path, sid)
    standing = "持续改进检索增强生成的评测。"
    assert compare_and_swap_continuous_config(
        life, expected=read_continuous_state(life), enabled=True, objective=standing, open_ended=True,
    )
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    _install_manager(monkeypatch, lambda text: text, lifetime="bounded")

    manager_bridge.manager_message(sid, f"{standing} 另外补一组消融。", global_root=tmp_path)

    assert read_continuous_state(life).open_ended is True


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


def test_manager_stream_heartbeat_defaults_to_five_seconds(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S", raising=False)
    assert server._manager_stream_heartbeat_seconds() == 5.0
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S", "invalid")
    assert server._manager_stream_heartbeat_seconds() == 5.0


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


def test_fresh_idle_daemon_survives_concurrent_startup_gc(tmp_path: Path) -> None:
    """Regression: another user's daemon/REPL startup may run project GC in the
    gap between POST /api/daemons and this TUI's first snapshot. A freshly
    created empty session must survive that sweep."""
    from argus.core.project_gc import gc_stale_projects

    created = daemon_lifecycle.create_daemon(global_root=tmp_path)
    sid = created["sid"]
    assert gc_stale_projects(tmp_path, now=time.time() + 2) == []
    assert project_state.project_life_dir(sid, global_root=tmp_path) is not None


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


def test_a_turn_that_files_to_argus_keeps_its_own_delivery() -> None:
    """Scenario eval m3: the Manager changed files and also recorded the operator's
    choice as a lasting rule (to=manager); the changed files must still be its delivery."""
    from types import SimpleNamespace

    import pytest

    from argus.webapi.manager_dispatch import _settle_manager_turn

    class Journaled(Exception):
        pass

    seen: dict = {}

    class Emitter:
        life_dir = None
        turn_id = "web-1"

        def journal(self, text, result):
            seen.update(result)
            raise Journaled

    handoff = SimpleNamespace(to="manager", get=lambda key, default=None: default)
    receipt = {"delivery_id": "delivery:x:task_completed:1", "summary": "已完成", "targets": [{"path": "README.md"}]}
    with pytest.raises(Journaled):
        _settle_manager_turn(
            None, "已完成。", handoff, {"_self_delivery": receipt}, Emitter(),
            source_channel="web", source_message_id="m", operator_text="选第一套",
        )
    assert seen["delivery"] == receipt and seen["delivery_id"] == receipt["delivery_id"]


@pytest.mark.parametrize("applies", [True, False])
def test_one_answer_gets_one_acknowledgement(tmp_path: Path, applies: bool) -> None:
    """Scenario eval k1-split-cents (2026-10-24): the operator answered the question
    in the composer; the Manager said "已按你的选择恢复执行：…" and Argus then added
    "好的，按你的选择继续：只接受非负金额。" for the same answer. One answer, one
    acknowledgement — and none that says it resumed when the answer did not apply."""
    from types import SimpleNamespace

    from argus.webapi.manager_dispatch import _settle_manager_turn, _TurnEmitter

    life = _make_project(tmp_path)
    backlog = LifeMemory.open(life).backlog
    item = BacklogItem.new(title="给 moneyfmt 加一个公开函数 split_cents(amount)",
                           objective="split_cents", iterate=False)
    item.pending_question = "split_cents 的边界规则你希望采用哪一种？"
    item.operator_decision = {"options": [{"id": "option-1", "label": "推荐：支持负数，同号拆分"},
                                          {"id": "option-2", "label": "只接受非负金额"}]}
    backlog.add(item)
    if not applies:
        backlog.update(item.id, pending_question="")
    answer = {"item": item.id, "decision": "负数抛 ValueError；超过两位小数抛 ValueError。", "option": "option-2"}
    handoff = SimpleNamespace(to="manager", get=lambda key, default=None: {"answer": answer}.get(key, default))
    reply = "已按你的选择恢复执行：负数和超过两位的有效小数报 ValueError，不舍入、不截断。"
    operator_text = "① 接受 int/float/Decimal。② 负数直接报 ValueError。③ 超过两位小数直接报 ValueError。"
    emitter = _TurnEmitter(life_dir=life, turn_id="web-1", fragment=lambda *_args: None)
    mem = SimpleNamespace(backlog=backlog, project_root=life)

    result = _settle_manager_turn(mem, reply, handoff, {}, emitter, source_channel="web",
                                  source_message_id="", operator_text=operator_text)

    turns = [json.loads(line) for line in (life / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    said = [turn["text"] for turn in turns if turn["role"] == "argus"]
    assert len(said) == 1, said
    assert result["reply"] == said[0]
    if applies:
        assert said[0] == reply and result.get("resolved") is True
    else:
        assert "恢复执行" not in said[0] and result.get("resolved") is not True
