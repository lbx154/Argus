"""Each scientific hard gate has one home, and every role that applies it reads it once.

The prompts are rendered from s-efe9deac in Experiment as it stood when the
Engineer ran mission 5665c4cc565d (call dfd4a435), the Reviewer judged it
(a8447ec8) and the Planner planned from the same brief (ee405d70): the
research vertical on a broad route to ACL, the mission and brief as recorded,
the Reviewer's real next action, and the workspace's METHOD.md claim and
claim statement. A change that deletes a copy of a gate keeps every count
here at exactly one.

H8 (independent review) lives in host routing rather than prose; H9 (the
final manuscript gate) has no single home yet.
"""
from __future__ import annotations

import json
import re
import shutil
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.apps._runtime_supervisor import _independent_review_required_for_project_root
from argus.roles.prompts import resolve_role_prompt
from argus.roles.prompts.engineer import build_mission_prompt, mission_request
from argus.roles.prompts.planner import build_continuous_prompt
from argus.roles.prompts.reviewer import assemble_reviewer_prompt, render_reviewer_prompt
from argus.skills.stage_machine import current_stage

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "hard_gates"

ROLES = ("engineer", "reviewer", "planner")
# Each gate in its home's words, and the prompts that must carry it once.
GATES = (
    ("H1", "Never fabricate results, numbers, citations or literature, fake credentials or "
     "services, or misreport progress, completion or retries; a reconstructed or overwritten "
     "record is never the contemporaneous one.", ROLES),
    ("H2", "Stand-ins are never results: a mock model, fake environment, oracle policy, random "
     "inputs or synthetic data standing where the route names a real system or data belong in "
     "tests/spec, never in a claim-bearing run; when the real system cannot run here, in order: "
     "stand it up, shrink the scale keeping it real only as a Protocol deviation named in "
     "METHOD.md first, or name the deviation there and claim nothing from that run.", ROLES),
    ("H2", "is NOT_IMPLEMENTED whatever the tests say", ("reviewer",)),
    ("H3", "Claims of superiority require beating the strongest same-information baseline.",
     ("reviewer", "planner")),
    ("H4", "Freeze the comparison before held-out confirmation; held-out data never enters "
     "training, method or configuration selection.", ROLES),
    ("H5", "never request a rollback or reopen Idea selection.", ROLES),
    ("H5", "A claim-bearing task re-issued after any failure keeps the METHOD.md Protocol's "
     "data, scale and acceptance: repair the cause, never lower the bar.", ROLES),
    ("H5", "This item binds the METHOD.md claim and its claim-bearing comparisons, not the "
     "numeric tolerance of an engineering self-check.", ROLES),
    ("H6", "results with no statement are a continue with one question: which clauses do "
     "these numbers meet.", ("reviewer",)),
    ("H7", "Claim attainment (the Engineer's statement per clause of the claim;",
     ("reviewer", "planner")),
    ("H10", "is display masking that proves neither a defect nor a fix", ("reviewer",)),
    ("H10", "If the review disputes text a display masks", ("engineer after a review",)),
)


def _project(tmp_path: Path) -> tuple[Path, Path, dict]:
    state, workspace = tmp_path / "state", tmp_path / "workspace"
    (state / ".argus").mkdir(parents=True)
    (workspace / ".argus").mkdir(parents=True)
    shutil.copy(FIXTURE / "PIPELINE_STATE.json", state / ".argus" / "PIPELINE_STATE.json")
    shutil.copy(FIXTURE / "METHOD.md", workspace / "METHOD.md")
    shutil.copy(FIXTURE / "claim_attainment.json", workspace / ".argus" / "claim_attainment.json")
    mission = json.loads((FIXTURE / "mission_5665c4cc565d.json").read_text(encoding="utf-8"))
    return state, workspace, mission


def _engineer(state: Path, workspace: Path, mission: dict, next_action: str | None) -> str:
    banner = resolve_role_prompt(
        mission_request(state, vertical="research", altitude_root=workspace, stage=current_stage(state))
    ).role_banner
    return build_mission_prompt(
        task=mission["objective"],
        skill_text="",
        next_action=next_action,
        original_request=mission["original_objective"],
        role_banner=banner,
        project_root=workspace,
    )


def _reviewer(state: Path, workspace: Path, mission: dict) -> str:
    owner = SimpleNamespace(skill_store=None, mission=None, _last_prompt_block_stats={})
    return assemble_reviewer_prompt(*render_reviewer_prompt(
        owner,
        objective=mission["objective"],
        original_objective=mission["original_objective"],
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=mission["id"],
        main_summary=mission["title"],
        main_error=None,
        scope="bounded",
        working_dir=workspace,
        vertical_state_root=state,
        vertical="research",
        preselected_skill_block="",
    ))


def _planner(state: Path, workspace: Path, mission: dict) -> str:
    return build_continuous_prompt(
        continuous_objective=mission["original_objective"],
        journal_tail="",
        planning_cycle=0,
        project_root=workspace,
        state_root=state,
    )


def _prompts(tmp_path: Path) -> tuple[dict[str, str], dict]:
    state, workspace, mission = _project(tmp_path)
    return {
        "engineer": _engineer(state, workspace, mission, None),
        "engineer after a review": _engineer(state, workspace, mission, mission["review_next_action"]),
        "reviewer": _reviewer(state, workspace, mission),
        "planner": _planner(state, workspace, mission),
    }, mission


def test_each_gate_reads_once_in_every_role_that_applies_it(tmp_path) -> None:
    prompts, _mission = _prompts(tmp_path)

    counts = {(gate, text[:40], role): prompts[role].count(text) for gate, text, roles in GATES for role in roles}

    assert counts == dict.fromkeys(counts, 1)


def test_moving_back_is_spoken_of_only_in_its_home(tmp_path) -> None:
    # H5 reworded is still H5; the recorded mission's own lines are its data.
    prompts, mission = _prompts(tmp_path)
    recorded = {line.strip() for value in mission.values() if isinstance(value, str) for line in value.splitlines()}
    words = re.compile(r"roll ?back|reopen|backward|forward[- ]only", re.IGNORECASE)

    counts = {
        role: len(words.findall("\n".join(line for line in prompt.splitlines() if line.strip() not in recorded)))
        for role, prompt in prompts.items()
    }

    # Its home, experiment.fixed_claim: "never request a rollback or reopen Idea selection".
    assert counts == dict.fromkeys(prompts, 2)


def test_the_reviewers_judging_rules_live_in_its_prompt_not_its_skill(tmp_path) -> None:
    rules = (
        "Judge the deliverable the operator asked for. Argus's own records (files under "
        "`.argus/`, evidence logs, certification notes) are not it: a slip in them is a note "
        "in your review, fixed in passing where the task owns that record, and never a reason "
        "to hold a deliverable that meets its acceptance.",
        "Judge the files as they are now. An earlier review's text is not a precondition: "
        "when it is not in front of you, reach your own judgment from the current files "
        "instead of blocking or asking for it.",
    )
    prompt = _reviewer(*_project(tmp_path))
    skill = " ".join(
        (resources.files("argus.builtin_skills") / "reviewer" / "argus-reviewer-role.md")
        .read_text(encoding="utf-8").split()
    )

    assert [prompt.count(rule) for rule in rules] == [1, 1]
    assert [rule.split(".")[0] in skill for rule in rules] == [False, False]


def test_the_external_target_rule_reads_once_for_the_planner_under_a_scorer(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.roles.prompts.planner import _EXTERNAL_TARGET_CONTRACT

    state, workspace, mission = _project(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_EXTERNAL_COMPLETION_GATE", "score >= 0.9")

    assert _planner(state, workspace, mission).count(_EXTERNAL_TARGET_CONTRACT) == 1  # H11


def test_independent_review_is_host_routing_for_the_project(tmp_path) -> None:
    state, _workspace, _mission = _project(tmp_path)

    assert _independent_review_required_for_project_root(state)  # H8
