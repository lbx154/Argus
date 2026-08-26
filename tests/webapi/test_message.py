"""POST /message — the Manager front-door endpoint (webapi).

The endpoint uses ``manager_triage``/``enqueue_mission`` via
``webapi.manager_bridge.manager_message``. Here we stub that bridge so the test
stays offline (no LLM call) and asserts the endpoint's contract: chat replies
pass through, task classifications lazily spawn the daemon, empty text 400s, and
an unknown project 404s.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.session import (
    SessionMeta,
    read_session_meta,
    write_session_meta,
)
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.manager import Manager, config_intent, dispatch, front_door
from argus_skill.manager.domain_author import VerticalDecision
from argus_skill.webapi import (
    manager_bridge,
    manager_dispatch,
    manager_state,
    project_state,
    server,
)

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
    manager_state._STATES.clear()

    class _Manager:
        def classify_front_door(self, _text, *, lifetime_sink=None, **_kwargs):
            if lifetime_sink is not None:
                lifetime_sink("standing")
            return None, None, "complex"

        def decide_vertical(self, text, **kwargs):
            return SimpleNamespace(execution_task=execution_for(text))

        def commit_vertical_decision(self, text, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    ensure = lambda chat_state, mem: SimpleNamespace(manager=_Manager())
    monkeypatch.setattr(front_door, "_ensure_manager_runner", ensure)
    monkeypatch.setattr(config_intent, "_ensure_manager_runner", ensure)


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


def test_pure_greeting_uses_one_frontdoor_model_call(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-one-call-greeting"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_greeting_reply"] = "你好，我是 Argus Manager。"
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("pure greeting must not make a second model call")
        ),
    )

    result = manager_bridge.manager_message(sid, "你好", global_root=tmp_path)

    assert result == {"kind": "chat", "reply": "你好，我是 Argus Manager。"}
    assert LifeMemory.open(life).backlog.all() == []


def test_message_only_fast_reply_uses_only_frontdoor_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-one-call-fast-reply"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_self_mode"] = "reply"
        chat_state["_frontdoor_fast_reply"] = "OK"
        return None, "no_dispatch", "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("message-only fast reply must not make a second model call")
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        "reply exactly OK",
        global_root=tmp_path,
    )

    assert result == {"kind": "chat", "reply": "OK"}
    assert LifeMemory.open(life).backlog.all() == []


def test_profile_self_skill_disables_stateless_fast_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-profile-self-reply"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    skill_dir = tmp_path / "skills" / "self"
    skill_dir.mkdir(parents=True)
    (skill_dir / "terminology.md").write_text(
        "---\nname: terminology\ndescription: Apply learned terminology\n---\n",
        encoding="utf-8",
    )
    state = manager_state._chat_state_for(sid)
    state["manager_runner"] = SimpleNamespace(
        manager=SimpleNamespace(
            self_mission=SimpleNamespace(
                libraries=lambda: SimpleNamespace(native_paths=[skill_dir])
            )
        )
    )

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_self_mode"] = "reply"
        chat_state["_frontdoor_fast_reply"] = "generic stale answer"
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: "learned terminology answer",
    )

    result = manager_bridge.manager_message(
        sid,
        "What does the learned term mean?",
        global_root=tmp_path,
    )

    assert result == {"kind": "chat", "reply": "learned terminology answer"}
    assert LifeMemory.open(life).backlog.all() == []


def test_profile_skill_upgrades_reply_mode_without_fast_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-profile-skill-reply-mode"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    skill_dir = tmp_path / "skills" / "engineer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "procedure.md").write_text(
        "---\nname: procedure\ndescription: Apply learned procedure\n---\n",
        encoding="utf-8",
    )
    state = manager_state._chat_state_for(sid)
    state["manager_runner"] = SimpleNamespace(
        manager=SimpleNamespace(
            self_mission=SimpleNamespace(
                libraries=lambda: SimpleNamespace(native_paths=[skill_dir])
            )
        )
    )
    seen: dict[str, str] = {}

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_self_mode"] = "reply"
        return None, None, "simple"

    def triage(*args, **kwargs):
        seen["self_mode"] = kwargs["self_mode"]
        return "learned procedure answer"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", triage)

    result = manager_bridge.manager_message(
        sid,
        "How should this learned procedure work?",
        global_root=tmp_path,
    )

    assert result == {"kind": "chat", "reply": "learned procedure answer"}
    assert seen["self_mode"] == "inspect"
    assert LifeMemory.open(life).backlog.all() == []


def test_followup_self_turn_disables_stateless_fast_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-contextual-self-reply"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    state = manager_state._chat_state_for(sid)
    state["turns"] = 1

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_self_mode"] = "reply"
        chat_state["_frontdoor_fast_reply"] = "I do not know your name."
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: "Your name is Xiaobei.",
    )

    result = manager_bridge.manager_message(
        sid,
        "What was my name?",
        global_root=tmp_path,
    )

    assert result == {"kind": "chat", "reply": "Your name is Xiaobei."}
    assert LifeMemory.open(life).backlog.all() == []


def test_recent_identical_team_request_reuses_durable_existing_item(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-team-replay"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    request = "Create result.txt."
    item = LifeMemory.open(life).backlog.add(
        BacklogItem.new(
            title="Create result",
            objective=request,
            manager_decision={"routed": True},
        )
    )
    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate TEAM replay must not call the model")
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        request,
        global_root=tmp_path,
    )

    assert result["kind"] == "task"
    assert result["duplicate"] is True
    assert result["dispatch_state"] == "already_queued"
    assert result["item"]["id"] == item.id
    assert len(LifeMemory.open(life).backlog.all()) == 1


def test_recent_identical_raw_team_request_survives_manager_rewording(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.core.transcript import append_turn

    sid = "s-team-reworded-replay"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    request = "启动 TEAM，只创建 result.txt。"
    append_turn(life, "operator", request)
    item = LifeMemory.open(life).backlog.add(
        BacklogItem.new(
            title="Create result",
            objective="Create only result.txt.",
            manager_decision={"routed": True},
        )
    )
    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate TEAM replay must not call the model")
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        request,
        global_root=tmp_path,
    )

    assert result["duplicate"] is True
    assert result["item"]["id"] == item.id
    assert len(LifeMemory.open(life).backlog.all()) == 1


def test_explicit_authorization_persists_current_blocker_and_never_dispatches(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-authorization"
    life = _make_project(tmp_path, sid)
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    from argus_skill.manager.control_state import CampaignControlStore

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
    store.clear_wait_for_new_evidence(
        identity=identity,
        terminal_evidence=[{
            "failure_source": "validator_defect",
            "validator_id": "terminal-contract",
            "repair_paths": ["tests/test_terminal_contract.py"],
        }],
        reason="Reviewer diagnosed validator defect",
    )
    store.activate_wait(
        identity=identity,
        wait_id="wait-1",
        blocker_fingerprint="validator:terminal-contract",
        recheck_token="validator-v1",
        watched_paths=["research/RESULT.json"],
    )
    manager_state._STATES.clear()

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_authorization"] = [
            "validator_repair",
            "acceptance_retry",
        ]
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
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
    event = store.get_authorization(result["authorization_id"])
    assert event is not None
    assert event["source_channel"] == "vscode"
    assert event["source_message_id"] == "message-42"
    assert event["validator_id"] == "terminal-contract"
    assert event["allowed_write_paths"] == ["tests/test_terminal_contract.py"]
    assert LifeMemory.open(life).backlog.all() == []


def test_validator_authorization_allows_exact_repair_under_watched_parent(
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
    from argus_skill.manager.control_state import CampaignControlStore

    (life / "continuous.json").write_text(
        json.dumps({"objective": "repair terminal gate", "generation": 2}),
        encoding="utf-8",
    )
    store = CampaignControlStore(life, project_root=workdir)
    identity = store.campaign_identity()
    store.clear_wait_for_new_evidence(
        identity=identity,
        terminal_evidence=[{
            "failure_source": "validator_defect",
            "validator_id": "terminal-contract",
            "repair_paths": ["tests/test_terminal_contract.py"],
        }],
        reason="Reviewer diagnosed validator defect",
    )
    store.activate_wait(
        identity=identity,
        wait_id="wait-parent",
        blocker_fingerprint="validator:terminal-contract",
        recheck_token="validator-v1",
        watched_paths=["tests"],
    )
    manager_state._STATES.clear()

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_authorization"] = ["validator_repair"]
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
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
    assert event["frozen_evidence"] == []
    assert event["allowed_write_paths"] == ["tests/test_terminal_contract.py"]


def test_authorization_without_current_blocker_fails_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-auth-no-blocker"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_authorization"] = ["resume_blocked_work"]
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)

    result = manager_bridge.manager_message(
        sid,
        "authorize resume",
        global_root=tmp_path,
    )

    assert result["control"] == "authorization_rejected"
    assert "no current Manager-bound blocker" in result["reply"]
    assert LifeMemory.open(life).backlog.all() == []


def test_repeated_greeting_calls_frontdoor_every_time_without_cache(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-greeting-no-cache"
    _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    classify_calls = 0

    def classify(mem, text, chat_state, **kwargs):
        nonlocal classify_calls
        classify_calls += 1
        chat_state["_frontdoor_greeting_reply"] = "你好，我是 Argus Manager。"
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("pure greeting must not make a second model call")
        ),
    )

    first = manager_bridge.manager_message(sid, "你好", global_root=tmp_path)
    second = manager_bridge.manager_message(sid, "你好", global_root=tmp_path)

    expected = {"kind": "chat", "reply": "你好，我是 Argus Manager。"}
    assert first == expected
    assert second == expected
    assert classify_calls == 2


def test_contextual_greeting_calls_real_manager(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-context-greeting"
    _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    triage_calls: list[str] = []

    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, None, "simple"),
    )

    def triage(mem, body, chat_state, **kwargs):
        triage_calls.append(body)
        return "当前任务仍在推进。"

    monkeypatch.setattr(front_door, "manager_triage", triage)

    result = manager_bridge.manager_message(
        sid,
        "你好，项目现在进展怎么样？",
        global_root=tmp_path,
    )

    assert triage_calls == ["你好，项目现在进展怎么样？"]
    assert result == {"kind": "chat", "reply": "当前任务仍在推进。"}


def test_advertised_what_are_you_doing_query_uses_frontdoor_manager_path(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-status-frontdoor"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    classified = threading.Event()
    calls: list[tuple[str, str, str | None]] = []

    def classify(mem, text, chat_state, **kwargs):
        calls.append(("classify", text, None))
        classified.set()
        return None, None, "simple"

    def triage(mem, body, chat_state, **kwargs):
        calls.append(("triage", body, kwargs.get("route")))
        return "当前即时状态由 Manager 读取项目状态后回复。"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", triage)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with manager_state.manager_context_lock(sid):
            future = pool.submit(
                manager_bridge.manager_message,
                sid,
                "你在干什么？",
                global_root=tmp_path,
            )
            assert not classified.wait(timeout=0.05)
            assert not future.done()
        result = future.result(timeout=2)

    assert result == {"kind": "chat", "reply": "当前即时状态由 Manager 读取项目状态后回复。"}
    assert calls == [
        ("classify", "你在干什么？", None),
        ("triage", "你在干什么？", "simple"),
    ]
    assert LifeMemory.open(life).backlog.all() == []


def test_natural_pause_is_frontdoor_control_after_pending_question_manager_check(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus_skill.daemon.state import read_continuous_state, write_continuous_config

    sid = "s-natural-pause"
    life = _make_project(tmp_path, sid)
    memory = LifeMemory.open(life)
    running = memory.backlog.add(BacklogItem.new(title="looping", objective="work"))
    memory.backlog.mark_running(running.id)
    blocked = BacklogItem.new(title="blocked", objective="choose")
    blocked.pending_question = "Which route?"
    memory.backlog.add(blocked)
    write_continuous_config(life, enabled=True, objective="keep optimizing")
    calls: list[str] = []

    def triage(mem, body, chat_state, **kwargs):
        calls.append("pending-question-manager")
        assert "Operator response:\n停一下，你在干什么？" in body
        return "IS_ANSWER=false\nRESOLVED=false\nDECISION=\nREPLY=\n"

    def classify(mem, text, chat_state, **kwargs):
        calls.append("frontdoor-classifier")
        assert text == "停一下，你在干什么？"
        return None, "pause", "simple"

    monkeypatch.setattr(front_door, "manager_triage", triage)
    monkeypatch.setattr(config_intent, "_front_door_classify", classify)

    result = manager_bridge.manager_message(
        sid,
        "停一下，你在干什么？",
        global_root=tmp_path,
    )

    assert calls == ["pending-question-manager", "frontdoor-classifier"]
    assert result["kind"] == "control"
    assert result["control"] == "pause"
    assert result["pause_persisted"] is True
    assert result["item_id"] == running.id
    assert read_continuous_state(life).enabled is False
    assert (life / "running_item_abort.json").exists()
    stored = LifeMemory.open(life).backlog.all()
    assert next(row for row in stored if row.id == blocked.id).pending_question == "Which route?"


def test_natural_pause_waits_for_busy_manager_session_lock_and_uses_classifier(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-pause-lock-frontdoor"
    _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    classified = threading.Event()

    def classify(mem, text, chat_state, **kwargs):
        classified.set()
        assert text == "停一下"
        return None, "pause", "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("classified pause control must not fall through to triage")
        ),
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with manager_state.manager_context_lock(sid):
            future = pool.submit(
                manager_bridge.manager_message,
                sid,
                "停一下",
                global_root=tmp_path,
            )
            assert not classified.wait(timeout=0.05)
            assert not future.done()
        result = future.result(timeout=2)

    assert classified.is_set()
    assert result["kind"] == "control"
    assert result["control"] == "pause"


def test_pause_is_serialized_after_inflight_manager_turn(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-pause-serialized"
    life = _make_project(tmp_path, sid)
    entered = threading.Event()
    release = threading.Event()
    call_order: list[str] = []

    def classify(mem, text, chat_state, **kwargs):
        if "start another long-running task" in text:
            call_order.append("first-classify")
            entered.set()
            assert release.wait(timeout=3)
            return None, None, "simple"
        call_order.append("pause-classify")
        assert text == "停一下"
        return None, "pause", "simple"

    def triage(mem, body, chat_state, **kwargs):
        call_order.append("first-triage")
        assert body == "start another long-running task"
        return "First Manager turn completed inline."

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", triage)

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
    assert call_order == ["first-classify", "first-triage", "pause-classify"]
    assert LifeMemory.open(life).backlog.all() == []


def test_classifier_explanation_cannot_escape_as_manager_reply(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-fast-leak"
    _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    triage_calls: list[str] = []

    def classify(mem, text, chat_state, **kwargs):
        # Even a custom/legacy classifier trying to smuggle a reply through
        # chat_state cannot bypass the actual Manager turn.
        chat_state["_frontdoor_fast_reply"] = (
            "这是需要结合具体上下文的反思性问题，不属于通用闲聊，需在对话中实质回应。"
        )
        return None, None, "simple"

    def triage(mem, body, chat_state, **kwargs):
        triage_calls.append(body)
        return "有加深，具体体现在对核心障碍的理解上。"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", triage)

    result = manager_bridge.manager_message(
        sid,
        "你觉得你对这个问题的理解是否有加深？",
        global_root=tmp_path,
    )

    assert triage_calls == ["你觉得你对这个问题的理解是否有加深？"]
    assert result == {
        "kind": "chat",
        "reply": "有加深，具体体现在对核心障碍的理解上。",
    }


def test_frontdoor_classifier_failure_never_dispatches_unclassified_message(
    tmp_path: Path, monkeypatch,
) -> None:
    sid = "s-classify-fail"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()

    def failed_classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_failure"] = "classifier failed"
        return None, None, "complex"

    monkeypatch.setattr(config_intent, "_front_door_classify", failed_classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)

    result = manager_bridge.manager_message(
        sid,
        "请介绍当前状态",
        global_root=tmp_path,
    )

    assert result["kind"] == "chat"
    assert result["reply"].startswith("[not dispatched]")
    assert "Manager backend" in result["reply"]
    assert "argus doctor --deep" in result["reply"]
    assert LifeMemory.open(life).backlog.all() == []


def test_cancelled_manager_request_cannot_dispatch_after_classification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-cancelled"
    life = _make_project(tmp_path, sid)
    cancelled = threading.Event()

    def classify(*args, **kwargs):
        cancelled.set()
        return None, None, "complex"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cancelled request must stop before triage/dispatch")
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        "start a long task",
        global_root=tmp_path,
        cancelled=cancelled.is_set,
    )

    assert result["kind"] == "cancelled"
    assert LifeMemory.open(life).backlog.all() == []


def test_cancel_during_handoff_stops_before_backlog_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-cancel-commit"
    life = _make_project(tmp_path, sid)
    cancelled = threading.Event()
    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, None, "complex"),
    )
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dispatch,
        "maybe_promote_to_continuous",
        lambda *args, **kwargs: False,
    )

    def handoff(mem, body, state, persist, **kwargs):
        cancelled.set()
        return persist("safe task", None)

    monkeypatch.setattr(front_door, "manager_bounded_handoff", handoff)

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
    def classify(_mem, _text, chat_state, **_kwargs):
        chat_state["_frontdoor_steering_directive"] = (
            "暂停当前形式化路线；先检索最接近的前人研究，再由 Planner "
            "根据来源证据安排下一节点。"
        )
        chat_state["_frontdoor_lifetime"] = "standing"
        chat_state["_frontdoor_operator_question_policy"] = "forbid"
        return None, "steer", "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("structured steering must not run a second model turn")
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        "停止形式检查，优先发明新的数学工具",
        global_root=tmp_path,
    )

    assert result["kind"] == "control"
    assert result["control"] == "steer"
    assert result["continuous"] is True
    assert "我已调整团队方向" in result["reply"]
    assert "已升级为持续任务" in result["reply"]
    inbox = [
        json.loads(line)
        for line in (life / "inbox.jsonl").read_text().splitlines()
    ]
    assert "Operator steering (standing)" in inbox[-1]["text"]
    assert "检索最接近的前人研究" in inbox[-1]["text"]
    assert "发明新的数学工具" not in inbox[-1]["text"]
    from argus_skill.manager.directive import load_active_manager_directive

    active = load_active_manager_directive(life)
    assert active is not None
    assert "检索最接近的前人研究" in active.text
    assert "发明新的数学工具" not in active.text
    assert active.operator_question_policy == "forbid"
    from argus_skill.daemon.state import read_continuous_state

    continuous = read_continuous_state(life)
    assert continuous.enabled is True
    assert continuous.open_ended is True
    assert continuous.objective == active.text


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

    class _FailingManager:
        def decide_vertical(self, text, **kwargs):
            raise RuntimeError("provider quota reached")

    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, None, "complex"),
    )
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *args, **kwargs: SimpleNamespace(manager=_FailingManager()),
    )
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
    seen = {"classify": 0}

    def classify(*args, **kwargs):
        seen["classify"] += 1
        return None, None, "complex"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)

    def promote(mem, body, chat_state, **kwargs):
        seen["promoted"] = body
        chat_state.setdefault("config", {})["continuous"] = True
        return True

    def enqueue(mem, body, chat_state, **kwargs):
        seen["enqueued"] = body
        return None, True, 123

    monkeypatch.setattr(dispatch, "maybe_promote_to_continuous", promote)
    monkeypatch.setattr(dispatch, "enqueue_mission", enqueue)

    result = manager_bridge.manager_message(
        sid,
        "你怎么不动了？",
        global_root=tmp_path,
    )

    assert result["kind"] == "task"
    assert result["continuous"] is True
    assert seen["classify"] == 1
    assert "last_team_task: finish current work" in seen["promoted"]
    assert "[CURRENT OPERATOR MESSAGE]\n你怎么不动了？" in seen["promoted"]
    assert seen["enqueued"] == seen["promoted"]
    assert len(memory.backlog.all()) == 1


def test_mission_claimed_during_classification_still_uses_continuous_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-active-race"
    life = _make_project(tmp_path, sid)
    memory = LifeMemory.open(life)
    manager_state._STATES.clear()

    def classify(*args, **kwargs):
        item = memory.backlog.add(
            BacklogItem.new(title="concurrent work", objective="work")
        )
        assert memory.backlog.mark_running(item.id) is not None
        return None, None, "complex"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *a, **k: None)

    def promote(mem, body, chat_state, **kwargs):
        chat_state.setdefault("config", {})["continuous"] = True
        return True

    monkeypatch.setattr(
        dispatch,
        "maybe_promote_to_continuous",
        promote,
    )
    monkeypatch.setattr(
        dispatch,
        "enqueue_mission",
        lambda *a, **k: (None, True, 456),
    )

    result = manager_bridge.manager_message(
        sid,
        "start another task",
        global_root=tmp_path,
    )

    assert result["kind"] == "task"
    assert result["continuous"] is True
    assert len(memory.backlog.all()) == 1


def test_no_dispatch_control_stays_inline_even_if_route_says_team(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-no-dispatch"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, "no_dispatch", "complex"),
    )

    def reply(mem, body, state, **kwargs):
        seen["route"] = kwargs.get("route")
        return "read-only result"

    monkeypatch.setattr(front_door, "manager_triage", reply)
    monkeypatch.setattr(
        dispatch,
        "enqueue_mission",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("NO_DISPATCH must never enqueue")
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        "inspect read-only; do not dispatch",
        global_root=tmp_path,
    )

    assert result == {"kind": "chat", "reply": "read-only result"}
    assert seen["route"] == "simple"
    assert LifeMemory.open(life).backlog.all() == []


def test_no_dispatch_control_fails_closed_when_inline_reply_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-no-dispatch-fail"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, "no_dispatch", "simple"),
    )
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dispatch,
        "enqueue_mission",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("failed inline NO_DISPATCH must not enqueue")
        ),
    )

    result = manager_bridge.manager_message(
        sid,
        "read only and do not dispatch",
        global_root=tmp_path,
    )

    assert result["kind"] == "chat"
    assert result["reply"].startswith("[not dispatched]")
    assert LifeMemory.open(life).backlog.all() == []


def test_simple_route_reply_failure_never_falls_through_to_task_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-simple-fail"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, None, "simple"),
    )
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)
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

    assert result["kind"] == "chat"
    assert result["reply"].startswith("[not dispatched]")
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

    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, None, "complex"),
    )
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)

    def promote(mem, body, chat_state, **kwargs):
        assert seen["order"] == ["lifecycle"]
        seen["order"].append("promote")
        seen["promoted_body"] = body
        seen["root_task_id"] = kwargs.get("root_task_id")
        chat_state.setdefault("config", {})["continuous"] = True
        return True

    def enqueue(mem, body, chat_state, **kwargs):
        assert seen["order"] == ["lifecycle", "promote"]
        seen["order"].append("enqueue")
        seen["continuous_at_enqueue"] = chat_state["config"]["continuous"]
        return None, False, None

    def resume(mem):
        seen["order"] = ["lifecycle"]
        seen["resumed"] = True
        return False

    monkeypatch.setattr(dispatch, "maybe_promote_to_continuous", promote)
    monkeypatch.setattr(dispatch, "resume_done_lifecycle_for_team_dispatch", resume)
    monkeypatch.setattr(dispatch, "enqueue_mission", enqueue)

    result = manager_bridge.manager_message(
        sid,
        "keep researching this conjecture",
        global_root=tmp_path,
    )

    assert seen["promoted_body"] == "keep researching this conjecture"
    assert seen["root_task_id"]
    assert seen["resumed"] is True
    assert seen["continuous_at_enqueue"] is True
    assert seen["order"] == ["lifecycle", "promote", "enqueue"]
    assert result["kind"] == "task"
    assert result["item"] is None
    assert result["continuous"] is True


def test_finite_staged_paper_request_does_not_enter_bounded_dag(
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

    def classify(_mem, _text, chat_state, **_kwargs):
        # This is the exact regression: the finish line is finite, so the cheap
        # front door says BOUNDED, while Manager correctly says STAGED.
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
    monkeypatch.setattr(
        dispatch,
        "_plan_bounded_execution",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("staged paper must not be collapsed into a bounded DAG")
        ),
    )

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
    assert continuous["objective"] == objective


def test_manager_decided_math_vertical_web_enqueue_enters_backlog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-explicit-math"
    life = _make_project(tmp_path, sid)
    manager_state._STATES.clear()
    objective = "prove the bounded integer lemma"
    manager = Manager(project_root=life)
    monkeypatch.setattr(
        manager,
        "_decide_research_target",
        lambda *args, **kwargs: "exploratory",
    )

    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    monkeypatch.setattr(
        manager,
        "decide_vertical",
        lambda task, **kwargs: VerticalDecision(
            choice="existing",
            vertical="math",
            workflow_mode="direct",
            execution_task=task,
            research_target_level="exploratory",
        ),
    )
    monkeypatch.setattr(
        config_intent,
        "_front_door_classify",
        lambda *args, **kwargs: (None, None, "complex"),
    )
    monkeypatch.setattr(front_door, "manager_triage", lambda *args, **kwargs: None)

    class _PlannerBackend:
        def run_exec(self, **_kwargs):
            return SimpleNamespace(
                exit_code=0,
                fatal_error=None,
                agent_messages=[
                    "\n".join([
                        "PLAN_REASON=one bounded proof task",
                        "TASK_KEY=proof",
                        "TASK_DEPS=",
                        "TASK_TITLE=Prove the bounded integer lemma",
                        f"TASK_OBJECTIVE={objective}",
                        "TASK_HYPOTHESIS=The stated proof route can close the lemma.",
                        "TASK_GOAL_CONTRIBUTION=Produce the bounded proof the user requested.",
                        "TASK_EXPECTED_REGRESSIONS=Intermediate obligations may temporarily reopen.",
                        "TASK_DECISION_RULE=Replan if a counterexample refutes the proof route.",
                        "TASK_SCOPE=bounded",
                        "TASK_STAGE_CLOSING=false",
                        "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
                        "TASK_SKIP_STAGE_TRANSITION=false",
                        "TASK_OPERATOR_APPROVAL_REQUIRED=false",
                        "TASK_ALLOW_SKILL_CHANGES=false",
                    ])
                ],
            )

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda chat_state, mem: SimpleNamespace(
            manager=manager,
            planner_backend=_PlannerBackend(),
        ),
    )
    monkeypatch.setattr(
        dispatch,
        "maybe_promote_to_continuous",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        server,
        "start_project_daemon",
        lambda *args, **kwargs: {"alive": True},
    )
    client = TestClient(server.create_app(global_root=tmp_path))

    response = client.post(
        f"/api/projects/{sid}/message",
        json={"text": objective},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "task"
    assert payload["item"]["status"] == "pending"
    backlog = LifeMemory.open(life).backlog.all()
    assert len(backlog) == 1
    assert backlog[0].objective == objective
    state = json.loads(
        (life / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["vertical"] == "math"
    assert state["research_target_level"] == "exploratory"




def test_message_empty_400(client: TestClient) -> None:
    assert client.post("/api/projects/s-msgtest0/message", json={"text": "  "}).status_code == 400


def test_message_unknown_project_404(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.webapi.manager_bridge.manager_message",
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
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the explicit answer endpoint must not call a model")
        ),
    )
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
    assert continuation.manager_decision == {"routed": True}
    assert "MANAGER OPERATOR-ANSWER DECISION" in (
        life / "inbox.jsonl"
    ).read_text(encoding="utf-8")
    assert "life.operator_question.answered" in (
        life / "events.jsonl"
    ).read_text(encoding="utf-8")

    duplicate = client.post(
        f"/api/projects/s-msgtest0/backlog/{blocked.id}/answer",
        json={"text": "A duplicate answer."},
    )
    assert duplicate.status_code == 409
    assert len(LifeMemory.open(life).backlog.all()) == 2


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

    _, continuation = mem.backlog.continue_with_operator_reply(
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
        "This decision supersedes every conflicting requirement"
        in continuation.objective
    )
    assert continuation.acceptance_check.startswith(
        "The Manager operator-answer decision in this continuation is authoritative."
    )
    assert continuation.non_goals[0].startswith(
        "Subject to the authoritative Manager operator-answer decision"
    )


def test_concurrent_pending_answers_create_one_continuation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path)
    blocked = BacklogItem.new(title="Blocked", objective="Original objective")
    blocked.pending_question = "Choose A or B?"
    LifeMemory.open(life).backlog.add(blocked)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the explicit answer endpoint must not call a model")
        ),
    )

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


def test_manager_message_resolves_single_pending_question(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path)
    blocked = BacklogItem.new(title="Choose GPU", objective="Run the matrix")
    blocked.status = "failed"
    blocked.pending_question = "Which GPU may I use?"
    LifeMemory.open(life).backlog.add(blocked)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: json.dumps({
            "is_answer": True,
            "resolved": True,
            "decision": "Use GPU 1 through the project allocation contract.",
            "reply": "GPU 1 is now authorized for the continuation.",
        }),
    )

    result = manager_bridge.manager_message(
        "s-msgtest0",
        "Use GPU 1.",
        global_root=tmp_path,
    )

    assert result["kind"] == "pending_question"
    assert result["resolved"] is True
    assert result["answered_item_id"] == blocked.id
    rows = LifeMemory.open(life).backlog.all()
    assert len(rows) == 2
    assert next(row for row in rows if row.id == blocked.id).pending_question == ""


def test_non_answer_message_falls_through_without_clearing_pending_question(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path)
    blocked = BacklogItem.new(title="Choose GPU", objective="Run the matrix")
    blocked.status = "failed"
    blocked.pending_question = "Which GPU may I use?"
    LifeMemory.open(life).backlog.add(blocked)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: json.dumps({
            "is_answer": False,
            "resolved": False,
            "decision": "",
            "reply": "",
        }),
    )

    result = manager_bridge.manager_message(
        "s-msgtest0",
        "What is the status?",
        global_root=tmp_path,
    )

    assert result["kind"] == "chat"
    rows = LifeMemory.open(life).backlog.all()
    assert len(rows) == 1
    assert rows[0].pending_question == "Which GPU may I use?"


def test_manager_keeps_pending_question_when_answer_is_insufficient(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path)
    blocked = BacklogItem.new(title="Choose GPU", objective="Run the matrix")
    blocked.status = "failed"
    blocked.pending_question = "Which GPU may I use?"
    LifeMemory.open(life).backlog.add(blocked)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *args, **kwargs: json.dumps({
            "is_answer": True,
            "resolved": False,
            "decision": "",
            "reply": "Please provide the approved GPU number.",
        }),
    )

    result = manager_bridge.manager_message(
        "s-msgtest0",
        "Use one of the free cards.",
        global_root=tmp_path,
    )

    assert result["kind"] == "pending_question"
    assert result["resolved"] is False
    rows = LifeMemory.open(life).backlog.all()
    assert len(rows) == 1
    assert rows[0].pending_question == "Which GPU may I use?"


# ── streaming front-door: POST /message/stream (Server-Sent Events) ──────────

def _parse_sse(text: str) -> list[dict]:
    """Collect the JSON payloads of every ``data:`` frame in an SSE body."""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):].strip()))
    return out


def test_turn_emitter_schedules_learning_only_for_chat(tmp_path: Path) -> None:
    from argus_skill.webapi.manager_dispatch import _TurnEmitter

    reviewed: list[str] = []
    emitter = _TurnEmitter(
        life_dir=tmp_path,
        turn_id="turn-1",
        fragment=lambda *_args: None,
        after_reply=reviewed.append,
    )

    emitter.respond("answer", {"kind": "chat"})
    emitter.respond("queued", {"kind": "task"})

    assert reviewed == ["answer"]


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


def test_turn_emitter_schedules_learning_only_after_chat_reply(tmp_path) -> None:
    from argus_skill.webapi.manager_dispatch import _TurnEmitter

    learned: list[str] = []
    emitter = _TurnEmitter(
        life_dir=tmp_path,
        turn_id="turn-1",
        fragment=lambda *_args: None,
        after_reply=learned.append,
    )

    emitter.respond("answer", {"kind": "chat"})
    emitter.respond("queued", {"kind": "task"})

    assert learned == ["answer"]


def test_message_stream_emits_phase_delta_done(client: TestClient, monkeypatch) -> None:
    """A streamed chat turn: the endpoint forwards each on_fragment(phase|delta)
    live, then a final ``done`` frame carrying the classification + reply."""
    def _streaming(
        sid, text, *, global_root=None, on_fragment=None, cancelled=None,
    ):
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
    def _streaming(
        sid, text, *, global_root=None, on_fragment=None, cancelled=None,
    ):
        assert cancelled is None
        return {"kind": "task", "reply": None,
                "item": {"id": "x9", "title": "optimize kernel"}, "daemon_alive": False}

    monkeypatch.setattr("argus_skill.webapi.manager_bridge.manager_message", _streaming)
    spawned: dict[str, object] = {}
    monkeypatch.setattr(
        server, "start_project_daemon",
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
    client: TestClient, monkeypatch,
) -> None:
    def _streaming(
        sid, text, *, global_root=None, on_fragment=None, cancelled=None,
    ):
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
    def _boom(sid, text, *, global_root=None, on_fragment=None, cancelled=None):
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
        "continuous": False,
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
    def _name_from_front_door(mem, text, chat_state, **_kwargs):
        front_door._maybe_name_session(
            chat_state,
            text,
            suggested_name="MRAM paper",
        )
        return None, None, "complex"

    monkeypatch.setattr(config_intent, "_front_door_classify", _name_from_front_door)
    raw = "write the MRAM paper; Manager owns the right sidebar"

    result = server.create_daemon(objective=raw, global_root=tmp_path)

    life_dir = tmp_path / "projects" / result["sid"]
    continuous = json.loads((life_dir / "continuous.json").read_text())
    session = json.loads((life_dir / "session.json").read_text())
    assert continuous["objective"] == "write the MRAM paper"
    assert session["objective"] == "write the MRAM paper"
    assert session["display_name"] == "MRAM paper"
    assert spawned["objective"] == "write the MRAM paper"
    assert raw not in (life_dir / "continuous.json").read_text()


@pytest.mark.parametrize(
    ("lifetime", "expected_open_ended"),
    [("bounded", False), ("standing", True)],
)
def test_named_daemon_uses_manager_lifetime(
    tmp_path: Path,
    monkeypatch,
    lifetime: str,
    expected_open_ended: bool,
) -> None:
    monkeypatch.setattr(
        server,
        "spawn_detached_daemon",
        lambda *_args, **_kwargs: 0,
    )

    def _classify(mem, text, chat_state, **_kwargs):
        chat_state["_frontdoor_lifetime"] = lifetime
        return None, None, "complex"

    monkeypatch.setattr(config_intent, "_front_door_classify", _classify)

    result = server.create_daemon(
        objective="write one reviewed report and stop",
        name="finite report",
        global_root=tmp_path,
    )

    continuous = json.loads(
        (tmp_path / "projects" / result["sid"] / "continuous.json").read_text()
    )
    assert continuous["open_ended"] is expected_open_ended


def test_create_daemon_preserves_manual_rename_during_manager_handoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server,
        "spawn_detached_daemon",
        lambda *_args, **_kwargs: 0,
    )

    def _handoff(sid, objective, *, global_root=None, name_session=False):
        assert name_session is True
        renamed = server.update_project(
            sid,
            name="Operator title",
            global_root=global_root,
        )
        assert renamed is not None
        return "Manager-authored objective"

    monkeypatch.setattr(manager_dispatch, "manager_continuous_handoff", _handoff)

    result = server.create_daemon(
        objective="raw operator objective",
        global_root=tmp_path,
    )

    session = json.loads(
        (tmp_path / "projects" / result["sid"] / "session.json").read_text()
    )
    assert session["display_name"] == "Operator title"
    assert session["objective"] == "Manager-authored objective"


def test_create_daemon_normalizes_explicit_name(tmp_path: Path) -> None:
    result = server.create_daemon(
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
    monkeypatch.setattr(
        server,
        "spawn_detached_daemon",
        lambda *_args, **_kwargs: 0,
    )
    created = server.create_daemon(global_root=tmp_path)

    item = server.enqueue_task(
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
    assert "direct task" in display_name


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


def test_create_daemon_never_overwrites_global_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "4321"}),
        encoding="utf-8",
    )

    server.create_daemon(global_root=tmp_path)

    assert json.loads(config.read_text())["ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"] == "4321"


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
    monkeypatch.setenv("ARGUS_SKILL_PLANNER_TASK_ITERATION_MAX_CYCLES", "9")
    life_dir = tmp_path / "life"
    life_dir.mkdir()
    write_session_meta(
        tmp_path,
        SessionMeta(id=life_dir.name, cwd=str(life_dir), workdir=str(life_dir)),
    )
    cfg = server._worker_config_from_env(life_dir, tmp_path)
    assert cfg.project_workdir == life_dir.resolve()
    assert cfg.engineer_model == "engineer-model"
    assert cfg.reviewer_model == "reviewer-model"
    assert cfg.engineer_reasoning_effort == "high"
    assert cfg.reviewer_reasoning_effort == "xhigh"
    assert cfg.planner_task_iteration_max_cycles == 9


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

    cfg = server._worker_config_from_env(life_dir, tmp_path)

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

    cfg = server._worker_config_from_env(life_dir, tmp_path)

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
        server,
        "read_daemon_status",
        lambda _path: SimpleNamespace(project_workdir=str(workspace)),
    )

    cfg = server._worker_config_from_env(life_dir, tmp_path)
    meta = read_session_meta(tmp_path, sid)

    assert cfg.project_workdir == workspace.resolve()
    assert meta is not None
    assert meta.workdir == str(workspace.resolve())


def test_web_daemon_config_refuses_legacy_session_without_workdir(
    tmp_path: Path,
) -> None:
    sid = "legacy-without-workdir"
    life_dir = tmp_path / "projects" / sid
    life_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="no trustworthy workdir"):
        server._worker_config_from_env(life_dir, tmp_path)

    assert read_session_meta(tmp_path, sid) is None


def test_web_daemon_start_reports_legacy_session_without_workdir(
    tmp_path: Path,
) -> None:
    sid = "legacy-without-workdir"
    life_dir = tmp_path / "projects" / sid
    life_dir.mkdir(parents=True)

    result = server.start_project_daemon(sid, global_root=tmp_path)

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
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {"ARGUS_SKILL_RUNNER_BACKEND": "copilot"},
    )

    life_dir = tmp_path / "life"
    life_dir.mkdir()
    write_session_meta(
        tmp_path,
        SessionMeta(id=life_dir.name, cwd=str(life_dir), workdir=str(life_dir)),
    )
    cfg = server._worker_config_from_env(life_dir, tmp_path)

    assert cfg.backend == "copilot"
