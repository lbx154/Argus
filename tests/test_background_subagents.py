"""Tests for the background-subagent advisory used by the engineer round loop.

Covers registry scanning + self-watched/needs-attention classification,
structured-wait advisory rendering, the legacy sentinel adapter, and cadence.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from argus_skill.engineer import background_subagents as bs


def _write_record(reg: Path, task_id: str, **fields) -> Path:
    reg.mkdir(parents=True, exist_ok=True)
    record = {
        "task_id": task_id,
        "description": f"job {task_id}",
        "mode": "supervised",
        "state": "running",
        "last_supervisor_health": "healthy",
        "last_supervisor_decision": "continue",
        "last_supervisor_concern": "",
        "monitor_interval": 120,
        "elapsed_seconds": 1000,
        "worker_pid": os.getpid(),
    }
    record.update(fields)
    path = reg / f"{task_id}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _dead_pid() -> int:
    """A pid that is guaranteed not to name a live process."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


# ---- scanning + classification -------------------------------------------

def test_no_registry_is_empty(tmp_path: Path) -> None:
    assert bs.scan_inflight_subagents(tmp_path) == []
    assert bs.render_background_subagents_advisory(tmp_path) == ""


def test_supervised_healthy_is_self_watched(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "t1")
    subs = bs.scan_inflight_subagents(tmp_path)
    assert len(subs) == 1
    assert subs[0].self_watched is True
    assert subs[0].attention_reason == ""


def test_direct_mode_needs_attention(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "t1", mode="direct")
    sub = bs.scan_inflight_subagents(tmp_path)[0]
    assert sub.self_watched is False
    assert "no independent supervisor" in sub.attention_reason


def test_discussing_needs_attention(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "t1", state="discussing")
    sub = bs.scan_inflight_subagents(tmp_path)[0]
    assert sub.self_watched is False
    assert "discussion" in sub.attention_reason


def test_degraded_health_needs_attention(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "t1", last_supervisor_health="diverging")
    sub = bs.scan_inflight_subagents(tmp_path)[0]
    assert sub.self_watched is False
    assert "health=diverging" in sub.attention_reason


def test_concern_needs_attention(tmp_path: Path) -> None:
    _write_record(
        tmp_path / ".argus_subagents", "t1",
        last_supervisor_concern="reward collapsed to zero",
    )
    sub = bs.scan_inflight_subagents(tmp_path)[0]
    assert sub.self_watched is False
    assert "concern raised" in sub.attention_reason


def test_boilerplate_concern_is_not_attention(tmp_path: Path) -> None:
    _write_record(
        tmp_path / ".argus_subagents", "t1",
        last_supervisor_concern="none",
    )
    sub = bs.scan_inflight_subagents(tmp_path)[0]
    assert sub.self_watched is True


def test_early_stop_decision_needs_attention(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "t1", last_supervisor_decision="early_stop")
    sub = bs.scan_inflight_subagents(tmp_path)[0]
    assert sub.self_watched is False
    assert "early_stop" in sub.attention_reason


def test_dead_pid_needs_attention(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "t1", worker_pid=_dead_pid())
    sub = bs.scan_inflight_subagents(tmp_path)[0]
    assert sub.pid_alive is False
    assert sub.self_watched is False
    assert "not alive" in sub.attention_reason


def test_stale_heartbeat_needs_attention(tmp_path: Path) -> None:
    reg = tmp_path / ".argus_subagents"
    path = _write_record(reg, "t1", monitor_interval=120)
    old = path.stat().st_mtime
    # now far enough past the file mtime to exceed max(interval, cap=900) * 2.
    now = old + 900 * 2 + 5
    sub = bs.scan_inflight_subagents(tmp_path, now=now)[0]
    assert sub.stale is True
    assert sub.self_watched is False
    assert "stale" in sub.attention_reason


def test_terminal_states_excluded(tmp_path: Path) -> None:
    reg = tmp_path / ".argus_subagents"
    for i, state in enumerate(["done", "error", "crashed", "timeout", "early_stopped"]):
        _write_record(reg, f"t{i}", state=state)
    assert bs.scan_inflight_subagents(tmp_path) == []


def test_unreadable_record_is_skipped(tmp_path: Path) -> None:
    reg = tmp_path / ".argus_subagents"
    reg.mkdir()
    (reg / "broken.json").write_text("{not json", encoding="utf-8")
    _write_record(reg, "t1")
    subs = bs.scan_inflight_subagents(tmp_path)
    assert [s.task_id for s in subs] == ["t1"]


# ---- advisory rendering ---------------------------------------------------

def test_advisory_lists_watched_and_offers_wait(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "train-1", description="full GRPO run")
    text = bs.render_background_subagents_advisory(tmp_path)
    assert "Background subagents in flight" in text
    assert "Self-watched and healthy" in text
    assert "train-1" in text
    assert "WAIT_FOR_SUBAGENT: <task_id>" in text
    assert "Needs your attention" not in text


def test_advisory_attention_suppresses_wait(tmp_path: Path) -> None:
    reg = tmp_path / ".argus_subagents"
    _write_record(reg, "healthy-1")
    _write_record(reg, "direct-1", mode="direct")
    text = bs.render_background_subagents_advisory(tmp_path)
    assert "Needs your attention" in text
    assert "direct-1" in text
    # A job needs attention, so the blanket wait affordance must be withheld.
    assert "reply with exactly" not in text
    assert "Do not request a structured wait" in text


# ---- sentinel parsing -----------------------------------------------------

def test_parse_sentinel_plain() -> None:
    assert bs.parse_wait_sentinel("WAIT_FOR_SUBAGENT: train-1") == "train-1"


def test_parse_sentinel_fenced_and_backticked() -> None:
    assert bs.parse_wait_sentinel("```\nWAIT_FOR_SUBAGENT: `train-1`\n```") == "train-1"


def test_parse_sentinel_accepts_final_control_line_after_summary_and_handoff() -> None:
    message = (
        "Summary:\n"
        "- repaired the evaluator\n"
        "\n"
        "HANDOFF:\n"
        "- supervised run is still healthy\n"
        "\n"
        "WAIT_FOR_SUBAGENT: train-1"
    )
    assert bs.parse_wait_sentinel(message) == "train-1"


def test_parse_sentinel_rejects_prose() -> None:
    assert bs.parse_wait_sentinel("I will now WAIT_FOR_SUBAGENT: train-1 and wait") is None


def test_parse_sentinel_rejects_embedded_prose_mentions() -> None:
    message = (
        "Summary:\n"
        "- I may WAIT_FOR_SUBAGENT: train-1 later if nothing else is ready\n"
        "\n"
        "HANDOFF:\n"
        "- keep working for now"
    )
    assert bs.parse_wait_sentinel(message) is None


def test_parse_sentinel_rejects_nonempty_text_after_final_control_line() -> None:
    assert (
        bs.parse_wait_sentinel("WAIT_FOR_SUBAGENT: train-1\nAlso doing other work.")
        is None
    )


def test_parse_sentinel_rejects_trailing_text_on_control_line() -> None:
    assert bs.parse_wait_sentinel("WAIT_FOR_SUBAGENT: train-1 please wait") is None


def test_parse_sentinel_rejects_empty() -> None:
    assert bs.parse_wait_sentinel("") is None
    assert bs.parse_wait_sentinel("WAIT_FOR_SUBAGENT:") is None


# ---- cadence wait ---------------------------------------------------------

def test_cadence_seconds_clamped(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "lo", monitor_interval=5)
    _write_record(tmp_path / ".argus_subagents", "hi", monitor_interval=100000)
    subs = {s.task_id: s for s in bs.scan_inflight_subagents(tmp_path)}
    assert bs.cadence_seconds(subs["lo"]) == 30.0
    assert bs.cadence_seconds(subs["hi"]) == 900.0


def test_wait_not_waitable_for_unknown(tmp_path: Path) -> None:
    slept: list[float] = []
    reason, waited = bs.wait_for_subagent_cadence(
        tmp_path, "nope", sleep=slept.append
    )
    assert reason == "not_waitable"
    assert waited == 0.0
    assert slept == []


def test_wait_not_waitable_for_attention_job(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "d1", mode="direct")
    reason, _ = bs.wait_for_subagent_cadence(tmp_path, "d1", sleep=lambda _s: None)
    assert reason == "not_waitable"


def test_wait_cadence_elapsed(tmp_path: Path) -> None:
    _write_record(tmp_path / ".argus_subagents", "t1", monitor_interval=30)
    slept: list[float] = []
    reason, waited = bs.wait_for_subagent_cadence(
        tmp_path, "t1", sleep=slept.append, poll_interval=1000
    )
    assert reason == "cadence_elapsed"
    assert waited == 30.0
    assert slept == [30.0]


def test_wait_wakes_on_terminal(tmp_path: Path) -> None:
    reg = tmp_path / ".argus_subagents"
    path = _write_record(reg, "t1", monitor_interval=120)

    def _sleep_then_finish(_seconds: float) -> None:
        # Simulate the run finishing during the first sleep chunk.
        record = json.loads(path.read_text())
        record["state"] = "done"
        path.write_text(json.dumps(record))

    reason, waited = bs.wait_for_subagent_cadence(
        tmp_path, "t1", sleep=_sleep_then_finish, poll_interval=15
    )
    assert reason == "terminal"
    assert waited == 15.0  # woke after the first poll chunk
