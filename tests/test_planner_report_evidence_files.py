"""Reviewer→planner evidence_files briefing + LLM-judged-health wiring.

The reviewer's ``planner_report`` is the ONLY structured thing the L4 planner
reads about a mission. For the planner to actually diagnose a failed training
run (instead of re-dispatching another micro-smoke), the reviewer must hand it
the concrete files to open, and the planner must be told to read them. These
tests pin that contract end to end.
"""
from __future__ import annotations

from argus_skill.reviewer import _parse_planner_report
from argus_skill.life.supervisor import LifeSupervisor


def test_parse_planner_report_extracts_evidence_files() -> None:
    parsed = {
        "planner_report": {
            "forward_progress": False,
            "headline": "r4c smoke wired but terminal gate tripped",
            "blocker": "mechanical clipping gate fired on one tail step",
            "recommended_next": "scale to real pilot or diagnose clipping",
            "evidence_files": [
                {"path": "experiments/runs/r4c/status.json", "why": "shows state=failed + exit"},
                {"path": "experiments/runs/r4c/progress.jsonl", "why": "metric trend per step"},
                {"path": "code/train_rl_lora_adapter.py", "why": "the terminal gate source"},
            ],
        }
    }
    out = _parse_planner_report(parsed, status="done", reason="")
    ev = out["evidence_files"]
    assert len(ev) == 3
    assert ev[0]["path"] == "experiments/runs/r4c/status.json"
    assert "metric trend" in ev[1]["why"]


def test_parse_planner_report_evidence_files_fail_soft() -> None:
    # Junk entries are dropped, not fatal; missing field -> empty list.
    parsed = {
        "planner_report": {
            "forward_progress": True,
            "headline": "h",
            "evidence_files": [
                "not-an-object",
                {"why": "no path -> dropped"},
                {"path": "  ", "why": "blank path -> dropped"},
                {"path": "real/file.py", "why": "kept"},
            ],
        }
    }
    out = _parse_planner_report(parsed, status="done", reason="")
    assert [e["path"] for e in out["evidence_files"]] == ["real/file.py"]

    out2 = _parse_planner_report({"planner_report": {"headline": "x"}}, status="continue", reason="")
    assert out2["evidence_files"] == []

    out3 = _parse_planner_report({}, status="blocked", reason="r")
    assert out3["evidence_files"] == []


def test_parse_planner_report_caps_evidence_files_at_eight() -> None:
    many = [{"path": f"f{i}.txt", "why": "w"} for i in range(20)]
    out = _parse_planner_report(
        {"planner_report": {"forward_progress": False, "headline": "h", "evidence_files": many}},
        status="done", reason="",
    )
    assert len(out["evidence_files"]) == 8


def test_render_planner_report_lists_evidence_files() -> None:
    report = {
        "forward_progress": False,
        "headline": "terminal gate tripped on noise",
        "blocker": "mechanical clipping gate",
        "recommended_next": "scale up or diagnose",
        "evidence_files": [
            {"path": "experiments/runs/r4c/progress.jsonl", "why": "metric trend"},
            {"path": "code/train_rl_lora_adapter.py", "why": "gate source"},
        ],
    }
    rendered = LifeSupervisor._render_planner_report(report)
    assert "evidence_files the planner MUST open before replanning" in rendered
    assert "experiments/runs/r4c/progress.jsonl" in rendered
    assert "metric trend" in rendered
    assert "code/train_rl_lora_adapter.py" in rendered


def test_render_planner_report_omits_evidence_header_when_empty() -> None:
    rendered = LifeSupervisor._render_planner_report(
        {"forward_progress": True, "headline": "done", "evidence_files": []})
    assert "evidence_files" not in rendered
