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
