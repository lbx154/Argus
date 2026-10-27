from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus.life.context_packet import (
    create_mission_context,
    record_engineer_handoff,
    record_reviewed_handoff,
    render_mission_brief,
    render_mission_contract,
)
from argus.life.memory import BacklogItem
from argus.life.supervisor import LifeSupervisor


def test_context_packet_seals_engineer_and_reviewer_handoffs(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path,
        mission_id="mission-1",
        stage="research",
        scope="bounded",
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
    assert "work_kind" not in mission_payload
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
    assert "frontier" not in mission_payload
    assert not (mission.parent / "frontier.json").exists()
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
    }
    assert reviewed_payload["engineer_summary"] == "Created the screen packet."
    assert "text" not in reviewed_payload["checkpoint"]
    rendered = render_mission_contract(mission)
    assert rendered.count("Screen one candidate on public tasks.") == 1
    assert "Acceptance:" in rendered
    assert "- do not preregister" in rendered
    assert "content_hash" not in rendered


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
            "argus/life/context_packet.py",
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
        "- Owned paths (authoritative write boundary; Reviewer must not request "
        "edits outside it; Argus's Skill and Wiki directories and "
        "`.argus/evidence/` are always writable): argus/life/context_packet.py; "
        "tests/life/test_context_packet.py\n"
        "- Tools/resources: tool: tools/native_check.py (public native verifier)\n"
        "- Native check: python -m pytest tests/life/test_context_packet.py\n"
        "- Last review: continue: The focused check passed but one condition remains.\n"
        "- Reviewed Engineer work: Implemented the focused behavior and ran its check.\n"
        "- Next action: Exercise the public entry point."
    )
    assert "objective" not in brief.lower()
    assert "mission transcript must not appear" not in brief.lower()


def test_stale_latest_reference_does_not_reopen_reviewed_work(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path, mission_id="already-reviewed", stage="review", objective="Improve the paper",
    )
    record_engineer_handoff(
        mission_context_path=mission, round_index=1, engineer_summary="Completed change",
        checkpoint_path=mission.parent / "CHECKPOINT.md",
    )
    old_reference = (mission.parent / "latest.json").read_bytes()
    record_reviewed_handoff(
        mission_context_path=mission, round_index=1, engineer_summary="Completed change",
        review=SimpleNamespace(status="continue", reason="Current assessment", next_action="New experiment"),
        checkpoint_path=mission.parent / "CHECKPOINT.md",
    )
    (mission.parent / "latest.json").write_bytes(old_reference)

    brief = render_mission_brief(mission)
    assert "Last review: continue: Current assessment" in brief
    assert "Reviewed Engineer work: Completed change" in brief
    assert "awaiting review" not in brief


def test_invalid_optional_work_reference_does_not_break_the_brief(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path, mission_id="bad-reference", stage="review",
        objective="Current goal", acceptance_check="Current standard",
    )
    (mission.parent / "latest.json").write_text(json.dumps({
        "kind": "handoff_ref", "handoff": {"path": "bad\u0000path"},
    }))
    brief = render_mission_brief(mission)
    assert "Current standard" in brief
    assert "awaiting review" not in brief


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
