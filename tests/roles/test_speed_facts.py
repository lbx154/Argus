"""A phase's share of one run is a fact about that run, and speed has one rule home.

In s-efe9deac the 10:36 profile said generation took 88% of a step, and the
Engineer still wrote "no root cause, replacement or engine speed-up
conclusion": its prompt (call dfd4a435) said latency evidence needs an
uncontended device, and no snapshot ever proves one. The Reviewer (a8447ec8)
read that dominant-stage claims need proof and the Planner (ee405d70) the same
of performance claims, while staying on native generation needed none. The
prompts are rendered from that state (test_hard_gate_homes); the Skill pages
are read where the Engineer opens them.
"""
from __future__ import annotations

from pathlib import Path

from test_hard_gate_homes import _engineer, _planner, _project, _reviewer

import argus
from argus.verticals.research.prompt_policy import _research_learning_block

SKILLS = Path(argus.__file__).parent / "verticals" / "research" / "skills"
SHARE = "A phase's share of one run's own time is read inside that run and needs none of this."
ISOLATION = ("uncontended device", "does not prove isolation", "uncontended measurement window")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_engineer_reads_a_share_inside_its_run(tmp_path) -> None:
    state, workspace, mission = _project(tmp_path)
    for prompt in (
        _engineer(state, workspace, mission, None),
        _engineer(state, workspace, mission, mission["review_next_action"]),
    ):
        assert [phrase for phrase in ISOLATION if phrase in prompt] == []
        assert "replacement claims" not in prompt
        assert prompt.count(SHARE) == 1


def test_one_proof_rule_for_performance_claims_and_none_for_staying(tmp_path) -> None:
    state, workspace, mission = _project(tmp_path)
    reviewer, planner = _reviewer(state, workspace, mission), _planner(state, workspace, mission)

    assert "dominant-stage" not in reviewer
    assert reviewer.count("Root-cause, bottleneck, or replacement claims need code-path evidence") == 1
    assert "Performance claims need" not in planner
    assert "measured throughput" not in planner
    assert _flat(planner).count(
        "From the digest's times, what would shorten the time to the first held-out number?"
    ) == 1


def test_the_playbook_times_comparisons_not_shares() -> None:
    playbook = _flat((SKILLS / "research-experiment-playbook.md").read_text(encoding="utf-8"))

    assert [phrase for phrase in ISOLATION if phrase in playbook] == []
    assert playbook.count(SHARE) == 1
    assert "When latency or throughput is compared across runs or devices" in playbook
    assert "optional performance optimizations" not in playbook
    assert "A speedup goes into the next attempt instead of replacing a running one." in playbook
    assert "`engineer/training_scripts/phase_table.py`" in playbook


def test_the_infrastructure_guide_runs_the_script_and_keeps_the_rule_once() -> None:
    guide = _flat((SKILLS / "engineer" / "training-infrastructure.md").read_text(encoding="utf-8"))
    rule = "The dominant phase decides the architecture"
    pages = [path for path in SKILLS.rglob("*") if path.suffix in {".md", ".py"} and path.is_file()]

    assert "python training_scripts/phase_table.py <log> --engine" in guide
    assert "log-prob scoring, backward, weight sync and idle" not in guide
    assert [path.name for path in pages if rule in _flat(path.read_text(encoding="utf-8"))] == [
        "training-infrastructure.md"
    ]
    assert "state what would change the choice" not in guide
    assert "the `phase_table.py` command that re-measures it" in guide
    assert "is not a reason to stay on native generation" in guide


def test_the_decision_record_keeps_a_rerun_command_not_a_condition() -> None:
    """The project's record kept HF under "What would change it: 受控phase测量显示值得迁移",
    a condition its own profile could never meet."""
    block = _flat(_research_learning_block("engineer", "experiment", ""))

    assert "what would change it" not in block
    assert "decision; the `phase_table.py` command that re-measures it; valid while" in block
