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
        return mode


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
