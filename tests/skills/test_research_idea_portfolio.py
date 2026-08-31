from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.vertical_contract import VerticalLibraryContext
from argus_skill.skills.loop_skill_library import SkillLibraryMixin
from argus_skill.skills.loop_state import MissionContext
from argus_skill.team import pool, task_board
from argus_skill.verticals.research.idea_portfolio import (
    ensure_idea_portfolio,
    idea_portfolio_completion_issues,
    idea_portfolio_selection,
    late_selection_reviews,
    portfolio_required,
    portfolio_tasks,
    refresh_idea_portfolio,
)
from argus_skill.verticals.research.library_preparation import prepare_skill_libraries
from argus_skill.verticals.research.stages import (
    _late_selection_reviews_block,
    stage_completion_issues,
)


def _pipeline(root: Path, *, direction: str = "broad") -> None:
    path = root / ".argus" / "PIPELINE_STATE.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "vertical": "research",
            "current_stage": "research",
            "research_target_level": "publishable",
            "research_direction_mode": direction,
        }),
        encoding="utf-8",
    )


def test_vertical_state_root_drives_ambition_assertions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workdir = tmp_path / "worktree"
    state_root = tmp_path / "state" / "projects" / "session"
    workdir.mkdir(parents=True)
    _pipeline(state_root)
    state_path = state_root / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["current_stage"] = "run"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    seen: list[VerticalLibraryContext] = []

    class _Contract:
        def prepare_libraries(self, context: VerticalLibraryContext) -> None:
            seen.append(context)

    monkeypatch.setattr(
        "argus_skill.verticals._base.load_vertical_contract",
        lambda *_args, **_kwargs: _Contract(),
    )

    harness = SkillLibraryMixin()
    harness.config = SimpleNamespace(
        vertical_state_root=state_root,
        continuous_objective="",
        workflow_mode="staged",
        paper_mission=True,
        engineer_model=None,
    )
    harness.engineer_runner = None
    harness._emit = lambda _event: None
    mission = MissionContext(
        workdir=workdir,
        run_id="run",
        task="task",
        skill_task="discover a thesis",
        request_anchor="broad research direction",
        active_vertical="research",
        engineer_role_banner="",
        seed_thread_id=None,
        scope="bounded",
    )

    assert harness._prepare_vertical_libraries(mission) == ()
    assert len(seen) == 1
    assert seen[0].workdir == workdir
    assert seen[0].state_root == state_root
    assert seen[0].stage == "run"
    assert portfolio_required(workdir) is False
    assert portfolio_required(state_root) is True
    assert stage_completion_issues(
        "research",
        workdir,
        state_root=state_root,
    ) == ("research idea portfolio state is missing or invalid",)


def test_missing_state_root_is_visible_and_fails_closed(
    tmp_path: Path,
    caplog,
) -> None:
    state_root = tmp_path / "missing-state"

    with caplog.at_level("WARNING"):
        issues = stage_completion_issues(
            "research",
            tmp_path / "evidence",
            state_root=state_root,
        )

    assert "cannot be determined" in " ".join(issues)
    assert "PIPELINE_STATE.json is missing" in caplog.text
    assert str(state_root) in caplog.text


def test_library_policy_reads_state_root_but_writes_workdir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workdir = tmp_path / "worktree"
    state_root = tmp_path / "state" / "projects" / "session"
    workdir.mkdir(parents=True)
    _pipeline(state_root)
    monkeypatch.setenv("ARGUS_SKILL_VENUE_RESEARCH", "0")
    monkeypatch.setenv("ARGUS_SKILL_IDEA_SEARCH", "0")
    events: list[dict] = []
    required: list[str] = []

    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=workdir,
            state_root=state_root,
            stage="research",
            objective="discover a thesis",
            direction="agent reliability",
            workflow_mode="staged",
            paper_mission=True,
            team_task_id=None,
            runner=None,
            model=None,
            emit=events.append,
            required_skill_paths=required,
        )
    )

    assert required == [
        "engineer/idea-discovery.md",
        "engineer/idea-creator.md",
        "agent-team-lead.md",
    ]
    assert events[0]["type"] == "idea.portfolio.formed"
    assert Path(events[0]["team_root"]).is_relative_to(workdir)
    assert not (state_root / ".argus" / "teams").exists()


def _route_text(task: dict) -> str:
    headings = (
        "## Mechanism",
        "## Frontier search",
        "## Primary sources\nhttps://example.com/paper",
        "## Closest work",
        "## Kill argument",
        "## Faithful probe",
    )
    return f"# {task['task_id']}\n\n" + "\n\nEvidence.\n".join(headings) + "\n"


def _review_payload(task: dict, *, verdict: str) -> dict:
    payload = {
        "schema_version": 2,
        "route_id": task["target"],
        "verdict": verdict,
        "summary": f"{task['target']} independent review",
        "technical_depth": "high",
        "originality": "high",
        "theoretical_grounding": "high",
        "field_significance": "high",
        "generality": "high",
        "top_conference_case": "strong",
        "local_feasibility": "conditional",
        "contribution_mode": "well-characterized boundary result",
        "frontier_freshness": "Date-sorted search covered the latest 12 months.",
        "novelty_delta": "Introduces a new training objective absent from closest work.",
        "publication_scale_plan": (
            "Evaluate three model families, four datasets, current baselines, and seeds."
        ),
        "resource_requirements": "Stage on local GPUs, then scale to four GPUs.",
        "fatal_concerns": [] if verdict == "qualified" else ["prior art collision"],
        "probe": {},
    }
    if verdict == "qualified":
        payload["probe"] = {
            "premise": "The route's binding mechanism produces a measurable effect.",
            "evaluator_identity": "tiny public slice revision 1",
            "comparison_identity": "simple baseline revision 1",
            "minimum_signal": "one honest mechanism observation",
            "stop_rules": "record one bounded observation, then continue",
        }
    return payload


def _write_shard(root: Path, owner: str, task: dict) -> str:
    shard = root / "shards" / f"{owner}.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_text(
        json.dumps({
            "member_id": owner,
            "task_id": task["task_id"],
            "success": True,
        }) + "\n",
        encoding="utf-8",
    )
    return str(shard)


def _claim_complete_base(
    project_root: Path,
    root: Path,
    owner: str,
    *,
    expected_role: str,
    review_verdict: str = "qualified",
) -> dict:
    task = task_board.claim_top(root, owner, now=time.time())
    assert task is not None
    assert task["role"] == expected_role
    output = project_root / task["owns_paths"][0]
    output.parent.mkdir(parents=True, exist_ok=True)
    if expected_role == "idea-route":
        output.write_text(_route_text(task), encoding="utf-8")
    else:
        output.write_text(
            json.dumps(
                _review_payload(task, verdict=review_verdict),
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    task_board.complete(root, task["task_id"], shard=_write_shard(root, owner, task))
    return task


def _complete_reviewed_route(
    project_root: Path,
    root: Path,
    *,
    prefix: str,
    review_verdict: str = "qualified",
) -> tuple[dict, dict]:
    route = _claim_complete_base(
        project_root,
        root,
        f"{prefix}-route",
        expected_role="idea-route",
    )
    review = _claim_complete_base(
        project_root,
        root,
        f"{prefix}-review",
        expected_role="idea-review",
        review_verdict=review_verdict,
    )
    return route, review


def _selection_root(project_root: Path) -> Path:
    state = json.loads(
        (project_root / "research" / "IDEA_PORTFOLIO.json").read_text(
            encoding="utf-8"
        )
    )
    return project_root / ".argus" / "teams" / state["selection_team_id"]


def _complete_selection(
    project_root: Path,
    *,
    selected_route: dict,
    selected_review: dict,
) -> dict:
    root = _selection_root(project_root)
    selector = task_board.claim_top(root, "selector", now=time.time())
    assert selector is not None and selector["role"] == "idea-selector"
    selection_path = project_root / selector["owns_paths"][0]
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(
        json.dumps({
            "schema_version": 2,
            "policy": "evidence_judgment_v3",
            "route_id": selected_route["target"],
            "route_task_id": selected_route["task_id"],
            "review_task_id": selected_review["task_id"],
            "route_artifact": selected_route["owns_paths"][0],
            "review_artifact": selected_review["owns_paths"][0],
            "rationale": "Best qualitative theory, novelty, and generality.",
            "evidence_considered": "All routes, reviews, and probes available at decision time.",
            "theory_strength": "high",
            "novelty": "high",
            "generality": "high",
            "top_conference_case": "strong",
            "frontier_freshness": "Latest 12 months and current venue cycle checked.",
            "novelty_delta": "A new training objective with a nontrivial mechanism.",
            "publication_scale_plan": (
                "Three model families, four datasets, strongest baselines, and seeds."
            ),
            "resource_requirements": "Four-GPU staged training and evaluation.",
            "unresolved_risks": ["implementation details will evolve"],
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    task_board.complete(
        root,
        selector["task_id"],
        shard=_write_shard(root, "selector", selector),
    )

    return selector


def _complete_review_set(
    project_root: Path,
    root: Path,
    *,
    count: int = 3,
    verdicts: list[str] | None = None,
) -> list[tuple[dict, dict]]:
    verdicts = verdicts or ["qualified"] * count
    return [
        _complete_reviewed_route(
            project_root,
            root,
            prefix=f"candidate-{index:02d}",
            review_verdict=verdict,
        )
        for index, verdict in enumerate(verdicts, 1)
    ]


def test_portfolio_size_is_an_operating_choice_not_a_selection_quota(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")

    custom = portfolio_tasks(portfolio_size=4)
    assert sum(task["role"] == "idea-route" for task in custom) == 4
    assert sum(task["role"] == "idea-review" for task in custom) == 4
    route_task = next(
        task for task in task_board.snapshot(root) if task["role"] == "idea-route"
    )
    review_task = next(
        task for task in task_board.snapshot(root) if task["role"] == "idea-review"
    )
    assert "genuinely distinct" in route_task["objective"]
    assert "theory, measurement, a dataset" in route_task["objective"]
    assert "negative results" in review_task["objective"]
    assert "Do not award credit for no-training convenience" in review_task["objective"]
    assert "Do not create, ensure, launch, or delegate another Team" in (
        route_task["objective"]
    )
    assert "Do not create, ensure, launch, or delegate another Team" in (
        review_task["objective"]
    )
    _complete_reviewed_route(tmp_path, root, prefix="candidate", review_verdict="rejected")
    ensure_idea_portfolio(tmp_path, direction="agent reliability")

    assert not (tmp_path / "research" / "IDEA_SELECTION.json").exists()
    assert "no qualified independent review" in " ".join(
        idea_portfolio_completion_issues(tmp_path)
    )


def test_evidence_selector_can_choose_best_not_earliest(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")

    selection_root = _selection_root(tmp_path)
    assert len(task_board.snapshot(selection_root)) == 1
    assert all(task["timeout_s"] == 0.0 for task in task_board.snapshot(selection_root))
    selector_task = next(
        task
        for task in task_board.snapshot(selection_root)
        if task["role"] == "idea-selector"
    )
    assert "all other relevant evidence" in selector_task["objective"]
    assert "including probes and later routes" in selector_task["objective"]
    assert "important, credible, nontrivial new knowledge" in selector_task["objective"]
    assert "Do not create, ensure, launch, or delegate another Team" in (
        selector_task["objective"]
    )
    selected_route, selected_review = reviewed[-1]
    _complete_selection(
        tmp_path,
        selected_route=selected_route,
        selected_review=selected_review,
    )

    assert idea_portfolio_completion_issues(tmp_path) == ()
    assert stage_completion_issues("research", tmp_path) == ()
    selection = idea_portfolio_selection(tmp_path)
    assert selection is not None
    assert selection["route_task_id"] == selected_route["task_id"]
    unfinished_routes = [
        task
        for task in task_board.snapshot(root)
        if task["role"] == "idea-route" and task["state"] != "done"
    ]
    assert unfinished_routes
    assert pool.read(root)["state"] == "running"
    assert pool.read(selection_root)["state"] == "draining"


def test_late_routes_remain_claimable_and_reach_reviewer_once_settled(
    tmp_path: Path,
) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    _complete_selection(
        tmp_path,
        selected_route=reviewed[0][0],
        selected_review=reviewed[0][1],
    )

    # Research is already free to proceed while other routes remain claimable.
    assert idea_portfolio_completion_issues(tmp_path) == ()
    assert pool.read(root)["state"] == "running"
    late = [
        _complete_reviewed_route(
            tmp_path,
            root,
            prefix=f"late-{index}",
        )
        for index in range(2)
    ]
    refresh_idea_portfolio(tmp_path)

    rows = late_selection_reviews(tmp_path)
    assert {row["route_task_id"] for row in rows} == {
        pair[0]["task_id"] for pair in late
    }
    block = _late_selection_reviews_block(tmp_path)
    assert "settled after the original selector" in block
    assert "plan_signal=reconsider" in block
    assert pool.read(root)["state"] == "running"

    # Refresh is idempotent and never rewrites the original selection.
    before = (tmp_path / "research" / "IDEA_SELECTION.json").read_bytes()
    refresh_idea_portfolio(tmp_path)
    assert (tmp_path / "research" / "IDEA_SELECTION.json").read_bytes() == before


def test_selection_record_has_no_post_selection_probe_gate(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    selection_root = _selection_root(tmp_path)
    selection = task_board.snapshot(selection_root)
    assert [task["role"] for task in selection] == ["idea-selector"]


def test_unenumerated_contribution_form_can_be_selected(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    selected_route, selected_review = reviewed[-1]

    _complete_selection(
        tmp_path,
        selected_route=selected_route,
        selected_review=selected_review,
    )

    selection = idea_portfolio_selection(tmp_path)
    assert selection is not None
    assert selection["route_task_id"] == selected_route["task_id"]
    assert idea_portfolio_completion_issues(tmp_path) == ()


def test_selector_may_choose_credible_late_evidence(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    _complete_review_set(tmp_path, root, count=1)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    late_route, late_review = _complete_reviewed_route(tmp_path, root, prefix="late")

    _complete_selection(
        tmp_path,
        selected_route=late_route,
        selected_review=late_review,
    )

    selection = idea_portfolio_selection(tmp_path)
    assert selection is not None
    assert selection["route_task_id"] == late_route["task_id"]
    assert idea_portfolio_completion_issues(tmp_path) == ()


def test_selection_waits_for_a_qualified_review(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    _complete_review_set(tmp_path, root, verdicts=["rejected"] * 3)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    assert "no qualified independent review" in " ".join(
        idea_portfolio_completion_issues(tmp_path)
    )

    qualified = _complete_reviewed_route(
        tmp_path,
        root,
        prefix="late-qualified",
        review_verdict="qualified",
    )
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    state = json.loads(
        (tmp_path / "research" / "IDEA_PORTFOLIO.json").read_text(encoding="utf-8")
    )
    assert qualified[1]["task_id"] in state["selection_review_task_ids"]


def test_invalid_selection_provenance_blocks_stage(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    selected_route, selected_review = reviewed[0]
    _complete_selection(
        tmp_path,
        selected_route=selected_route,
        selected_review=selected_review,
    )
    (tmp_path / selected_route["owns_paths"][0]).write_text(
        "Evidence.\n",
        encoding="utf-8",
    )

    assert "adversarial selection is still incomplete" in " ".join(
        idea_portfolio_completion_issues(tmp_path)
    )


def test_locked_hypothesis_does_not_require_portfolio(tmp_path: Path) -> None:
    _pipeline(tmp_path, direction="locked")
    assert idea_portfolio_completion_issues(tmp_path) == ()


def test_new_direction_gets_new_pipeline_and_clears_selection(
    tmp_path: Path,
) -> None:
    _pipeline(tmp_path)
    first = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, first)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    _complete_selection(
        tmp_path,
        selected_route=reviewed[0][0],
        selected_review=reviewed[0][1],
    )
    assert idea_portfolio_completion_issues(tmp_path) == ()

    second = ensure_idea_portfolio(tmp_path, direction="agent memory")

    assert second != first
    assert not (tmp_path / "research" / "IDEA_SELECTION.json").exists()


def test_research_library_hook_forms_evidence_portfolio(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _pipeline(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_VENUE_RESEARCH", "0")
    monkeypatch.setenv("ARGUS_SKILL_IDEA_SEARCH", "0")
    events: list[dict] = []
    required: list[str] = []

    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=tmp_path,
            state_root=tmp_path,
            stage="research",
            objective="discover a thesis",
            direction="agent reliability",
            workflow_mode="staged",
            paper_mission=True,
            team_task_id=None,
            runner=None,
            model=None,
            emit=events.append,
            required_skill_paths=required,
        )
    )

    assert required == [
        "engineer/idea-discovery.md",
        "engineer/idea-creator.md",
        "agent-team-lead.md",
    ]
    assert events[0]["type"] == "idea.portfolio.formed"
    assert events[0]["policy"] == "evidence_judgment_v3"
    assert "review_quorum" not in events[0]
    assert "breadth and selection sufficiency remain Agent judgments" in events[0]["text"]
    roles = {task["role"] for task in task_board.snapshot(Path(events[0]["team_root"]))}
    assert roles == {"idea-route", "idea-review"}


def test_research_library_requires_training_guide_after_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _pipeline(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_VENUE_RESEARCH", "0")
    monkeypatch.setenv("ARGUS_SKILL_IDEA_SEARCH", "0")
    required: list[str] = []

    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=tmp_path,
            state_root=tmp_path,
            stage="plan",
            objective="design current-model experiments",
            direction="locked",
            workflow_mode="staged",
            paper_mission=True,
            team_task_id=None,
            runner=None,
            model=None,
            emit=lambda _event: None,
            required_skill_paths=required,
        )
    )
    assert "engineer/training-infrastructure-guide.md" in required
    assert "engineer/training-infrastructure-guide.md" in required


def test_research_library_hook_never_recurses_inside_team_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _pipeline(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_VENUE_RESEARCH", "1")
    monkeypatch.setenv("ARGUS_SKILL_IDEA_SEARCH", "1")
    events: list[dict] = []
    required: list[str] = []

    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=tmp_path,
            state_root=tmp_path,
            stage="research",
            objective="investigate one assigned route",
            direction="route-01 mechanism",
            workflow_mode="staged",
            paper_mission=True,
            team_task_id="parent-route-01",
            runner=None,
            model=None,
            emit=events.append,
            required_skill_paths=required,
        )
    )

    assert required == [
        "engineer/idea-discovery.md",
        "engineer/idea-creator.md",
    ]
    assert events == [{
        "type": "idea.portfolio.nested_skipped",
        "team_task_id": "parent-route-01",
        "text": "team worker reused the parent portfolio without recursive fanout",
    }]
    assert not (tmp_path / ".argus" / "teams").exists()


def test_direct_nested_portfolio_formation_fails_before_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _pipeline(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "parent-route-01")

    with pytest.raises(RuntimeError, match="nested idea portfolio formation"):
        ensure_idea_portfolio(tmp_path, direction="route-local direction")

    assert not (tmp_path / ".argus" / "teams").exists()
