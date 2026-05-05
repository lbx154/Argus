"""Tests for ``argus_skill.daemon.mission_runtime.MissionDaemon``.

These tests don't spin up a real ``LoopEngine`` — that's covered by the
fake-runner integration test in ``test_mission_engine_integration.py``.
The goal here is to verify the command-routing surface: every operator
command kind dispatches to the right ``LoopStateStore`` method.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.ports import ControlCommand
from argus_skill.daemon.mission_runtime import (
    MissionConfig,
    MissionDaemon,
    MissionDaemonConfig,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeLoopStateStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.injects: list[str] = []
        self.review_criteria: list[str] = []
        self.plan_directions: list[str] = []
        self.plan_modes: list[str] = []
        self.stop_calls = 0
        self.plan_mode: str | None = None

    def request_inject(self, text, source="operator"):
        self.calls.append(("inject", text, source))
        self.injects.append(text)

    def request_stop(self, source="operator"):
        self.calls.append(("stop", source))
        self.stop_calls += 1

    def request_review_criteria(self, text, source="operator"):
        self.calls.append(("review", text, source))
        self.review_criteria.append(text)

    def request_plan_direction(self, text, source="operator"):
        self.calls.append(("plan", text, source))
        self.plan_directions.append(text)

    def request_plan_mode(self, mode, source="operator"):
        self.calls.append(("mode", mode, source))
        self.plan_modes.append(mode)
        self.plan_mode = mode
        return mode

    def current_plan_mode(self) -> str | None:
        return self.plan_mode


class FakeSink:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.verbose: bool | None = None

    def handle_event(self, event):
        self.events.append(event)

    def handle_stream_line(self, stream, line):
        pass

    def close(self):
        pass

    def set_verbose(self, verbose):
        self.verbose = verbose


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def _write_mission(state_dir: Path, *, plan_mode="auto") -> Path:
    payload = {
        "mission_id": "mission_TESTID",
        "objective": "do the test thing",
        "workdir": str(state_dir),
        "check_commands": [],
        "max_rounds": 5,
        "plan_mode": plan_mode,
        "main_model": "gpt-x",
        "reviewer_model": "gpt-x",
        "plan_model": "gpt-x",
        "main_reasoning_effort": "medium",
        "reviewer_reasoning_effort": "medium",
        "plan_reasoning_effort": "high",
        "started_at": 0,
        "started_at_iso": "1970-01-01T00:00:00+00:00",
    }
    mfile = state_dir / "mission.json"
    mfile.write_text(json.dumps(payload))
    return mfile


def _build_daemon(tmp_path, *, plan_mode="auto") -> tuple[MissionDaemon, FakeSink, FakeLoopStateStore]:
    state = tmp_path / "state"
    state.mkdir()
    _write_mission(state, plan_mode=plan_mode)
    mission = MissionConfig.from_json_file(state / "mission.json")

    # We need to build a MissionDaemon WITHOUT actually constructing an
    # ArgusBot LoopEngine (which would try to spawn codex). The cleanest
    # path: build the object via __new__, attach the minimum attrs the
    # handle_command surface needs (state_store + sinks + mission +
    # _stop_event + _lock), and exercise handle_command directly.
    sinks = FakeSink()
    fake_store = FakeLoopStateStore()

    daemon = MissionDaemon.__new__(MissionDaemon)
    daemon.mission = mission
    daemon.sinks = sinks
    daemon.state_store = fake_store
    daemon.config = MissionDaemonConfig(state_dir=str(state))
    daemon._lock = __import__("threading").Lock()
    daemon._stop_event = __import__("threading").Event()
    daemon._mission_status = "running"
    daemon._mission_result = None
    daemon._started_at = "1970-01-01T00:00:00+00:00"
    # Rich runtime state added in Phase B; fixture bypasses __init__ so set defaults.
    daemon._current_phase = "ready"
    daemon._current_round = 0
    daemon._max_rounds = mission.max_rounds
    daemon._last_review = None
    daemon._last_plan = None
    daemon._last_main_summary = ""
    from collections import deque
    daemon._recent_events = deque(maxlen=12)
    return daemon, sinks, fake_store


# ---------------------------------------------------------------------------
# Tests — operator commands routed to LoopStateStore
# ---------------------------------------------------------------------------

def test_inject_routes_to_request_inject(tmp_path):
    daemon, sinks, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="inject", text="add error handling"))
    assert store.injects == ["add error handling"]
    # An ack event should be emitted
    assert any(e.get("type") == "command.ack" for e in sinks.events)


def test_run_in_mission_mode_is_inject(tmp_path):
    """In mission mode, /run during a running mission is treated as an inject."""
    daemon, _, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="run", text="also do X"))
    assert store.injects == ["also do X"]


def test_inject_empty_text_emits_error(tmp_path):
    daemon, sinks, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="inject", text="   "))
    assert store.injects == []
    assert any(e.get("type") == "command.error" for e in sinks.events)


def test_skip_routes_to_inject_with_skip_text(tmp_path):
    daemon, _, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="skip", text=""))
    assert len(store.injects) == 1
    assert "/skip" in store.injects[0]
    assert "abandon" in store.injects[0].lower()


def test_stop_routes_to_request_stop(tmp_path):
    daemon, _, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="stop", text=""))
    assert store.stop_calls == 1
    assert daemon._stop_event.is_set()


def test_review_routes_to_request_review_criteria(tmp_path):
    daemon, sinks, store = _build_daemon(tmp_path)
    daemon.handle_command(
        ControlCommand(kind="review", text="must include unit tests")
    )
    assert store.review_criteria == ["must include unit tests"]
    assert any(e.get("type") == "command.ack" for e in sinks.events)


def test_review_empty_text_emits_error(tmp_path):
    daemon, sinks, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="review", text=""))
    assert store.review_criteria == []
    assert any(e.get("type") == "command.error" for e in sinks.events)


def test_plan_routes_to_request_plan_direction(tmp_path):
    daemon, sinks, store = _build_daemon(tmp_path)
    daemon.handle_command(
        ControlCommand(kind="plan", text="focus on parser robustness")
    )
    assert store.plan_directions == ["focus on parser robustness"]
    assert any(e.get("type") == "command.ack" for e in sinks.events)


def test_plan_empty_text_emits_error(tmp_path):
    daemon, sinks, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="plan", text=""))
    assert store.plan_directions == []
    assert any(e.get("type") == "command.error" for e in sinks.events)


def test_mode_auto_routes_to_request_plan_mode(tmp_path):
    daemon, _, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="mode", text="auto"))
    assert store.plan_modes == ["auto"]


def test_mode_off_routes_to_request_plan_mode(tmp_path):
    daemon, _, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="mode", text="off"))
    assert store.plan_modes == ["off"]


def test_mode_record_routes_to_request_plan_mode(tmp_path):
    daemon, _, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="mode", text="record"))
    assert store.plan_modes == ["record"]


def test_mode_invalid_emits_error(tmp_path):
    daemon, sinks, store = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="mode", text="yolo"))
    assert store.plan_modes == []
    assert any(e.get("type") == "command.error" for e in sinks.events)


def test_status_emits_status_report(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="status", text=""))
    matches = [e for e in sinks.events if e.get("type") == "status.report"]
    assert matches
    assert "mission" in matches[0]["text"]
    assert daemon.mission.mission_id in matches[0]["text"]


def test_verbose_quiet_forwarded_to_sinks(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="verbose", text=""))
    assert sinks.verbose is True
    daemon.handle_command(ControlCommand(kind="quiet", text=""))
    assert sinks.verbose is False


def test_help_emits_mission_aware_help(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="help", text=""))
    matches = [e for e in sinks.events if e.get("type") == "help"]
    assert matches
    text = matches[0]["text"]
    assert "/review" in text
    assert "/plan" in text
    assert "/mode" in text


def test_unknown_command_emits_command_unknown(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="sing-a-song", text=""))
    assert any(e.get("type") == "command.unknown" for e in sinks.events)


# ---------------------------------------------------------------------------
# MissionConfig serde
# ---------------------------------------------------------------------------

def test_mission_config_round_trip(tmp_path):
    p = tmp_path / "mission.json"
    p.write_text(
        json.dumps(
            {
                "mission_id": "mission_X",
                "objective": "do thing",
                "workdir": "/tmp/wd",
                "check_commands": ["pytest -q"],
                "max_rounds": 25,
                "plan_mode": "off",
                "main_model": "m1",
                "reviewer_model": "m2",
                "plan_model": "m3",
                "main_reasoning_effort": "low",
                "reviewer_reasoning_effort": "high",
                "plan_reasoning_effort": "medium",
            }
        )
    )
    cfg = MissionConfig.from_json_file(p)
    assert cfg.mission_id == "mission_X"
    assert cfg.objective == "do thing"
    assert cfg.workdir == "/tmp/wd"
    assert cfg.check_commands == ["pytest -q"]
    assert cfg.max_rounds == 25
    assert cfg.plan_mode == "off"
    assert cfg.main_model == "m1"


def test_mission_config_defaults_when_fields_missing(tmp_path):
    p = tmp_path / "mission.json"
    p.write_text(json.dumps({"mission_id": "m1", "objective": "obj"}))
    cfg = MissionConfig.from_json_file(p)
    assert cfg.max_rounds == 50
    assert cfg.plan_mode == "off"
    assert cfg.main_model == "gpt-5.4-mini"
    assert cfg.check_commands == []
    # Default for the new safety knob: OFF.
    assert cfg.auto_follow_up is False


def test_mission_config_auto_follow_up_round_trip(tmp_path):
    """Explicit ``auto_follow_up=true`` is preserved through JSON load."""
    p = tmp_path / "mission.json"
    p.write_text(
        json.dumps(
            {
                "mission_id": "m1",
                "objective": "obj",
                "plan_mode": "auto",
                "auto_follow_up": True,
            }
        )
    )
    cfg = MissionConfig.from_json_file(p)
    assert cfg.auto_follow_up is True


def test_mission_config_auto_follow_up_defaults_off_for_legacy_auto_mode(tmp_path, caplog):
    """Pre-existing mission.json with plan_mode=auto but no auto_follow_up
    field: load to False (the safe default) and emit a one-time warning so
    operators notice the breaking change.
    """
    import logging
    p = tmp_path / "mission.json"
    p.write_text(
        json.dumps(
            {
                "mission_id": "m1",
                "objective": "obj",
                "plan_mode": "auto",
            }
        )
    )
    with caplog.at_level(logging.WARNING, logger="argus_skill.daemon.mission_runtime"):
        cfg = MissionConfig.from_json_file(p)
    assert cfg.auto_follow_up is False
    assert any(
        "auto_follow_up" in rec.message and "safe default" in rec.message
        for rec in caplog.records
    )


def test_mission_config_auto_follow_up_silent_for_non_auto_mode(tmp_path, caplog):
    """plan_mode != 'auto' → no warning needed; field defaults to False quietly."""
    import logging
    p = tmp_path / "mission.json"
    p.write_text(
        json.dumps({"mission_id": "m1", "objective": "obj", "plan_mode": "off"})
    )
    with caplog.at_level(logging.WARNING, logger="argus_skill.daemon.mission_runtime"):
        cfg = MissionConfig.from_json_file(p)
    assert cfg.auto_follow_up is False
    assert not any("auto_follow_up" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Phase B: state tracker + rich /status rendering
# ---------------------------------------------------------------------------

def test_track_event_updates_round_and_phase(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    daemon._track_event({"type": "loop.started", "max_rounds": 7, "objective": "x"})
    assert daemon._max_rounds == 7
    daemon._track_event({"type": "round.started", "round_index": 2})
    assert daemon._current_round == 2
    assert daemon._current_phase == "engineering"
    daemon._track_event({"type": "round.main.completed", "round_index": 2,
                         "last_message": "wrote files; pytest 6 passed"})
    assert daemon._current_phase == "checks"
    assert daemon._last_main_summary == "wrote files; pytest 6 passed"
    daemon._track_event({"type": "round.checks.completed", "round_index": 2, "checks": []})
    assert daemon._current_phase == "review"
    daemon._track_event({"type": "round.review.completed", "round_index": 2,
                         "status": "continue", "reason": "test X failing",
                         "next_action": "fix X"})
    assert daemon._last_review == {
        "round": 2, "status": "continue",
        "reason": "test X failing", "next_action": "fix X",
    }
    assert daemon._current_phase == "engineering"  # not done → next round
    daemon._track_event({"type": "plan.completed", "round_index": 3,
                         "plan_mode": "auto", "follow_up_required": True,
                         "main_instruction": "fix X", "next_explore": "verify",
                         "review_instruction": ""})
    assert daemon._last_plan["main_instruction"] == "fix X"
    assert daemon._current_phase == "engineering"


def test_track_event_done_review_moves_to_planning(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    daemon._track_event({"type": "round.review.completed", "round_index": 1,
                         "status": "done", "reason": "objective met", "next_action": ""})
    assert daemon._current_phase == "planning"
    assert daemon._last_review["status"] == "done"


def test_recent_events_are_sanitized_and_capped(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    # Push 20 events; ring buffer caps at 12.
    for i in range(20):
        daemon._track_event({"type": "round.started", "round_index": i,
                             "ts": f"2026-05-04T18:{i:02d}:00Z"})
    assert len(daemon._recent_events) == 12
    # Each entry is sanitized: only ts/type/round_index/short keys.
    sample = daemon._recent_events[0]
    assert set(sample.keys()) == {"ts", "type", "round_index", "short"}
    # `short` is a single line (the rich renderer's first line).
    assert "\n" not in sample["short"]
    assert "round" in sample["short"]


def test_recent_events_skips_status_report_echoes(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    daemon._track_event({"type": "round.started", "round_index": 1})
    daemon._track_event({"type": "status.report", "text": "..."})
    daemon._track_event({"type": "help", "text": "..."})
    assert len(daemon._recent_events) == 1
    assert daemon._recent_events[0]["type"] == "round.started"


def test_effective_plan_mode_reads_from_state_store(tmp_path):
    daemon, _, store = _build_daemon(tmp_path, plan_mode="off")
    # Simulate /mode auto via state_store (the FakeLoopStateStore must support it).
    store.plan_mode = "auto"
    # _effective_plan_mode should reflect the runtime override, not the
    # mission.json value.
    assert daemon._effective_plan_mode() == "auto"


def test_render_mission_status_includes_round_phase_review_plan_recent(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    daemon._max_rounds = 10
    daemon._current_round = 3
    daemon._current_phase = "review"
    daemon._last_review = {"round": 2, "status": "continue",
                           "reason": "tests failing", "next_action": "fix X"}
    daemon._last_plan = {"round": 3, "main_instruction": "modify rm exit code",
                         "plan_mode": "auto", "follow_up_required": True,
                         "next_explore": "", "review_instruction": ""}
    daemon._last_main_summary = "wrote rm fix; pytest still red on edge case"
    from collections import deque
    daemon._recent_events = deque([
        {"ts": "2026-05-04T18:42:08Z", "type": "round.started",
         "round_index": 1, "short": "🔁 round 1 starting…"},
        {"ts": "2026-05-04T18:43:35Z", "type": "round.review.completed",
         "round_index": 1, "short": "🧑‍⚖️ round 1: review ↻ continue"},
    ], maxlen=12)
    out = daemon._render_status_short()
    assert "round 3/10" in out
    assert "phase=review" in out
    assert "↻ continue" in out
    assert "tests failing" in out
    assert "modify rm exit code" in out
    assert "wrote rm fix" in out
    assert "18:42:08" in out
    assert "🔁 round 1" in out


def test_render_mission_status_no_review_yet(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    out = daemon._render_status_short()
    assert "(none yet)" in out


def test_write_status_payload_contains_phase_and_history(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    daemon._track_event({"type": "loop.started", "max_rounds": 5, "objective": "x"})
    daemon._track_event({"type": "round.started", "round_index": 1})
    daemon._track_event({"type": "round.review.completed", "round_index": 1,
                         "status": "continue", "reason": "still red",
                         "next_action": "fix"})
    daemon._write_status()
    import json as _json
    payload = _json.loads((tmp_path / "state" / "status.json").read_text())
    assert payload["mode"] == "mission"
    assert payload["current_phase"] == "engineering"
    assert payload["current_round"] == 1
    assert payload["max_rounds"] == 5
    assert payload["last_review"]["status"] == "continue"
    assert payload["last_review"]["reason"] == "still red"
    assert isinstance(payload["recent_events"], list)
    assert len(payload["recent_events"]) >= 2


def test_write_status_includes_auto_follow_up_field(tmp_path):
    """status.json must surface auto_follow_up so chat_app's banner can show it."""
    daemon, _, _ = _build_daemon(tmp_path)
    # Default mission.json fixture omits auto_follow_up → False.
    daemon._write_status()
    import json as _json
    payload = _json.loads((tmp_path / "state" / "status.json").read_text())
    assert payload["auto_follow_up"] is False
    # Flip it and confirm the new value flows through.
    daemon.mission.auto_follow_up = True
    daemon._write_status()
    payload2 = _json.loads((tmp_path / "state" / "status.json").read_text())
    assert payload2["auto_follow_up"] is True


def test_setup_loop_passes_auto_follow_up_to_loop_config(tmp_path, monkeypatch):
    """``MissionDaemon.__init__`` must wire ``mission.auto_follow_up`` to
    ``LoopConfig.allow_follow_up_phase`` — that is the engine's gate that
    decides whether reviewer ✅ done auto-launches round N+1.
    """
    from argus_skill.daemon import mission_runtime as mr

    captured: dict = {}

    class _StubLoopConfig:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

    class _StubStateStore:
        def __init__(self, **kwargs):
            self.plan_mode = kwargs.get("plan_mode")

        def current_plan_mode(self):
            return self.plan_mode

    class _StubReviewer:
        def __init__(self, *a, **kw):
            pass

    class _StubPlanner:
        def __init__(self, *a, **kw):
            pass

    class _StubEngine:
        def __init__(self, **kw):
            pass

    fake = {
        "CodexRunner": object,
        "LoopConfig": _StubLoopConfig,
        "LoopEngine": _StubEngine,
        "LoopStateStore": _StubStateStore,
        "Reviewer": _StubReviewer,
        "ReviewerConfig": object,
        "Planner": _StubPlanner,
        "PlannerConfig": object,
    }
    monkeypatch.setattr(mr, "_import_argusbot", lambda: fake)
    monkeypatch.setattr(mr, "SkillStore", lambda *a, **kw: object())
    monkeypatch.setattr(mr, "Distiller", lambda *a, **kw: object())
    monkeypatch.setattr(mr, "SkillLoopRunner", lambda *a, **kw: object())

    state = tmp_path / "state"
    state.mkdir()
    payload_dict = {
        "mission_id": "mission_X",
        "objective": "do thing",
        "workdir": str(state),
        "check_commands": [],
        "max_rounds": 3,
        "plan_mode": "auto",
        "auto_follow_up": False,
        "main_model": "m",
        "reviewer_model": "m",
        "plan_model": "m",
        "main_reasoning_effort": "medium",
        "reviewer_reasoning_effort": "medium",
        "plan_reasoning_effort": "high",
    }
    (state / "mission.json").write_text(json.dumps(payload_dict))
    mission = mr.MissionConfig.from_json_file(state / "mission.json")
    cfg = mr.MissionDaemonConfig(state_dir=str(state))

    # Default OFF.
    daemon = mr.MissionDaemon(
        mission=mission,
        sinks=FakeSink(),
        engineer_backend=object(),
        codex_runner=object(),
        config=cfg,
    )
    assert captured["allow_follow_up_phase"] is False
    assert daemon.mission.auto_follow_up is False

    # Flip to ON.
    payload_dict["auto_follow_up"] = True
    (state / "mission.json").write_text(json.dumps(payload_dict))
    mission_on = mr.MissionConfig.from_json_file(state / "mission.json")
    mr.MissionDaemon(
        mission=mission_on,
        sinks=FakeSink(),
        engineer_backend=object(),
        codex_runner=object(),
        config=cfg,
    )
    assert captured["allow_follow_up_phase"] is True


# ---------------------------------------------------------------------------
# Phase E: /show prompt|plan|review|all
# ---------------------------------------------------------------------------

def _loop_state_dir(tmp_path):
    """Return the directory where MissionDaemon expects loop_state artifacts."""
    return tmp_path / "state" / "missions" / "mission_TESTID" / "loop_state"


def test_show_no_target_emits_error(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="show", text=""))
    errs = [e for e in sinks.events if e.get("type") == "command.error"]
    assert errs and "/show requires" in errs[0]["text"]


def test_show_unknown_target_emits_error(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="show", text="diary"))
    errs = [e for e in sinks.events if e.get("type") == "command.error"]
    assert errs and "diary" in errs[0]["text"]


def test_show_prompt_when_missing_returns_helpful_text(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    daemon.handle_command(ControlCommand(kind="show", text="prompt"))
    acks = [e for e in sinks.events if e.get("type") == "command.ack"]
    assert acks
    assert acks[0].get("show_kind") == "prompt"
    assert "no prompts" in acks[0]["text"].lower() or "engineer hasn't run" in acks[0]["text"]


def test_show_prompt_returns_last_round_section(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    base = _loop_state_dir(tmp_path)
    base.mkdir(parents=True, exist_ok=True)
    (base / "main_prompts.md").write_text(
        "# round 1\nfirst prompt body…\n# round 2\nSECOND PROMPT BODY here\n",
        encoding="utf-8",
    )
    daemon.handle_command(ControlCommand(kind="show", text="prompt"))
    ack = next(e for e in sinks.events if e.get("type") == "command.ack")
    assert "SECOND PROMPT BODY" in ack["text"]
    # The first round's section should NOT bleed into the latest view.
    assert "first prompt body" not in ack["text"]


def test_show_plan_returns_plan_overview(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    base = _loop_state_dir(tmp_path)
    base.mkdir(parents=True, exist_ok=True)
    (base / "plan_overview.md").write_text(
        "## Plan\nmain_instruction: refine error handling\n",
        encoding="utf-8",
    )
    daemon.handle_command(ControlCommand(kind="show", text="plan"))
    ack = next(e for e in sinks.events if e.get("type") == "command.ack")
    assert "refine error handling" in ack["text"]


def test_show_review_returns_latest_summary(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    base = _loop_state_dir(tmp_path)
    rdir = base / "review_summaries"
    rdir.mkdir(parents=True, exist_ok=True)
    import os, time as _time
    (rdir / "round_001.md").write_text("OLD review", encoding="utf-8")
    _time.sleep(0.01)
    (rdir / "round_002.md").write_text("FRESH review verdict", encoding="utf-8")
    daemon.handle_command(ControlCommand(kind="show", text="review"))
    ack = next(e for e in sinks.events if e.get("type") == "command.ack")
    assert "FRESH review verdict" in ack["text"]
    assert "OLD review" not in ack["text"]


def test_show_all_combines_all_three(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    base = _loop_state_dir(tmp_path)
    base.mkdir(parents=True, exist_ok=True)
    (base / "main_prompts.md").write_text("# round 1\nMP body\n", encoding="utf-8")
    (base / "plan_overview.md").write_text("PLAN body\n", encoding="utf-8")
    rdir = base / "review_summaries"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "round_001.md").write_text("REVIEW body\n", encoding="utf-8")
    daemon.handle_command(ControlCommand(kind="show", text="all"))
    ack = next(e for e in sinks.events if e.get("type") == "command.ack")
    assert "MP body" in ack["text"]
    assert "PLAN body" in ack["text"]
    assert "REVIEW body" in ack["text"]
    assert ack.get("show_kind") == "all"


# ---------------------------------------------------------------------------
# Phase E (parser): /show kind
# ---------------------------------------------------------------------------

def test_parser_show_with_target():
    from argus_skill.telegram.poller import parse_command_text
    cmd = parse_command_text(text="/show prompt", plain_text_as_inject=False)
    assert cmd is not None
    assert cmd.kind == "show"
    assert cmd.text == "prompt"


def test_parser_show_bare_emits_show_with_empty_text():
    from argus_skill.telegram.poller import parse_command_text
    cmd = parse_command_text(text="/show", plain_text_as_inject=False)
    assert cmd is not None
    assert cmd.kind == "show"
    assert cmd.text == ""


# ---------------------------------------------------------------------------
# Post-completion behaviour: daemon idles, inspection still works,
# mutations emit friendly no-op acks.
# ---------------------------------------------------------------------------

def test_mission_done_state_keeps_daemon_alive(tmp_path):
    """After _mission_status flips to done, /status must still respond."""
    daemon, sinks, _ = _build_daemon(tmp_path)
    # Simulate mission completion (don't actually run a worker thread).
    daemon._mission_status = "done"
    daemon._mission_result = {"success": True, "stop_reason": "ok",
                              "session_id": "x", "rounds": 2}
    daemon._current_phase = "idle"
    # _stop_event is NOT set — the daemon is idling.
    assert not daemon._stop_event.is_set()

    # /status, /show, /help, /verbose, /quiet, /stop must all work.
    daemon.handle_command(ControlCommand(kind="status", text=""))
    daemon.handle_command(ControlCommand(kind="help", text=""))
    daemon.handle_command(ControlCommand(kind="verbose", text=""))
    daemon.handle_command(ControlCommand(kind="quiet", text=""))
    types = [e.get("type") for e in sinks.events]
    assert "status.report" in types
    assert "help" in types
    assert "command.ack" in types  # verbose/quiet


def test_mutating_command_post_completion_returns_friendly_noop(tmp_path):
    """/inject /skip /review /plan /mode after mission done → ack with explanation."""
    daemon, sinks, store = _build_daemon(tmp_path)
    daemon._mission_status = "done"
    daemon._current_phase = "idle"

    for cmd in [
        ControlCommand(kind="inject", text="late nudge"),
        ControlCommand(kind="skip", text=""),
        ControlCommand(kind="review", text="be strict"),
        ControlCommand(kind="plan", text="redirect"),
        ControlCommand(kind="mode", text="auto"),
        ControlCommand(kind="run", text="late task"),
    ]:
        sinks.events.clear()
        daemon.handle_command(cmd)
        acks = [e for e in sinks.events if e.get("type") == "command.ack"]
        assert acks, f"/{cmd.kind} produced no ack post-completion"
        text = acks[0]["text"]
        assert "no-op" in text or "already finished" in text, \
            f"/{cmd.kind} ack should explain the no-op: {text!r}"

    # And NONE of these mutations should have hit the state_store.
    assert store.injects == []
    assert store.review_criteria == []
    assert store.plan_directions == []
    assert store.plan_modes == []


def test_show_post_completion_still_reads_artifacts(tmp_path):
    daemon, sinks, _ = _build_daemon(tmp_path)
    daemon._mission_status = "done"
    base = _loop_state_dir(tmp_path)
    base.mkdir(parents=True, exist_ok=True)
    (base / "plan_overview.md").write_text("LATE PLAN", encoding="utf-8")
    daemon.handle_command(ControlCommand(kind="show", text="plan"))
    acks = [e for e in sinks.events if e.get("type") == "command.ack"]
    assert any("LATE PLAN" in (e.get("text") or "") for e in acks)


def test_stop_post_completion_sets_stop_event(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    daemon._mission_status = "done"
    assert not daemon._stop_event.is_set()
    daemon.handle_command(ControlCommand(kind="stop", text=""))
    # stop() sets the event so daemon.wait() can return + process exits cleanly.
    assert daemon._stop_event.is_set()


def test_mission_finished_helper_recognizes_done_and_error(tmp_path):
    daemon, _, _ = _build_daemon(tmp_path)
    assert not daemon._mission_finished()
    daemon._mission_status = "done"
    assert daemon._mission_finished()
    daemon._mission_status = "error"
    assert daemon._mission_finished()
    daemon._mission_status = "running"
    assert not daemon._mission_finished()
