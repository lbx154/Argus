"""The host log of the Engineer's round, rendered for the Reviewer.

One control project's Reviewer re-read 61 of the Engineer's files and still
never opened the script whose "perplexity" was a formula; the host had the
commands all along. The provider summarises them: counts, longest commands
(bounded by the time to the next action), tests and evaluations invoked, and
paths outside the workspace.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.engineer.round_evidence import RoundEvidenceRequest
from argus.verticals.research import round_log as mod
from argus.verticals.research import spec_checks


def _event(ts: float, kind: str, text: str, *, layer: str = "engineer", tool: str = "bash") -> dict:
    return {"type": "engineer.progress", "ts": ts, "kind": kind, "agent_layer": layer, "tool_name": tool, "text": text}


def _write_events(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_round_log_names_longest_commands_tests_and_outside_paths(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    (workdir / "results").mkdir(parents=True)
    project = tmp_path / "project"
    life_dir = project / "handoffs" / "item1"
    t0 = 1_800_000_000.0
    rows = [
        _event(t0 - 100, "command_execution", "python old_round.py"),  # before this round
        {"type": "round.start", "ts": t0, "round_index": 1, "text": "engineer round 1"},
        _event(t0 + 1, "tool_use", 'read: {"path": "METHOD.md"}', tool="read"),
        _event(t0 + 2, "command_execution", ".venv/bin/python -m pytest tests/spec"),
        _event(t0 + 10, "command_execution", "CUDA_VISIBLE_DEVICES=2 .venv/bin/python scripts/eval_ruler.py --config c.yaml"),
        _event(t0 + 68, "command_execution", "ls -la /data/other_user/models--Llama-3-8B/snapshots"),
        _event(t0 + 70, "tool_use", 'write: {"path": "results/summary.json"}', tool="write"),
        _event(t0 + 71, "command_execution", "cat results/summary.json"),
        _event(t0 + 72, "command_execution", "ls -la /data/v-boxiuli/argus-web-skill-scope-release-cc934eed1/argus/verticals/research/figure_lint.py"),
        _event(t0 + 80, "command_execution", "ls -la", layer="manager"),  # another role, ignored
    ]
    _write_events(project / "events.jsonl", rows)

    text = mod.render_round_log(workdir, life_dir, 1)

    assert text.startswith("Engineer's actions this round (host log since ")
    assert "5 shell commands, 1 file reads, 1 writes over 1.2 min" in text
    assert "old_round.py" not in text
    assert "ran ≤58 s (time to the next action): `CUDA_VISIBLE_DEVICES=2 .venv/bin/python scripts/eval_ruler.py --config c.yaml`" in text
    assert "tests and checks run, with the result the host recorded for the latest run:" in text
    assert "  - `.venv/bin/python -m pytest tests/spec`: no result recorded" in text
    assert "  - `CUDA_VISIBLE_DEVICES=2" in text
    assert "paths outside the workspace touched:" in text
    assert "/data/other_user/models--Llama-3-8B/snapshots" in text
    assert "/data/v-boxiuli/argus-web-skill-scope-release-cc934eed1/argus" in text
    assert "manager" not in text


def test_provider_is_silent_without_a_log_or_a_round(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    request = RoundEvidenceRequest(workdir=workdir, life_dir=tmp_path / "nowhere", round_index=1)
    assert spec_checks.round_log_evidence(request) is None

    project = tmp_path / "project"
    _write_events(project / "events.jsonl", [_event(1.0, "command_execution", "echo hi")])
    request = RoundEvidenceRequest(workdir=workdir, life_dir=project / "handoffs" / "x", round_index=1)
    assert spec_checks.round_log_evidence(request) is None  # no round.start or mission start to anchor the window


def test_provider_falls_back_to_the_mission_start_and_reports_in_the_reviewer_slot(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    project = tmp_path / "project"
    t0 = 1_800_000_000.0
    _write_events(
        project / "events.jsonl",
        [
            {"type": "life.mission.started", "ts": t0},
            _event(t0 + 5, "command_execution", "python scripts/run_positive_control.py"),
            _event(t0 + 62, "command_execution", "cat results/positive_control_results.json"),
        ],
    )
    request = RoundEvidenceRequest(workdir=workdir, life_dir=project / "handoffs" / "x", round_index=3)

    evidence = spec_checks.round_log_evidence(request)

    assert evidence is not None and evidence.engineer_note == ""
    assert "run_positive_control.py" in evidence.reviewer_text and "ran ≤57 s" in evidence.reviewer_text


def test_the_research_vertical_registers_the_provider_and_the_renderer_knows_no_higher_layer() -> None:
    import argus.verticals.research  # noqa: F401 - importing the vertical registers its providers
    from argus.engineer.round_evidence import registered_round_evidence_providers

    assert spec_checks.round_log_evidence in registered_round_evidence_providers()
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "engineer.round_evidence" not in source and "register_round_evidence_provider" not in source


def _start(ts: float, text: str, call_id: str) -> dict:
    return {**_event(ts, "command_execution", text), "status": "running", "call_id": call_id}


def _result(ts: float, call_id: str, exit_code: int, excerpt: str) -> dict:
    return {
        "type": "engineer.progress", "ts": ts, "kind": "tool_result", "agent_layer": "engineer",
        "tool_name": "bash", "text": excerpt, "status": "completed", "exit_code": exit_code,
        "call_id": call_id, "output_excerpt": excerpt,
    }


def test_round_log_carries_the_result_the_host_recorded_for_each_check(tmp_path: Path) -> None:
    """A read-only Reviewer weighs these results instead of the Engineer's account of them."""
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    project = tmp_path / "project"
    t0 = 1_800_000_000.0
    rows = [
        {"type": "round.start", "ts": t0, "round_index": 1},
        # An older log names a failed command again without a call id.
        _event(t0 + 0.5, "command_execution", "make build"),
        {**_event(t0 + 0.6, "command_execution", "make build"), "status": "failed", "exit_code": 1},
        # Started, then finished: one command, joined by call id.
        _start(t0 + 1, "python -m pytest -q tests", "c1"),
        _result(t0 + 9, "c1", 0, "14 passed in 0.31s"),
        _start(t0 + 10, "python scripts/check_output.py", "c2"),
        {**_event(t0 + 12, "command_execution", "python scripts/check_output.py"),
         "status": "failed", "exit_code": 2, "call_id": "c2", "output_excerpt": "AssertionError: row 7"},
        # Reported once, finished, with its exit code on the same row.
        {**_event(t0 + 20, "command_execution", "python -m unittest discover -s tests"),
         "status": "completed", "exit_code": 0, "output_excerpt": "OK"},
        # A check whose run has no recorded result.
        _event(t0 + 40, "command_execution", "python verify_score.py"),
    ]
    _write_events(project / "events.jsonl", rows)

    text = mod.render_round_log(workdir, project / "handoffs" / "x", 1)

    assert "5 shell commands" in text  # results are not counted as commands
    assert "  - `python -m pytest -q tests`: exit 0: 14 passed in 0.31s" in text
    assert "  - `python scripts/check_output.py`: exit 2: AssertionError: row 7" in text
    assert "  - `python -m unittest discover -s tests`: exit 0: OK" in text
    assert "  - `python verify_score.py`: no result recorded" in text
    assert "- other commands that failed: `make build` (exit 1)" in text
    # The last commands are all checks listed above; nothing is said twice.
    assert "the round's last commands" not in text


def test_the_last_commands_carry_their_results_when_no_check_names_them(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    project = tmp_path / "project"
    t0 = 1_800_000_000.0
    _write_events(project / "events.jsonl", [
        {"type": "round.start", "ts": t0, "round_index": 1},
        _start(t0 + 1, "python read_score.py --xml > out.xml", "r1"),
        _result(t0 + 2, "r1", 0, "Readable XML is byte-identical to the compressed score member."),
    ])

    text = mod.render_round_log(workdir, project / "handoffs" / "x", 1)

    assert "- the round's last commands, with the result the host recorded:" in text
    assert "  - `python read_score.py --xml > out.xml`: exit 0: Readable XML is byte-identical" in text


def test_a_result_updates_the_latest_run_of_a_repeated_check(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    project = tmp_path / "project"
    t0 = 1_800_000_000.0
    _write_events(project / "events.jsonl", [
        {"type": "round.start", "ts": t0, "round_index": 2},
        _start(t0 + 1, "pytest -q", "a"),
        _result(t0 + 2, "a", 1, "1 failed, 13 passed"),
        _start(t0 + 5, "pytest -q", "b"),
        _result(t0 + 6, "b", 0, "14 passed"),
    ])

    text = mod.render_round_log(workdir, project / "handoffs" / "x", 2)

    assert "  - `pytest -q` ×2: exit 0: 14 passed" in text
    assert "1 failed" not in text
