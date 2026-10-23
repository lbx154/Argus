from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.life.context_packet import (
    create_mission_context,
    record_engineer_handoff,
    record_reviewed_handoff,
    render_mission_brief,
    render_mission_contract,
)
from argus.life.memory import BacklogItem
from argus.life.supervisor import LifeSupervisor
from tests.hand_off import hand_off


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
        "edits outside it): argus/life/context_packet.py; "
        "tests/life/test_context_packet.py\n"
        "- Tools/resources: tool: tools/native_check.py (public native verifier)\n"
        "- Native check: python -m pytest tests/life/test_context_packet.py\n"
        "- Last review: continue: The focused check passed but one condition remains.\n"
        "- Reviewed Engineer work: Implemented the focused behavior and ran its check.\n"
        "- Next action: Exercise the public entry point."
    )
    assert "objective" not in brief.lower()
    assert "mission transcript must not appear" not in brief.lower()


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


def test_mission_brief_uses_newest_review_after_round_numbers_restart(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path, mission_id="restarted", stage="review", objective="Improve the paper",
    )
    for round_index, created_at, marker in ((9, 100.0, "OLD_REVIEW"), (1, 200.0, "CURRENT_REVIEW")):
        path = record_reviewed_handoff(
            mission_context_path=mission, round_index=round_index,
            engineer_summary=marker + "_WORK",
            review=SimpleNamespace(status="continue", reason=marker, next_action=marker + "_ACTION"),
            checkpoint_path=mission.parent / "CHECKPOINT.md",
        )
        payload = json.loads(path.read_text())
        payload["created_at"] = created_at
        path.write_text(json.dumps(payload))

    brief = render_mission_brief(mission)
    assert "CURRENT_REVIEW" in brief
    assert "OLD_REVIEW" not in brief


def test_a_new_run_keeps_the_earlier_runs_round_records(tmp_path: Path) -> None:
    def review(marker: str) -> SimpleNamespace:
        return SimpleNamespace(status="continue", reason=marker, next_action=marker)

    first = create_mission_context(
        life_dir=tmp_path, mission_id="rerun", stage="review", objective="First objective",
    )
    record_engineer_handoff(
        mission_context_path=first, round_index=1, engineer_summary="RUN1_WORK",
        checkpoint_path=None,
    )
    first_review = record_reviewed_handoff(
        mission_context_path=first, round_index=1, engineer_summary="RUN1_WORK",
        review=review("RUN1_REVIEW"), checkpoint_path=None,
    )
    second = create_mission_context(
        life_dir=tmp_path, mission_id="rerun", stage="review", objective="Requeued objective",
    )
    engineer = record_engineer_handoff(
        mission_context_path=second, round_index=1, engineer_summary="RUN2_WORK",
        checkpoint_path=None,
    )
    second_review = record_reviewed_handoff(
        mission_context_path=second, round_index=1, engineer_summary="RUN2_WORK",
        review=review("RUN2_REVIEW"), checkpoint_path=None,
    )

    assert second == first
    assert json.loads(second.read_text())["run"] == 2
    assert first_review == second.parent / "round-0001.json"
    assert second_review == second.parent / "round-0001-run-02.json"
    assert engineer == second.parent / "round-0001-run-02-engineer.json"
    assert json.loads(first_review.read_text())["review"]["reason"] == "RUN1_REVIEW"
    assert (second.parent / "round-0001-engineer.json").is_file()
    assert json.loads(second_review.read_text())["run"] == 2
    assert "Last review: continue: RUN2_REVIEW" in render_mission_brief(second)


def test_pending_engineer_work_is_not_replaced_by_an_older_reviewed_account(tmp_path: Path) -> None:
    mission = create_mission_context(
        life_dir=tmp_path, mission_id="pending-review", stage="review", objective="Improve the paper",
    )
    record_reviewed_handoff(
        mission_context_path=mission, round_index=1, engineer_summary="OLD_ENGINEER_ACCOUNT",
        review=SimpleNamespace(status="continue", reason="PRIOR_FINDING", next_action="PRIOR_REPAIR"),
        checkpoint_path=mission.parent / "CHECKPOINT.md",
    )
    current = record_engineer_handoff(
        mission_context_path=mission, round_index=2, engineer_summary="CURRENT_SCIENTIFIC_WORK",
        checkpoint_path=mission.parent / "CHECKPOINT.md",
    )
    brief = render_mission_brief(mission)
    assert "Previous review: continue: PRIOR_FINDING" in brief
    assert "Previously requested action: PRIOR_REPAIR" in brief
    assert f"Latest work awaiting review: `{current}`" in brief
    assert "Unreviewed Engineer work: CURRENT_SCIENTIFIC_WORK" in brief
    assert "OLD_ENGINEER_ACCOUNT" not in brief
    assert "CURRENT_SCIENTIFIC_WORK" not in render_mission_brief(
        mission, include_engineer_account=False,
    )


def test_refresh_rebinds_initial_contract_without_overwriting_checkpoint_or_handoff(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.life import context_packet

    args = dict(life_dir=tmp_path, mission_id="strong-target", stage="review")
    mission = create_mission_context(**args, objective="Old drawing route", acceptance_check="weak accept")
    checkpoint = mission.parent / "CHECKPOINT.md"
    checkpoint.write_text("Existing scientific evidence")
    handoff = record_engineer_handoff(
        mission_context_path=mission, round_index=3, engineer_summary="Ready for independent review",
        checkpoint_path=checkpoint,
    )
    handoff_before = handoff.read_bytes()
    created_at = json.loads(mission.read_text())["created_at"]
    write = context_packet._atomic_write_json

    def no_latest_rewrite(path, payload):
        assert path != mission.parent / "latest.json"
        return write(path, payload)

    monkeypatch.setattr(context_packet, "_atomic_write_json", no_latest_rewrite)

    create_mission_context(**args, objective="Science first; editable PPT", acceptance_check="strong accept")

    assert json.loads(mission.read_text())["acceptance_check"] == "strong accept"
    assert checkpoint.read_text() == "Existing scientific evidence"
    assert handoff.read_bytes() == handoff_before
    assert json.loads((mission.parent / "latest.json").read_text())["handoff"]["path"] == str(handoff)
    assert json.loads(mission.read_text())["created_at"] == created_at


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


def _mission_brief_from_prompt(prompt: str) -> str:
    start = prompt.index("## MissionBrief")
    return prompt[start:].split("\n\n", 1)[0]


def test_public_skill_loop_brief_preserves_contract_and_advances_current_work(
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
        owns_paths=owned_paths,
    )

    def run(context_packet_path: str) -> tuple[object, list[tuple[str, str, object]]]:
        backend = MemoryBackend()
        backend.queue("engineer-r1", CannedResponse(message="public flow exercised"))
        backend.queue("reviewer", CannedResponse(action=hand_off("done", 'public flow verified')))
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
    assert briefs[0] == expected_brief
    assert briefs[1].startswith(expected_brief + "\n")
    assert "Latest work awaiting review" in briefs[1]
    assert "Unreviewed Engineer work" not in briefs[1]
    reviewer_prompt = next(
        prompt for label, prompt, _options in new_history if label == "reviewer"
    )
    boundary = (
        "- Owned paths (authoritative write boundary; Reviewer must not request "
        "edits outside it): " + "; ".join(owned_paths)
    )
    assert boundary in reviewer_prompt
    assert reviewer_prompt.count("public flow exercised") == 1
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
                "content_hash": "[4096, 1760000000000000000, 1760000000000000000]",
            }
        ],
    )

    rendered = supervisor._render_backlog_item_metadata(item)

    assert "research/RESULT.json" in rendered
    assert "current result" in rendered
    assert "content_hash" not in rendered
    assert "1760000000000000000" not in rendered


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


