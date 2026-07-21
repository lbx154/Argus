"""Tests for the generic research-gate contract (research_gates.py)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills import research_gates as rg


def _fail(fid: str, sev: str = "major", **kw) -> dict:
    return {
        "failure_id": fid, "severity": sev, "stage": "scope", "artifact": "X.csv",
        "field": "", "message": f"msg {fid}", "required_action": f"fix {fid}",
        "blocks_progress": sev == "blocker", **kw,
    }


def test_write_gate_outputs_emits_failures_with_required_action(tmp_path: Path) -> None:
    failures = [_fail("LIT-001", "blocker"), _fail("LIT-003")]
    rg.write_gate_outputs(tmp_path, "literature", result={"passed": False}, failures=failures,
                          human_review="# review\nfail")
    research = tmp_path / "research"
    assert (research / "LITERATURE_GATE_RESULT.json").is_file()
    assert (research / "LITERATURE_GATE_REVIEW.md").is_file()
    dumped = json.loads((research / "LITERATURE_GATE_FAILURES.json").read_text())
    assert [f["failure_id"] for f in dumped] == ["LIT-001", "LIT-003"]
    assert all(f["required_action"] for f in dumped)
    tasks = (research / "LITERATURE_GATE_REPAIR_TASKS.md").read_text()
    assert "LIT-001" in tasks and "Required action" in tasks


def test_write_gate_outputs_clears_failures_on_pass(tmp_path: Path) -> None:
    rg.write_gate_outputs(tmp_path, "literature", result={"passed": False}, failures=[_fail("LIT-001")],
                          human_review="x")
    rg.write_gate_outputs(tmp_path, "literature", result={"passed": True}, failures=[], human_review="ok")
    assert not (tmp_path / "research" / "LITERATURE_GATE_FAILURES.json").exists()


def test_update_gate_state_tracks_resolved_persistent_new(tmp_path: Path) -> None:
    rg.update_gate_state(tmp_path, "literature", [_fail("A"), _fail("B"), _fail("C")])
    s = rg.update_gate_state(tmp_path, "literature", [_fail("B"), _fail("C"), _fail("D")])
    assert s["round"] == 2
    assert set(s["resolved_failure_ids"]) == {"A"}
    assert set(s["persistent_failure_ids"]) == {"B", "C"}
    assert set(s["new_failure_ids"]) == {"D"}


def test_stall_after_two_rounds_without_drop(tmp_path: Path) -> None:
    rg.update_gate_state(tmp_path, "literature", [_fail("A"), _fail("B")])
    s = rg.update_gate_state(tmp_path, "literature", [_fail("A"), _fail("B")])
    assert not s["stalled"] and s["no_drop_streak"] == 1
    s = rg.update_gate_state(tmp_path, "literature", [_fail("A"), _fail("B")])
    assert s["stalled"] and s["status"] == "literature_stalled"
    assert (tmp_path / "research" / "LITERATURE_GATE_STALLED.md").is_file()


def test_drop_clears_stall_and_stalled_md(tmp_path: Path) -> None:
    for _ in range(3):
        rg.update_gate_state(tmp_path, "literature", [_fail("A"), _fail("B")])
    assert (tmp_path / "research" / "LITERATURE_GATE_STALLED.md").exists()
    s = rg.update_gate_state(tmp_path, "literature", [_fail("A")])
    assert not s["stalled"] and s["no_drop_streak"] == 0
    assert not (tmp_path / "research" / "LITERATURE_GATE_STALLED.md").exists()


def test_render_active_repair_blocks_includes_failure_ids(tmp_path: Path) -> None:
    rg.update_gate_state(tmp_path, "literature", [_fail("LIT-001", "blocker"), _fail("LIT-003")])
    block = rg.render_active_repair_blocks(tmp_path)
    assert "LITERATURE_GATE REPAIR REQUIRED" in block
    assert "LIT-001" in block and "LIT-003" in block and "fix LIT-001" in block


def test_clear_gate_state_removes_state(tmp_path: Path) -> None:
    rg.update_gate_state(tmp_path, "literature", [_fail("A")])
    assert rg.read_gate_state(tmp_path, "literature") is not None
    rg.clear_gate_state(tmp_path, "literature")
    assert rg.read_gate_state(tmp_path, "literature") is None
    assert rg.render_active_repair_blocks(tmp_path) == ""


def test_render_active_blocks_multiple_gates(tmp_path: Path) -> None:
    rg.update_gate_state(tmp_path, "literature", [_fail("LIT-001")])
    rg.update_gate_state(tmp_path, "theory", [_fail("TH-002")])
    block = rg.render_active_repair_blocks(tmp_path)
    assert "LITERATURE_GATE" in block and "THEORY_GATE" in block


def test_gate_file_prefix() -> None:
    assert rg.gate_file_prefix("literature") == "LITERATURE_GATE"
    assert rg.gate_file_prefix("paper-type") == "PAPER_TYPE_GATE"
