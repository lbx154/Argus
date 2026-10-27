"""Tests for role configuration and event-derived live activity.

Deterministic: every test passes an explicit ``env`` and writes a synthetic
``events.jsonl``, so nothing depends on the real vault, real env, or wall clock.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.core.role_config import (
    resolve_role_config,
)
from argus.life.role_activity import role_activity


@pytest.fixture(autouse=True)
def _isolated_argus_skill_home(tmp_path, monkeypatch):
    """resolve_role_config's backend/model/effort resolution now also falls
    back to core.knob_store's persisted config.json (~/.argus-skill/config.json
    by default) when a test passes an env={} with nothing set for a given
    knob — isolate ARGUS_SKILL_HOME so these "falls back to the hard-coded
    default" tests never read (or race against) a REAL operator's persisted
    switches on this machine."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-skill-home"))


@pytest.fixture(autouse=True)
def _hermetic_capability_vault(monkeypatch, tmp_path):
    """Honor this module's "nothing depends on the real vault" contract even on a
    box that HAS a ``~/.argus-skill/capabilities/model_api.json``.

    Model resolution derives the vault path from the passed ``env``, and an
    ``env={}`` falls back to ``~/.argus-skill`` (NOT the ARGUS_SKILL_HOME
    isolated above) — so a developer whose local vault routes to (say)
    ``claude-sonnet-5`` would see the default-model assertions fail locally while
    they pass on CI (which has no such file). Pointing the vault at a nonexistent
    path makes every test read the code default (``gpt-5.5``) deterministically,
    on any box.
    """
    from argus.tools import capability_vault

    monkeypatch.setattr(
        capability_vault,
        "default_vault_path",
        lambda env=None: tmp_path / "no-such-vault.json",
    )
    monkeypatch.setattr(
        capability_vault,
        "default_codex_config_path",
        lambda env=None: tmp_path / "no-such-codex-config.toml",
    )


# ── backend resolution + fallback chain ───────────────────────────────────


def test_every_role_runs_on_the_shared_backend():
    env = {
        "ARGUS_SKILL_LIFE_BACKEND": "codex",
        "ARGUS_SKILL_RUNNER_BACKEND": "claude",
        "ARGUS_SKILL_REVIEWER_BACKEND": "copilot",  # retired, ignored
    }
    for role in ("manager", "planner", "engineer", "reviewer"):
        assert resolve_role_config(role, env=env).backend_label == "Claude Code"


def test_memory_backend_preserved():
    env = {"ARGUS_SKILL_LIFE_BACKEND": "memory"}
    assert resolve_role_config("engineer", env=env).backend == "memory"


# ── model resolution ──────────────────────────────────────────────────────


def test_planner_reads_plan_model_env():
    env = {"ARGUS_SKILL_PLAN_MODEL": "o3"}
    assert resolve_role_config("planner", env=env).model == "o3"


# ── reasoning effort ──────────────────────────────────────────────────────


# ── live activity from events.jsonl ────────────────────────────────────────

def _write_events(life_dir, events):
    (life_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


def test_activity_canonicalizes_historical_event_aliases(tmp_path):
    now = time.time()
    _write_events(
        tmp_path,
        [{"type": "round.started", "round_index": 2, "ts": now - 1}],
    )

    engineer = role_activity(tmp_path, now=now)["engineer"]
    assert engineer.active is True
    assert engineer.label == "round 2"


def test_long_inflight_venue_call_does_not_decay_to_waiting(tmp_path):
    now = time.time()
    _write_events(
        tmp_path,
        [
            {
                "type": "venue.research.started",
                "text": "researching ICLR",
                "ts": now - 220,
            },
            {
                "type": "agent.io.start",
                "call_id": "venue-call",
                "run_label": "venue-research",
                "ts": now - 219,
            },
            {
                "type": "engineer.progress",
                "kind": "tool_use",
                "agent_layer": "engineer",
                "text": "web_fetch",
                "ts": now - 200,
            },
        ],
    )

    engineer = role_activity(tmp_path, now=now)["engineer"]

    assert engineer.active is True
    assert engineer.status == "running"
    assert "web_fetch" in engineer.label


def test_completed_venue_call_is_not_active(tmp_path):
    now = time.time()
    _write_events(
        tmp_path,
        [
            {
                "type": "agent.io.start",
                "call_id": "venue-call",
                "run_label": "venue-research",
                "ts": now - 220,
            },
            {
                "type": "agent.io.complete",
                "call_id": "venue-call",
                "run_label": "venue-research",
                "exit_code": 0,
                "ts": now - 1,
            },
        ],
    )

    engineer = role_activity(tmp_path, now=now)["engineer"]

    assert engineer.active is False
    assert engineer.status == "done"
    assert engineer.label == "researching target venue done"


def test_review_deferral_is_engineer_activity(tmp_path):
    now = time.time()
    _write_events(tmp_path, [{
        "type": "round.review.deferred",
        "next_step": "wire the parser into the runner",
        "ts": now - 1,
    }])

    acts = role_activity(tmp_path, now=now)
    assert acts["engineer"].active is True
    assert acts["engineer"].label == "continuing before review"
    assert acts["reviewer"].active is False


def test_old_wait_events_are_ignored(tmp_path):
    """Rounds no longer write ``round.external_work_wait.*``; old journals still hold them."""
    now = time.time()
    work = {"work_id": "team:research-idea-pipeline-v8-g2", "item_id": "mission-1"}
    _write_events(tmp_path, [
        {"type": "loop.start", "ts": now - 900, **work},
        {"type": "round.external_work_wait.started", "ts": now - 5, **work},
        {"type": "round.external_work_wait.completed", "reason": "cadence_elapsed", "ts": now - 1, **work},
    ])

    engineer = role_activity(tmp_path, now=now)["engineer"]

    assert engineer.active is False
    assert engineer.label == "idle"


def test_activity_reads_only_the_event_log_tail(tmp_path, monkeypatch):
    events = tmp_path / "events.jsonl"
    events.write_text(
        ("x" * (2 * 1024 * 1024))
        + "\n"
        + json.dumps({
            "type": "engineer.progress",
            "text": "tail event",
            "ts": time.time(),
        })
        + "\n",
        encoding="utf-8",
    )
    original_read_text = Path.read_text

    def reject_full_event_read(path, *args, **kwargs):
        if path == events:
            raise AssertionError("role activity must not read the whole event log")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_full_event_read)
    assert role_activity(tmp_path)["engineer"].label == "thinking · tail event"


def test_activity_inactive_stale_role_decays_to_idle(tmp_path):
    # LIVE bug: an inactive role whose last event is ~2.7h old must decay to a
    # clean "idle" — not freeze its last (possibly verbose) label until it
    # scrolls out of the 200-line tail. Manager/Engineer were stuck on stale
    # content while Planner/Reviewer (no recent events) correctly read "idle".
    now = time.time()
    _write_events(tmp_path, [
        {"type": "life.manager.decision", "action": "hold",
         "reason": "The operator's only message was a greeting ('你好'), which "
                   "the engineer should not be interrupted for.",
         "ts": now - 9966},
        {"type": "loop.done", "status": "done", "ts": now - 9966},
    ])
    acts = role_activity(tmp_path, now=now)
    assert acts["manager"].active is False and acts["manager"].label == "idle"
    assert acts["engineer"].active is False and acts["engineer"].label == "idle"
    # age_s stays recorded (the panel de-emphasizes it, it is not zeroed)
    assert acts["manager"].age_s is not None and acts["manager"].age_s > 9000


def test_manager_stage_decision_is_terminal_not_active(tmp_path):
    now = time.time()
    _write_events(tmp_path, [{
        "type": "life.manager.stage_decision",
        "action": "advance",
        "current_stage": "inspect",
        "target_stage": "implement_cli",
        "ts": now - 3,
    }])

    manager = role_activity(tmp_path, now=now)["manager"]
    assert manager.label == "advance"
    assert manager.status == "done"
    assert manager.active is False


def test_activity_recognizes_concurrent_agent_io_without_leaking_stream_text(tmp_path):
    now = time.time()
    _write_events(tmp_path, [
        {"type": "agent.io.stream", "run_label": "engineer-r4",
         "line": "SECRET INTERNAL PAYLOAD", "ts": now - 2},
        {"type": "agent.io.stream", "run_label": "simple-1",
         "line": "[SESSION HANDOFF SECRET]", "ts": now - 1},
    ])
    acts = role_activity(tmp_path, now=now)
    assert acts["engineer"].active is True
    assert acts["engineer"].label == "round 4"
    assert acts["manager"].active is True
    assert acts["manager"].label == "handling your message"
    assert "SECRET" not in acts["engineer"].label + acts["manager"].label


def test_activity_labels_progress_by_agent_layer(tmp_path):
    now = time.time()
    _write_events(tmp_path, [
        {"type": "engineer.progress", "kind": "tool_call",
         "agent_layer": "planner", "text": "/bin/bash -lc 'pytest -q'",
         "ts": now - 3},
        {"type": "engineer.progress", "kind": "tool_call",
         "agent_layer": "engineer", "text": "/bin/bash -lc 'pytest -q'",
         "ts": now - 2},
    ])

    acts = role_activity(tmp_path, now=now)
    # Same event type, same text: only the layer differs. A planning turn must
    # not be described as an Engineer shell step.
    assert acts["planner"].label == "planning"
    assert acts["engineer"].label == "run · pytest -q"


def test_activity_names_calls_by_their_kind_without_a_summary(tmp_path):
    now = time.time()
    _write_events(tmp_path, [
        {"type": "engineer.progress", "kind": "command_execution", "agent_layer": "engineer",
         "text": "set -o pipefail && pytest -q", "ts": now - 2},
    ])
    assert role_activity(tmp_path, now=now)["engineer"].label == "run · set -o pipefail && pytest -q"
    _write_events(tmp_path, [
        {"type": "engineer.progress", "kind": "tool_use", "agent_layer": "engineer",
         "text": 'view: {"path": "/repo/core.py"}', "ts": now - 1},
    ])
    assert role_activity(tmp_path, now=now)["engineer"].label == "using view"


def test_activity_keeps_action_summary_for_non_engineer_layers(tmp_path):
    now = time.time()
    _write_events(tmp_path, [{
        "type": "engineer.progress", "kind": "reasoning",
        "agent_layer": "planner", "action_summary": "ranking three candidate routes",
        "ts": now - 1,
    }])

    assert role_activity(tmp_path, now=now)["planner"].label == (
        "ranking three candidate routes"
    )


@pytest.mark.parametrize(
    "event_type",
    ["life.planner.idle", "life.planner.waiting", "life.team.waiting"],
)
def test_planner_idle_event_projects_idle_label(tmp_path, event_type):
    now = time.time()
    _write_events(tmp_path, [{
        "type": event_type,
        "reason": "await remote job completion",
        "ts": now - 1,
    }])

    planner = role_activity(tmp_path, now=now)["planner"]
    assert planner.label == "nothing new to start"
    assert planner.status == "idle"
    assert planner.active is False


@pytest.mark.parametrize(("event_type", "status"), [
    ("round.review.completed", "manager"),
    ("round.review.completed", "operator"),
    ("round.review.completed", "engineer"),
    ("loop.done", "parked"),
    ("loop.done", "failed"),
])
def test_a_settled_verdict_or_task_is_not_live_work(tmp_path, event_type, status):
    """Handing the work on ends the role's turn, wherever the work went."""
    now = time.time()
    _write_events(tmp_path, [{"type": event_type, "status": status, "ts": now - 3}])

    role = "reviewer" if event_type == "round.review.completed" else "engineer"
    activity = role_activity(tmp_path, now=now)[role]
    assert activity.active is False
    assert status in activity.label
