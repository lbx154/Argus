from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.life.context_packet import (
    create_mission_context,
    record_engineer_handoff,
    record_reviewed_handoff,
    render_mission_brief,
    render_mission_contract,
)
from argus_skill.life.memory import BacklogItem
from argus_skill.life.supervisor import LifeSupervisor
from argus_skill.planner.planner import hydrate_task_context_refs


def test_context_packet_seals_engineer_and_reviewer_handoffs(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path,
        mission_id="mission-1",
        stage="research",
        scope="bounded",
        work_kind="algorithm_discovery",
        objective="Screen one candidate on public tasks.",
        acceptance_check="research/screen.json reports a binding pass/fail",
        plan_hypothesis="The candidate screen can eliminate weak directions cheaply.",
        goal_contribution="Reduce uncertainty before the expensive experiment.",
        expected_regressions="Candidate count may fall sharply.",
        decision_rule="Replace the screen if it fails to predict the binding test.",
        owns_paths=["research/screen.json", "tests/test_screen.py"],
        non_goals=["do not preregister", "do not run GPU inference"],
        context_refs=[
            {
                "kind": "artifact",
                "ref": "research/IDEA_CANDIDATES.md",
                "why": "candidate universe",
                "content_hash": "abc",
            }
        ],
        plan_id="plan-1",
        plan_version=1,
        node_key="screen",
    )
    checkpoint = mission.parent / "CHECKPOINT.md"
    assert checkpoint.is_file()
    assert checkpoint.read_text(encoding="utf-8") == ""
    checkpoint.write_text("# Current State\n\nScreen complete.\n", encoding="utf-8")
    engineer = record_engineer_handoff(
        mission_context_path=mission,
        round_index=1,
        engineer_summary="Created the screen packet.",
        checkpoint_path=checkpoint,
        thread_id="fresh-engineer-session",
    )
    assert engineer is not None
    latest = json.loads((mission.parent / "latest.json").read_text())
    mission_payload = json.loads(mission.read_text())
    engineer_payload = json.loads(engineer.read_text())
    assert latest["kind"] == "handoff_ref"
    assert latest["handoff"]["path"] == str(engineer)
    assert "sha256" not in latest["handoff"]
    assert latest["mission"]["path"] == str(mission)
    assert mission_payload["stage"] == "research"
    assert mission_payload["scope"] == "bounded"
    assert mission_payload["work_kind"] == "algorithm_discovery"
    assert mission_payload["objective"] == "Screen one candidate on public tasks."
    assert mission_payload["acceptance_check"].endswith("binding pass/fail")
    assert mission_payload["plan_hypothesis"].startswith("The candidate screen")
    assert mission_payload["goal_contribution"].startswith("Reduce uncertainty")
    assert mission_payload["expected_regressions"] == "Candidate count may fall sharply."
    assert mission_payload["decision_rule"].startswith("Replace the screen")
    assert mission_payload["owns_paths"] == [
        "research/screen.json",
        "tests/test_screen.py",
    ]
    assert mission_payload["non_goals"] == [
        "do not preregister",
        "do not run GPU inference",
    ]
    assert mission_payload["context_refs"][0]["ref"] == "research/IDEA_CANDIDATES.md"
    assert "content_hash" not in mission_payload["context_refs"][0]
    assert mission_payload["checkpoint"] == {
        "path": str(checkpoint),
        "contract_version": 2,
    }
    frontier_path = Path(mission_payload["frontier"]["path"])
    frontier = json.loads(frontier_path.read_text(encoding="utf-8"))
    assert frontier["objective"] == "Screen one candidate on public tasks."
    assert frontier["current_hypothesis"].startswith("The candidate screen")
    assert frontier["next_decision_point"].startswith("Replace the screen")
    assert (
        not {
            "stage",
            "scope",
            "objective",
            "acceptance_check",
            "plan_hypothesis",
            "goal_contribution",
            "expected_regressions",
            "decision_rule",
            "non_goals",
            "context_refs",
        }
        & latest.keys()
    )
    assert "sha256" not in engineer_payload["checkpoint"]
    assert "control" not in engineer_payload
    assert "text" not in engineer_payload["checkpoint"]
    assert engineer_payload["engineer_summary"] == "Created the screen packet."

    reviewed = record_reviewed_handoff(
        mission_context_path=mission,
        round_index=1,
        engineer_summary="Created the screen packet.",
        review=SimpleNamespace(
            status="done",
            reason="Artifact verified.",
            next_action="Planner may choose the next frontier.",
            operator_question="",
            frontier_report={
                "change": "uncertainty_reduced",
                "summary": "The screen eliminated one weak direction.",
                "resolved_obligations": ["screen candidate"],
                "remaining_work": ["choose the next candidate"],
                "uncertainty": "One candidate is now ruled out.",
                "next_decision_point": "Choose or stop based on remaining candidates.",
            },
        ),
        checkpoint_path=checkpoint,
    )
    assert reviewed is not None
    latest = json.loads((mission.parent / "latest.json").read_text())
    reviewed_payload = json.loads(reviewed.read_text())
    assert latest["kind"] == "handoff_ref"
    assert latest["mission"] == {"path": str(mission)}
    assert latest["handoff"]["path"] == str(reviewed)
    assert reviewed_payload["review"]["status"] == "done"
    assert set(reviewed_payload["review"]) == {
        "status",
        "reason",
        "next_action",
        "operator_question",
        "frontier_transition",
        "frontier_disposition",
    }
    frontier = json.loads(frontier_path.read_text(encoding="utf-8"))
    assert frontier["history"][-1]["change"] == "uncertainty_reduced"
    assert reviewed_payload["review"]["frontier_disposition"] == "continue"
    assert reviewed_payload["engineer_summary"] == "Created the screen packet."
    assert "text" not in reviewed_payload["checkpoint"]
    rendered = render_mission_contract(mission)
    assert rendered.count("Screen one candidate on public tasks.") == 1
    assert "Acceptance:" in rendered
    assert "- do not preregister" in rendered
    assert "content_hash" not in rendered


def test_context_refresh_never_overwrites_role_authored_checkpoint(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path,
        mission_id="mission-refresh",
        stage="research",
        objective="First objective",
    )
    checkpoint = mission.parent / "CHECKPOINT.md"
    checkpoint.write_text(
        "# Open Questions / Blockers\n\n- preserve this state\n",
        encoding="utf-8",
    )
    role_state = mission.parent / "role-sessions" / "engineer.json"
    role_state.parent.mkdir()
    role_state.write_text('{"turns": 3, "thread_id": "keep-me"}\n', encoding="utf-8")

    refreshed = create_mission_context(
        life_dir=tmp_path,
        mission_id="mission-refresh",
        stage="research",
        objective="Refreshed objective",
    )

    assert refreshed == mission
    assert checkpoint.read_text(encoding="utf-8").endswith("- preserve this state\n")
    assert json.loads(role_state.read_text(encoding="utf-8")) == {
        "turns": 3,
        "thread_id": "keep-me",
    }


def test_round_packets_cap_engineer_account_at_4000_chars(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path,
        mission_id="account-cap",
        stage="change",
        objective="Keep the actual account compact.",
    )
    handoff = record_engineer_handoff(
        mission_context_path=mission,
        round_index=1,
        engineer_summary="a" * 5000,
        checkpoint_path=None,
    )

    assert handoff is not None
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    assert payload["engineer_summary"] == "a" * 4000


def test_mission_brief_projects_only_named_authoritative_fields(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    mission = create_mission_context(
        life_dir=tmp_path,
        mission_id="mission-brief",
        stage="change",
        objective="Do not duplicate this objective into the compact brief.",
        acceptance_check="python -m pytest tests/life/test_context_packet.py",
        execution_workdir=str(workdir),
        owns_paths=[
            "argus_skill/life/context_packet.py",
            "tests/life/test_context_packet.py",
        ],
        context_refs=[{
            "kind": "tool",
            "ref": "tools/native_check.py",
            "why": "public native verifier",
        }],
    )
    record_reviewed_handoff(
        mission_context_path=mission,
        round_index=1,
        engineer_summary="Implemented the focused behavior and ran its check.",
        review=SimpleNamespace(
            status="continue",
            reason="The focused check passed but one condition remains.",
            next_action="Exercise the public entry point.",
            operator_question="",
            frontier_report={
                "change": "artifact_improved",
                "summary": "Focused behavior now works.",
                "artifacts": ["argus_skill/life/context_packet.py"],
                "evidence": ["focused pytest"],
                "remaining_work": ["public entry-point trial"],
            },
        ),
        checkpoint_path=mission.parent / "CHECKPOINT.md",
    )
    mission_payload = json.loads(mission.read_text(encoding="utf-8"))
    mission_payload["transcript"] = "MISSION TRANSCRIPT MUST NOT APPEAR"
    mission.write_text(json.dumps(mission_payload), encoding="utf-8")

    brief = render_mission_brief(mission)

    assert brief == (
        "## MissionBrief\n"
        f"- Workdir: `{workdir}`\n"
        "- Stage: change\n"
        "- Work kind: scope\n"
        "- Owned paths (authoritative write boundary; Reviewer must not request "
        "edits outside it): argus_skill/life/context_packet.py; "
        "tests/life/test_context_packet.py\n"
        "- Changed surface: argus_skill/life/context_packet.py\n"
        "- Tools/resources: tool: tools/native_check.py (public native verifier)\n"
        "- Native check: python -m pytest tests/life/test_context_packet.py\n"
        "- Decisive result: continue: The focused check passed but one condition remains.\n"
        "- Engineer account: Implemented the focused behavior and ran its check.\n"
        "- Missing condition: public entry-point trial\n"
        "- Next action: Exercise the public entry point."
    )
    assert "objective" not in brief.lower()
    assert "mission transcript must not appear" not in brief.lower()
    assert "focused pytest" not in brief


def test_mission_brief_accepts_legacy_sparse_mission_context(tmp_path: Path) -> None:
    mission = tmp_path / "mission.json"
    mission.write_text(json.dumps({
        "kind": "mission_context",
        "execution_workdir": str(tmp_path),
        "stage": "change",
        "acceptance_check": "python -m pytest focused.py",
    }), encoding="utf-8")

    assert render_mission_brief(None) == ""
    assert render_mission_brief(mission) == (
        "## MissionBrief\n"
        f"- Workdir: `{tmp_path}`\n"
        "- Stage: change\n"
        "- Native check: python -m pytest focused.py"
    )


def _mission_brief_from_prompt(prompt: str) -> str:
    start = prompt.index("## MissionBrief")
    return prompt[start:].split("\n\n", 1)[0]


def test_public_skill_loop_ab_injects_same_brief_into_isolated_roles(
    tmp_path: Path,
) -> None:
    """A=no packet preserves old input; B gives both fresh roles identical state."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    owned_paths = ["src/public_flow.py", "tests/test_public_flow.py"]
    mission = create_mission_context(
        life_dir=tmp_path,
        mission_id="public-flow",
        stage="change",
        objective="Exercise the public SkillLoop entry point.",
        acceptance_check="reviewer returns done",
        execution_workdir=str(workdir),
        work_kind="validation",
        owns_paths=owned_paths,
    )

    def run(context_packet_path: str) -> tuple[object, list[tuple[str, str, object]]]:
        backend = MemoryBackend()
        backend.queue("engineer-r1", CannedResponse(message="public flow exercised"))
        backend.queue("reviewer", CannedResponse(message=json.dumps({
            "status": "done",
            "reason": "public flow verified",
            "next_action": "",
        })))
        outcome = SkillLoop(
            skills_dir=tmp_path / "skills",
            engineer_runner=backend,
            reviewer_runner=backend,
            config=SkillLoopConfig(
                engineer_model="memory",
                reviewer_model="memory",
                max_rounds=1,
                backend_failure_backoff_seconds=0,
                context_packet_path=context_packet_path,
                role_session_policy="fresh",
            ),
        ).run("Exercise the public SkillLoop entry point.", workdir=workdir)
        return outcome, backend.history

    old_outcome, old_history = run("")
    expected_brief = render_mission_brief(mission)
    new_outcome, new_history = run(str(mission))

    assert old_outcome.successful and new_outcome.successful
    old_prompts = [prompt for label, prompt, _options in old_history if label in {"engineer-r1", "reviewer"}]
    new_prompts = [prompt for label, prompt, _options in new_history if label in {"engineer-r1", "reviewer"}]
    assert len(old_prompts) == len(new_prompts) == 2
    assert all("## MissionBrief" not in prompt for prompt in old_prompts)
    briefs = [_mission_brief_from_prompt(prompt) for prompt in new_prompts]
    assert briefs[0] == briefs[1] == expected_brief
    assert all(prompt.count("- Work kind: validation") == 1 for prompt in new_prompts)
    reviewer_prompt = next(
        prompt for label, prompt, _options in new_history if label == "reviewer"
    )
    boundary = (
        "- Owned paths (authoritative write boundary; Reviewer must not request "
        "edits outside it): " + "; ".join(owned_paths)
    )
    assert boundary in reviewer_prompt
    assert [path for path in owned_paths if path in reviewer_prompt] == owned_paths
    assert all("TRANSCRIPT" not in prompt for prompt in new_prompts)


def test_agent_task_context_hides_host_content_hash() -> None:
    supervisor = LifeSupervisor.__new__(LifeSupervisor)
    supervisor.config = SimpleNamespace(paper_mission=False)
    item = BacklogItem.new(
        title="Inspect artifact",
        objective="Use the current artifact.",
        context_refs=[
            {
                "kind": "artifact",
                "ref": "research/RESULT.json",
                "why": "current result",
                "content_hash": "sha256:" + ("a" * 64),
            }
        ],
    )

    rendered = supervisor._render_backlog_item_metadata(item)

    assert "research/RESULT.json" in rendered
    assert "current result" in rendered
    assert "content_hash" not in rendered
    assert "sha256" not in rendered


def test_attachment_context_ref_renders_structured_metadata() -> None:
    supervisor = LifeSupervisor.__new__(LifeSupervisor)
    supervisor.config = SimpleNamespace(paper_mission=False)
    item = BacklogItem.new(
        title="Inspect upload",
        objective="Read the operator attachment.",
        context_refs=[
            {
                "kind": "attachment",
                "ref": ".argus/attachments/s-demo/att-123456789abc/brief.md",
                "why": "operator-uploaded attachment in the canonical project workdir",
                "attachment_id": "att-123456789abc",
                "original_name": "brief.md",
                "mime": "text/markdown",
                "size_bytes": "9",
                "integrity": "01234567 89abcdef 01234567 89abcdef 01234567 89abcdef 01234567 89abcdef",
            }
        ],
    )

    rendered = supervisor._render_backlog_item_metadata(item)

    assert ".argus/attachments/s-demo/att-123456789abc/brief.md" in rendered
    assert "attachment_id: att-123456789abc" in rendered
    assert "original_name: brief.md" in rendered
    assert "mime: text/markdown" in rendered
    assert "size_bytes: 9" in rendered
    assert "integrity: 01234567 89abcdef" in rendered


def test_planner_context_ref_hash_tracks_project_file_revision(tmp_path: Path) -> None:
    artifact = tmp_path / "research" / "chem_playground" / "x" / "QUESTION.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("first revision", encoding="utf-8")
    refs = [{
        "kind": "artifact",
        "ref": "research/chem_playground/x/QUESTION.md",
        "why": "candidate question",
        "content_hash": "",
    }]

    first = hydrate_task_context_refs(refs, tmp_path)
    artifact.write_text("second revision", encoding="utf-8")
    second = hydrate_task_context_refs(refs, tmp_path)

    assert first[0]["content_hash"].startswith("sha256:")
    assert second[0]["content_hash"].startswith("sha256:")
    assert first[0]["content_hash"] != second[0]["content_hash"]
    assert refs[0]["content_hash"] == ""


def test_planner_context_ref_at_project_root_is_safe_but_not_hydrated(
    tmp_path: Path,
) -> None:
    assert hydrate_task_context_refs(
        [{
            "kind": "workspace",
            "ref": "./",
            "why": "whole bounded workspace",
            "content_hash": "",
        }],
        tmp_path,
    ) == []


def test_planner_context_refs_reject_project_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes the project root"):
        hydrate_task_context_refs(
            [{
                "kind": "artifact",
                "ref": "../../outside.txt",
                "why": "unsafe",
                "content_hash": "",
            }],
            tmp_path,
        )


def test_planner_context_refs_normalize_local_absolute_and_drop_external(
    tmp_path: Path,
) -> None:
    local = tmp_path / "notes.md"
    local.write_text("grounded", encoding="utf-8")
    external = tmp_path.parent / "external-handoff.json"
    external.write_text("runtime", encoding="utf-8")

    hydrated = hydrate_task_context_refs(
        [
            {"kind": "wiki", "ref": str(local), "why": "local grounding"},
            {"kind": "handoff", "ref": str(external), "why": "runtime state"},
        ],
        tmp_path,
        discard_external=True,
    )

    assert [ref["ref"] for ref in hydrated] == ["notes.md"]
