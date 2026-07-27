"""Persistent Manager session + context-rotation (webapi.manager_bridge).

The Manager session stays alive across turns (its thread id is resumed) and is
ROTATED only when its context fills — a fresh thread seeded with a STRUCTURED
handoff. Here we stub the Manager triage so the test is offline and assert the
rotation fires at the threshold: thread reset + handoff carrying the identity +
project path is prepended to the first post-rotation turn.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.webapi import manager_bridge, manager_state


def _make_project(root: Path, sid: str = "s-rot00001") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text("", encoding="utf-8")
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    (life / "session.json").write_text(
        json.dumps({"id": sid, "created": time.time(), "last_active": time.time()}),
        encoding="utf-8",
    )
    return life


def test_manager_prewarm_schedule_is_one_shot_after_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager_bridge._STATES.clear()
    manager_bridge._MANAGER_PREWARMING.clear()
    calls: list[tuple[str, Path | None]] = []

    def fake_prewarm(sid: str, *, global_root=None) -> None:
        calls.append((sid, Path(global_root) if global_root is not None else None))
        manager_bridge._STATES.setdefault(sid, {})["_manager_acp_prewarmed"] = True

    class InlineThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:  # noqa: ANN001
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(manager_state, "_prewarm_manager_context", fake_prewarm)
    monkeypatch.setattr(manager_state.threading, "Thread", InlineThread)

    manager_state.schedule_manager_prewarm("s-prewarm01", global_root=tmp_path)
    manager_state.schedule_manager_prewarm("s-prewarm01", global_root=tmp_path)

    assert calls == [("s-prewarm01", tmp_path)]
    assert manager_bridge._MANAGER_PREWARMING == set()


def test_manager_session_rotates_with_structured_handoff(tmp_path: Path, monkeypatch) -> None:
    _make_project(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_ROTATE_TURNS", "4")
    manager_bridge._STATES.clear()

    seen: list[str] = []

    def _fake_triage(mem, body, chat_state, **_kw):
        seen.append(body)
        chat_state["last_thread_id"] = "thread-xyz"  # a live session accrues a thread
        return "ok"

    # Offline: stub the merged front-door classify (no real runner/LLM) + triage.
    monkeypatch.setattr(
        "argus_skill.manager.config_intent._front_door_classify", lambda *a, **k: (None, "simple")
    )
    monkeypatch.setattr("argus_skill.manager.front_door.manager_triage", _fake_triage)

    # Turns 1..4 stay on the same session (thread resumed, no handoff).
    for _ in range(4):
        r = manager_bridge.manager_message(
            "s-rot00001",
            "checking in",
            global_root=tmp_path,
        )
        assert r["kind"] == "chat"
    events = [
        json.loads(line)
        for line in (tmp_path / "projects" / "s-rot00001" / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [event["type"] for event in events if event["type"].startswith("ui.")] == [
        event_type
        for _ in range(4)
        for event_type in ("ui.operator", "ui.argus")
    ]
    assert all("SESSION HANDOFF" not in b for b in seen)
    st = manager_bridge._STATES["s-rot00001"]
    assert st["last_thread_id"] == "thread-xyz"

    # Turn 5 crosses the threshold → rotate: fresh thread + handoff prepended.
    manager_bridge.manager_message("s-rot00001", "still there?", global_root=tmp_path)
    assert "SESSION HANDOFF" in seen[-1]
    assert "s-rot00001" in seen[-1]  # handoff carries the project path
    assert st["rotations"] == 1
    assert st["turns"] == 1  # counter reset after rotation


def test_rotation_resets_cached_runner_seed(tmp_path: Path, monkeypatch) -> None:
    """Rotation must clear the CACHED RUNNER's own session memory too.

    ``_simple_quick_reply`` falls back to ``runner._next_seed_thread_id`` when the
    caller passes ``seed_thread_id=None`` — so resetting only
    ``chat_state["last_thread_id"]`` let the runner resurrect the just-rotated
    thread. Rotation then never took and the session grew unbounded (its resume
    cost climbing every turn). This pins that the bridge resets the runner too.
    """
    _make_project(tmp_path, sid="s-rot00003")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_ROTATE_TURNS", "4")
    manager_bridge._STATES.clear()

    class _FakeRunner:
        def __init__(self) -> None:
            self._next_seed_thread_id = "old-thread"
            self.last_thread_id = "old-thread"
            self.reset_calls = 0

        def reset_chat_session(self) -> None:
            self.reset_calls += 1
            self._next_seed_thread_id = None
            self.last_thread_id = None

    runner = _FakeRunner()

    def _fake_triage(mem, body, chat_state, **_kw):
        # Mirror the front-door: the runner is cached and accrues a thread id.
        chat_state["manager_runner"] = runner
        chat_state["last_thread_id"] = runner._next_seed_thread_id or "old-thread"
        return "ok"

    # Stub BOTH front-door steps so the test is offline (no real runner built).
    monkeypatch.setattr(
        "argus_skill.manager.config_intent._front_door_classify", lambda *a, **k: (None, "simple")
    )
    monkeypatch.setattr("argus_skill.manager.front_door.manager_triage", _fake_triage)

    for _ in range(4):  # turns 1..4 — no rotation yet
        manager_bridge.manager_message(
            "s-rot00003",
            "checking in",
            global_root=tmp_path,
        )
    assert runner.reset_calls == 0
    assert runner._next_seed_thread_id == "old-thread"

    # Turn 5 crosses the threshold → rotation must reset the runner's seed so the
    # next reply starts a FRESH thread instead of resurrecting the old one.
    manager_bridge.manager_message("s-rot00003", "still there?", global_root=tmp_path)
    assert runner.reset_calls == 1
    assert runner._next_seed_thread_id is None


def test_handoff_names_identity_and_path(tmp_path: Path) -> None:
    life = _make_project(tmp_path, "s-rot00002")
    ho = manager_bridge._build_handoff(life)
    assert "Argus Manager" in ho
    assert str(life) in ho
    assert "events.jsonl" in ho  # tells it where to self-serve state


def test_manager_stream_announces_classification_before_model_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A silent classifier still leaves the TUI on a truthful concrete phase."""
    _make_project(tmp_path, "s-phase001")
    manager_bridge._STATES.clear()
    fragments: list[tuple[str, dict]] = []

    def _classify(mem, text, chat_state, *, root_task_id=None):
        assert fragments == [
            (
                "phase",
                {"role": "manager", "label": "Manager · classifying this message"},
            )
        ]
        return None, "simple"

    monkeypatch.setattr("argus_skill.manager.config_intent._front_door_classify", _classify)
    monkeypatch.setattr(
        "argus_skill.manager.front_door.manager_triage",
        lambda *a, **k: "done",
    )

    result = manager_bridge.manager_message(
        "s-phase001",
        "what now?",
        global_root=tmp_path,
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert result == {"kind": "chat", "reply": "done"}


def test_manager_stream_and_persisted_reply_share_message_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path, "s-stream001")
    manager_bridge._STATES.clear()
    fragments: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        "argus_skill.manager.config_intent._front_door_classify",
        lambda *a, **k: (None, "simple"),
    )

    def _triage(*args, on_fragment=None, **kwargs):
        assert on_fragment is not None
        on_fragment("delta", {"text": "done", "message_id": "internal-id"})
        return "done"

    monkeypatch.setattr("argus_skill.manager.front_door.manager_triage", _triage)

    result = manager_bridge.manager_message(
        "s-stream001",
        "what now?",
        global_root=tmp_path,
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert result == {"kind": "chat", "reply": "done"}
    delta = next(payload for kind, payload in fragments if kind == "delta")
    persisted = [
        json.loads(line)
        for line in (life / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    reply = next(event for event in persisted if event["type"] == "ui.argus")
    assert delta["message_id"] == reply["message_id"]
    assert delta["message_id"].startswith("web-")
    assert delta["message_id"].endswith("-argus")


def test_natural_language_abort_is_control_not_backlog_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path, "s-abort001")
    memory = LifeMemory.open(life)
    item = memory.backlog.add(
        BacklogItem.new(title="long task", objective="run a long task")
    )
    memory.backlog.mark_running(item.id)
    manager_bridge._STATES.clear()

    monkeypatch.setattr(
        "argus_skill.manager.config_intent._front_door_classify",
        lambda *a, **k: (None, "abort", "simple"),
    )
    monkeypatch.setattr(
        "argus_skill.manager.front_door.manager_triage",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("abort control must not enter Manager reply work")
        ),
    )
    monkeypatch.setattr(
        "argus_skill.manager.dispatch.enqueue_mission",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("abort control must never enqueue a mission")
        ),
    )

    result = manager_bridge.manager_message(
        "s-abort001",
        "停止现在的所有任务",
        global_root=tmp_path,
    )

    assert result["kind"] == "control"
    assert result["control"] == "abort"
    assert result["requested"] is True
    assert result["item_id"] == item.id
    assert len(memory.backlog.all()) == 1
    request = json.loads((life / "running_item_abort.json").read_text())
    assert "停止现在的所有任务" in request["reason"]


def test_web_process_restart_seeds_one_startup_handoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path, "s-restart01")
    from argus_skill.core.transcript import append_turn

    append_turn(life, "operator", "old question")
    append_turn(life, "argus", "old answer")
    manager_bridge._STATES.clear()  # model a fresh web process
    seen: list[str] = []
    classified: list[str] = []

    def _classify(mem, text, chat_state, *, root_task_id=None):
        classified.append(text)
        return None, "simple"

    monkeypatch.setattr("argus_skill.manager.config_intent._front_door_classify", _classify)

    def _triage(mem, body, chat_state, **_kw):
        seen.append(body)
        chat_state["last_thread_id"] = "warm-thread"
        return "ok"

    monkeypatch.setattr("argus_skill.manager.front_door.manager_triage", _triage)

    manager_bridge.manager_message(
        "s-restart01",
        "new question",
        global_root=tmp_path,
    )
    assert "SESSION HANDOFF" in seen[0]
    assert "old question" in seen[0] and "old answer" in seen[0]
    assert seen[0].count("new question") == 1
    assert classified == ["new question"]
    assert manager_bridge._STATES["s-restart01"]["startup_handoffs"] == 1

    manager_bridge.manager_message(
        "s-restart01",
        "next question",
        global_root=tmp_path,
    )
    assert "SESSION HANDOFF" not in seen[1]
    assert classified == ["new question", "next question"]


def test_natural_language_config_change_is_applied_inline(tmp_path: Path, monkeypatch) -> None:
    """The cockpit front-door is a full NL control surface: a hyperparameter
    request ("set the engineer to xhigh") is applied + confirmed inline, never
    enqueued as a mission and never reaching triage. Now driven by the MERGED
    front-door classify (``_front_door_classify`` → intent + route) + explicit
    ``_apply_config_intent``."""
    _make_project(tmp_path, "s-cfg00001")
    manager_bridge._STATES.clear()

    triaged: list[str] = []

    def _fake_front_door(mem, text, chat_state, *, root_task_id=None):
        # config request → non-None intent (apply path); else None + route.
        return (object() if "xhigh" in text else None), "simple"

    def _fake_apply(mem, intent, chat_state, *, on_confirm=None):
        if on_confirm:
            on_confirm("Set Engineer reasoning effort to xhigh.")
        return True

    def _fake_triage(mem, body, chat_state, **_kw):
        triaged.append(body)
        return "chatted"

    monkeypatch.setattr("argus_skill.manager.config_intent._front_door_classify", _fake_front_door)
    monkeypatch.setattr("argus_skill.manager.config_intent._apply_config_intent", _fake_apply)
    monkeypatch.setattr("argus_skill.manager.front_door.manager_triage", _fake_triage)

    r = manager_bridge.manager_message(
        "s-cfg00001", "set the engineer to xhigh", global_root=tmp_path
    )
    assert r["kind"] == "chat"
    assert "xhigh" in r["reply"]
    assert triaged == []  # config short-circuits BEFORE triage — not enqueued

    # A non-config message flows on to triage as normal (with the precomputed route).
    r2 = manager_bridge.manager_message("s-cfg00001", "how's it going?", global_root=tmp_path)
    assert r2["kind"] == "chat"
    assert triaged == ["how's it going?"]


def test_active_mission_config_change_is_still_applied_inline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path, "s-cfg-active")
    memory = LifeMemory.open(life)
    item = memory.backlog.add(
        BacklogItem.new(title="active", objective="work")
    )
    memory.backlog.mark_running(item.id)
    manager_bridge._STATES.clear()
    applied: list[object] = []

    intent = object()
    monkeypatch.setattr(
        "argus_skill.manager.config_intent._front_door_classify",
        lambda *a, **k: (intent, None, "complex"),
    )

    def _apply(mem, candidate, chat_state, *, on_confirm=None):
        applied.append(candidate)
        if on_confirm:
            on_confirm("Set budget to $20.")
        return True

    monkeypatch.setattr(
        "argus_skill.manager.config_intent._apply_config_intent",
        _apply,
    )
    monkeypatch.setattr(
        "argus_skill.manager.front_door.manager_triage",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("config intent must short-circuit triage")
        ),
    )

    result = manager_bridge.manager_message(
        "s-cfg-active",
        "set the budget to 20",
        global_root=tmp_path,
    )

    assert result["kind"] == "chat"
    assert applied == [intent]
    assert len(memory.backlog.all()) == 1
