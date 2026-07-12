"""POST /message — the Manager front-door endpoint (webapi).

The endpoint reuses the REPL's ``manager_triage``/``enqueue_mission`` via
``webapi.manager_bridge.manager_message``. Here we stub that bridge so the test
stays offline (no LLM call) and asserts the endpoint's contract: chat replies
pass through, task classifications lazily spawn the daemon, empty text 400s, and
an unknown project 404s.
"""
from __future__ import annotations

import json
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.manager import front_door
from argus_skill.manager import repl as manager_repl
from argus_skill.webapi import manager_bridge, project_state, server

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


@pytest.fixture(autouse=True)
def _identity_manager_handoff(monkeypatch) -> None:
    _install_manager(monkeypatch, lambda text: text)


def _install_manager(monkeypatch, execution_for) -> None:
    manager_bridge._STATES.clear()

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


def test_message_chat_reply_passthrough(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.webapi.manager_bridge.manager_message",
        lambda sid, text, *, global_root=None: {"kind": "chat", "reply": "你好呀 👋"},
    )
    r = client.post("/api/projects/s-msgtest0/message", json={"text": "你好"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "chat"
    assert body["reply"] == "你好呀 👋"


def test_message_task_lazily_spawns_daemon(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.webapi.manager_bridge.manager_message",
        lambda sid, text, *, global_root=None: {
            "kind": "task", "reply": None,
            "item": {"id": "x1", "title": "optimize kernel"}, "daemon_alive": False,
        },
    )
    spawned: dict[str, object] = {}
    monkeypatch.setattr(
        server, "start_project_daemon",
        lambda sid, *, global_root=None, resume_continuous=False, reclaim_idle=False:
            spawned.update(sid=sid, resume_continuous=resume_continuous) or {"alive": True},
    )
    r = client.post("/api/projects/s-msgtest0/message", json={"text": "optimize the matmul kernel fully"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "task"
    assert body["item"]["title"] == "optimize kernel"
    assert spawned.get("sid") == "s-msgtest0"  # lazy spawn fired
    assert spawned.get("resume_continuous") is False
    assert "daemon" in body


def test_active_mission_message_bypasses_front_door_classification(
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
    manager_bridge._STATES.clear()
    seen = {}

    def unexpected_classify(*args, **kwargs):
        raise AssertionError("active mission must not start another classify call")

    def direct_manager_reply(mem, body, state, **kwargs):
        seen["route"] = kwargs.get("route")
        return "current mission is still running"

    monkeypatch.setattr(manager_repl, "_front_door_classify", unexpected_classify)
    monkeypatch.setattr(manager_repl, "manager_triage", direct_manager_reply)

    result = manager_bridge.manager_message(
        sid,
        "你怎么不动了？",
        global_root=tmp_path,
    )

    assert result["kind"] == "chat"
    assert result["reply"] == "current mission is still running"
    assert seen["route"] == "simple"
    assert len(memory.backlog.all()) == 1


def test_message_empty_400(client: TestClient) -> None:
    assert client.post("/api/projects/s-msgtest0/message", json={"text": "  "}).status_code == 400


def test_message_unknown_project_404(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.webapi.manager_bridge.manager_message",
        lambda sid, text, *, global_root=None: {"kind": "chat", "reply": "x"},
    )
    assert client.post("/api/projects/s-nope/message", json={"text": "hi"}).status_code == 404


def test_pending_answer_bypasses_manager_and_continues_blocked_task(
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
        iteration_budget_usd=12.0,
    )
    blocked.pending_question = "Should the appendix be included?"
    mem.backlog.add(blocked)
    started: list[str] = []
    monkeypatch.setattr(
        server,
        "start_project_daemon",
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
    items = LifeMemory.open(life).backlog.all()
    original = next(item for item in items if item.id == blocked.id)
    continuation = next(item for item in items if item.id == payload["item"]["id"])
    assert original.pending_question == ""
    assert "Operator reply to blocked question" in continuation.objective
    assert "include it after the references" in continuation.objective
    assert continuation.iterate is False
    assert continuation.iteration_budget_usd == 12.0
    assert continuation.tags == ["paper", "operator-reply"]
    assert not (life / "inbox.jsonl").exists()

    duplicate = client.post(
        f"/api/projects/s-msgtest0/backlog/{blocked.id}/answer",
        json={"text": "A duplicate answer."},
    )
    assert duplicate.status_code == 409
    assert len(LifeMemory.open(life).backlog.all()) == 2


def test_concurrent_pending_answers_create_one_continuation(tmp_path: Path) -> None:
    life = _make_project(tmp_path)
    blocked = BacklogItem.new(title="Blocked", objective="Original objective")
    blocked.pending_question = "Choose A or B?"
    LifeMemory.open(life).backlog.add(blocked)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda answer: server.answer_pending_question(
                "s-msgtest0",
                blocked.id,
                answer,
                global_root=tmp_path,
            ),
            ["A", "B"],
        ))

    assert sum(bool(result and result.get("item")) for result in results) == 1
    assert sum(bool(result and result.get("error")) for result in results) == 1
    assert len(LifeMemory.open(life).backlog.all()) == 2


# ── streaming front-door: POST /message/stream (Server-Sent Events) ──────────

def _parse_sse(text: str) -> list[dict]:
    """Collect the JSON payloads of every ``data:`` frame in an SSE body."""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):].strip()))
    return out


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


def test_message_stream_emits_phase_delta_done(client: TestClient, monkeypatch) -> None:
    """A streamed chat turn: the endpoint forwards each on_fragment(phase|delta)
    live, then a final ``done`` frame carrying the classification + reply."""
    def _streaming(sid, text, *, global_root=None, on_fragment=None):
        assert on_fragment is not None  # the stream endpoint MUST pass a sink
        on_fragment("phase", {"role": "manager", "label": "Manager · reading events.jsonl"})
        on_fragment("delta", {"text": "你好", "message_id": "m1"})
        on_fragment("delta", {"text": "需要帮忙吗?", "message_id": "m1"})
        return {"kind": "chat", "reply": "你好\n需要帮忙吗?"}

    monkeypatch.setattr("argus_skill.webapi.manager_bridge.manager_message", _streaming)
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


def test_message_stream_task_spawns_and_reports(client: TestClient, monkeypatch) -> None:
    """A streamed TEAM classification lazily spawns the executor (like /message)
    and the done frame carries the enqueued item."""
    def _streaming(sid, text, *, global_root=None, on_fragment=None):
        return {"kind": "task", "reply": None,
                "item": {"id": "x9", "title": "optimize kernel"}, "daemon_alive": False}

    monkeypatch.setattr("argus_skill.webapi.manager_bridge.manager_message", _streaming)
    spawned: dict[str, object] = {}
    monkeypatch.setattr(
        server, "start_project_daemon",
        lambda sid, *, global_root=None, resume_continuous=False, reclaim_idle=False:
            spawned.update(sid=sid, resume_continuous=resume_continuous) or {"alive": True},
    )
    r = client.post("/api/projects/s-msgtest0/message/stream", json={"text": "optimize the matmul kernel"})
    assert r.status_code == 200
    done = _parse_sse(r.text)[-1]
    assert done["type"] == "done"
    assert done["result"]["kind"] == "task"
    assert done["result"]["item"]["title"] == "optimize kernel"
    assert spawned.get("sid") == "s-msgtest0"  # lazy spawn fired on the stream path too
    assert spawned.get("resume_continuous") is False


def test_message_stream_standing_task_starts_continuous_executor(
    client: TestClient, monkeypatch,
) -> None:
    def _streaming(sid, text, *, global_root=None, on_fragment=None):
        return {
            "kind": "task",
            "reply": None,
            "item": None,
            "daemon_alive": False,
            "continuous": True,
        }

    monkeypatch.setattr("argus_skill.webapi.manager_bridge.manager_message", _streaming)
    spawned: dict[str, object] = {}
    monkeypatch.setattr(
        server,
        "start_project_daemon",
        lambda sid, *, global_root=None, resume_continuous=False, reclaim_idle=False:
            spawned.update(sid=sid, resume_continuous=resume_continuous) or {"alive": True},
    )
    r = client.post(
        "/api/projects/s-msgtest0/message/stream",
        json={"text": "keep improving the benchmark until no weakness remains"},
    )
    assert r.status_code == 200
    assert spawned == {"sid": "s-msgtest0", "resume_continuous": True}


def test_message_stream_error_frame(client: TestClient, monkeypatch) -> None:
    """A triage crash surfaces as an ``error`` frame, not a wedged stream."""
    def _boom(sid, text, *, global_root=None, on_fragment=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("argus_skill.webapi.manager_bridge.manager_message", _boom)
    r = client.post("/api/projects/s-msgtest0/message/stream", json={"text": "你好"})
    assert r.status_code == 200
    frames = _parse_sse(r.text)
    assert frames[-1]["type"] == "error"
    assert "kaboom" in frames[-1]["error"]


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

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
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
        "continuous": True,
        "continuous_objective": "reproduce the recursive kernel task",
        "resume_continuous": True,
    }


def test_create_daemon_persists_only_manager_execution_handoff(
    tmp_path: Path, monkeypatch,
) -> None:
    spawned: dict[str, object] = {}
    _install_manager(monkeypatch, lambda text: "write the MRAM paper")
    monkeypatch.setattr(
        server,
        "spawn_detached_daemon",
        lambda cfg, quiet=True: spawned.update(
            objective=cfg.continuous_objective,
        ) or 0,
    )
    raw = "write the MRAM paper; Manager owns the right sidebar"

    result = server.create_daemon(objective=raw, global_root=tmp_path)

    life_dir = tmp_path / "projects" / result["sid"]
    continuous = json.loads((life_dir / "continuous.json").read_text())
    session = json.loads((life_dir / "session.json").read_text())
    assert continuous["objective"] == "write the MRAM paper"
    assert session["objective"] == "write the MRAM paper"
    assert spawned["objective"] == "write the MRAM paper"
    assert raw not in (life_dir / "continuous.json").read_text()


def test_create_daemon_without_objective_is_idle(tmp_path: Path, monkeypatch) -> None:
    # No objective: creating a daemon is starting a conversation — mint an idle
    # session, DON'T arm continuous, DON'T spawn. The Manager writes objectives
    # later via /message (which lazily spawns).
    spawned: list[object] = []
    monkeypatch.setattr(server, "spawn_detached_daemon", lambda cfg, quiet=True: spawned.append(1) or 0)
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
        return server.DaemonStatus(
            alive=alive,
            pid=99 if alive else None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=path,
            pid_path=path / "daemon.pid",
        )

    monkeypatch.setattr(server, "read_daemon_status", fake_status)
    monkeypatch.setattr(project_state, "read_daemon_status", fake_status)
    monkeypatch.setattr(server, "_max_active_daemons", lambda config: 1)
    monkeypatch.setattr(server, "_active_daemon_count", lambda config: 1)
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
    from argus_skill.core.project_gc import gc_stale_projects

    created = server.create_daemon(global_root=tmp_path)
    sid = created["sid"]
    assert gc_stale_projects(tmp_path, now=time.time() + 2) == []
    assert server.project_life_dir(sid, global_root=tmp_path) is not None


def test_web_daemon_config_uses_resolved_role_models_and_efforts(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "engineer-model")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "reviewer-model")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "xhigh")
    life_dir = tmp_path / "life"
    cfg = server._worker_config_from_env(life_dir, tmp_path)
    assert cfg.project_workdir == life_dir
    assert cfg.engineer_model == "engineer-model"
    assert cfg.reviewer_model == "reviewer-model"
    assert cfg.engineer_reasoning_effort == "high"
    assert cfg.reviewer_reasoning_effort == "xhigh"
