from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.vertical_contract import VerticalLibraryContext
from argus_skill.skills.loop_skill_library import SkillLibraryMixin
from argus_skill.skills.loop_state import MissionContext
from argus_skill.team import pool, registry, task_board
from argus_skill.verticals.research.idea_portfolio import (
    DEFAULT_PORTFOLIO_SIZE,
    SELECTION_POLICY,
    TEAM_ID,
    _ensure_selection_team,
    _materialize_selection,
    ensure_idea_portfolio,
    idea_portfolio_completion_issues,
    idea_portfolio_selection,
    portfolio_required,
    portfolio_tasks,
)
from argus_skill.verticals.research.library_preparation import prepare_skill_libraries
from argus_skill.verticals.research.stages import stage_completion_issues


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


def test_source_only_selection_uses_a_new_portfolio_identity() -> None:
    assert TEAM_ID == "research-idea-pipeline-v7"
    assert SELECTION_POLICY == "fixed_twelve_source_only_v5"


def test_new_portfolio_retires_previous_policy_campaigns(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    old_team_id = "research-idea-pipeline-v6-legacy"
    old_selection_id = f"{old_team_id}-selection"
    teams = tmp_path / ".argus" / "teams"
    for team_id in (old_team_id, old_selection_id):
        root = teams / team_id
        root.mkdir(parents=True)
        pool.update(root, width=4, state="running")
        registry.write_marker(
            tmp_path,
            team_id=team_id,
            team_root=root,
            cwd=tmp_path,
            now=time.time(),
        )
    task_board.form(
        teams / old_selection_id,
        [{
            "task_id": "legacy-selector",
            "title": "Legacy selector",
            "objective": "Use probe outcomes.",
            "acceptance_check": "Write the old shared selection.",
            "role": "idea-selector",
            "owns_paths": ["research/IDEA_SELECTION.json"],
            "target": "legacy",
            "priority": 0,
        }],
    )
    assert task_board.claim_top(
        teams / old_selection_id,
        "legacy-worker",
        now=time.time(),
    ) is not None
    state = tmp_path / "research" / "IDEA_PORTFOLIO.json"
    state.parent.mkdir()
    state.write_text(
        json.dumps({
            "team_id": old_team_id,
            "selection_team_id": old_selection_id,
            "artifact_root": "research/ideation/portfolios/legacy",
            "direction_sha256": "0" * 64,
        }),
        encoding="utf-8",
    )

    new_root = ensure_idea_portfolio(tmp_path, direction="agent reliability")

    assert new_root.name.startswith(TEAM_ID)
    for team_id in (old_team_id, old_selection_id):
        retired = pool.read(teams / team_id)
        assert retired["state"] == "dissolved"
        assert retired["width"] == 0
        assert registry.marker_path(tmp_path, team_id).is_file()
    legacy_selector = task_board.snapshot(teams / old_selection_id)[0]
    assert legacy_selector["state"] == "failed"
    assert legacy_selector["reason"] == "superseded by source-only selection policy"


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
        "## Future decisive experiment",
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
    selector_owner: str = "selector",
) -> dict:
    root = _selection_root(project_root)
    selector = task_board.claim_top(root, selector_owner, now=time.time())
    assert selector is not None and selector["role"] == "idea-selector"
    selection_path = project_root / selector["owns_paths"][0]
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(
        json.dumps({
            "schema_version": 2,
            "policy": "fixed_twelve_source_only_v5",
            "route_id": selected_route["target"],
            "route_task_id": selected_route["task_id"],
            "review_task_id": selected_review["task_id"],
            "route_artifact": selected_route["owns_paths"][0],
            "review_artifact": selected_review["owns_paths"][0],
            "rationale": "Best qualitative theory, novelty, and generality.",
            "evidence_considered": "All routes and reviews available at decision time.",
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
        shard=_write_shard(root, selector_owner, selector),
    )

    return selector


def _complete_review_set(
    project_root: Path,
    root: Path,
    *,
    count: int = DEFAULT_PORTFOLIO_SIZE,
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


def test_fixed_policy_retires_legacy_selector_team(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    state = json.loads(
        (tmp_path / "research" / "IDEA_PORTFOLIO.json").read_text(encoding="utf-8")
    )
    legacy_output = f"{state['artifact_root']}/selection.json"
    legacy_root = root.with_name(f"{root.name}-selection-12")
    legacy_task = {
        "task_id": f"{root.name}-legacy-selector",
        "title": "Legacy early selector",
        "objective": "Choose before every review settles.",
        "acceptance_check": "Write one choice.",
        "role": "idea-selector",
        "owns_paths": [legacy_output],
        "target": "legacy",
        "priority": 0,
    }
    task_board.form(legacy_root, [legacy_task])
    pool.update(legacy_root, width=1, state="running")
    assert task_board.claim_top(legacy_root, "legacy-worker", now=time.time())
    state_path = tmp_path / "research" / "IDEA_PORTFOLIO.json"
    legacy_state = json.loads(state_path.read_text(encoding="utf-8"))
    legacy_state["selection_policy"] = "source_only_judgment_v4"
    legacy_state["selection_team_id"] = legacy_root.name
    state_path.write_text(json.dumps(legacy_state), encoding="utf-8")
    canonical_path = tmp_path / "research" / "IDEA_SELECTION.json"
    canonical_path.write_text('{"policy": "source_only_judgment_v4"}\n', encoding="utf-8")
    freeze_path = tmp_path / ".argus" / "IDEA_SELECTION_FREEZE.json"
    freeze_path.write_text('{"selection_sha256": "legacy"}\n', encoding="utf-8")

    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    assert pool.read(legacy_root) == {"width": 0, "state": "dissolved"}
    retired = task_board.snapshot(legacy_root)[0]
    assert retired["state"] == "failed"
    assert retired["reason"] == "superseded by source-only selection policy"
    assert not canonical_path.exists()
    assert not freeze_path.exists()

    reviewed = _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")

    state = json.loads(
        (tmp_path / "research" / "IDEA_PORTFOLIO.json").read_text(encoding="utf-8")
    )
    assert state["selection_team_id"].endswith("-selection-v2")
    selector = task_board.snapshot(_selection_root(tmp_path))[0]
    assert selector["owns_paths"][0].endswith("/selection-v2.json")
    _complete_selection(
        tmp_path,
        selected_route=reviewed[0][0],
        selected_review=reviewed[0][1],
    )
    assert idea_portfolio_completion_issues(tmp_path) == ()

    legacy_path = tmp_path / legacy_output
    legacy_path.write_text('{"policy": "obsolete"}\n', encoding="utf-8")
    task_board.complete(
        legacy_root,
        retired["task_id"],
        shard=_write_shard(legacy_root, "legacy-worker", retired),
    )
    assert idea_portfolio_completion_issues(tmp_path) == ()


def test_selector_migration_rejects_team_root_traversal(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    outside = tmp_path / "outside"
    task_board.form(
        outside,
        [{
            "task_id": "outside-selector",
            "title": "Outside selector",
            "objective": "Must not be touched.",
            "acceptance_check": "Remain active.",
            "role": "idea-selector",
            "owns_paths": ["outside.json"],
            "target": "outside",
            "priority": 0,
        }],
    )
    pool.update(outside, width=1, state="running")
    assert task_board.claim_top(outside, "outside-worker", now=time.time())
    state_path = tmp_path / "research" / "IDEA_PORTFOLIO.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["selection_team_id"] = "../../outside"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    ensure_idea_portfolio(tmp_path, direction="agent reliability")

    assert pool.read(outside) == {"width": 1, "state": "running"}
    assert task_board.snapshot(outside)[0]["state"] == "claimed"


def test_default_portfolio_has_twelve_routes_and_twelve_reviews() -> None:
    tasks = portfolio_tasks()
    assert sum(task["role"] == "idea-route" for task in tasks) == 12
    assert sum(task["role"] == "idea-review" for task in tasks) == 12
    route_task = next(task for task in tasks if task["role"] == "idea-route")
    review_task = next(task for task in tasks if task["role"] == "idea-review")
    assert "genuinely distinct" in route_task["objective"]
    assert "theory, measurement, a dataset" in route_task["objective"]
    assert "Idea selection is read-only" in route_task["objective"]
    assert "do not execute candidate code" in route_task["objective"]
    assert "negative results" in review_task["objective"]
    assert "Do not award credit for no-training convenience" in review_task["objective"]
    assert "Do not request or run an experiment during idea selection" in (
        review_task["objective"]
    )
    assert "Do not create, ensure, launch, or delegate another Team" in (
        route_task["objective"]
    )
    assert "Do not create, ensure, launch, or delegate another Team" in (
        review_task["objective"]
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
    assert "all twelve route/review pairs" in selector_task["objective"]
    assert "Selection must precede candidate execution" in selector_task["objective"]
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
    assert not unfinished_routes
    assert pool.read(root)["state"] == "draining"
    assert pool.read(selection_root)["state"] == "draining"


def test_research_gate_separates_state_root_from_project_portfolio(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_root = tmp_path / "state"
    project_root.mkdir()
    _pipeline(state_root)

    root = ensure_idea_portfolio(project_root, direction="agent reliability")
    reviewed = _complete_review_set(project_root, root)
    ensure_idea_portfolio(project_root, direction="agent reliability")
    _complete_selection(
        project_root,
        selected_route=reviewed[0][0],
        selected_review=reviewed[0][1],
    )

    state_files = sorted(
        path.relative_to(state_root).as_posix()
        for path in state_root.rglob("*")
        if path.is_file()
    )
    assert state_files == [".argus/PIPELINE_STATE.json"]
    assert stage_completion_issues(
        "research",
        project_root,
        state_root=state_root,
    ) == ()

    missing_project = tmp_path / "missing-project"
    missing_project.mkdir()
    assert stage_completion_issues(
        "research",
        missing_project,
        state_root=state_root,
    ) == ("research idea portfolio state is missing or invalid",)


def test_selector_identity_is_scoped_to_its_separate_team(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    selected_route, selected_review = reviewed[0]
    _complete_selection(
        tmp_path,
        selected_route=selected_route,
        selected_review=selected_review,
        selector_owner=str(selected_route["owner"]),
    )

    assert idea_portfolio_completion_issues(tmp_path) == ()


def test_selector_waits_for_all_twelve_route_reviews(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    _complete_review_set(
        tmp_path,
        root,
        count=DEFAULT_PORTFOLIO_SIZE - 1,
    )
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    state = json.loads(
        (tmp_path / "research" / "IDEA_PORTFOLIO.json").read_text(encoding="utf-8")
    )
    assert "selection_team_id" not in state
    assert "all twelve valid route/review pairs" in " ".join(
        idea_portfolio_completion_issues(tmp_path)
    )

    _complete_reviewed_route(tmp_path, root, prefix="twelfth")
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    state = json.loads(
        (tmp_path / "research" / "IDEA_PORTFOLIO.json").read_text(encoding="utf-8")
    )
    assert state["selection_team_id"].endswith("-selection-v2")
    assert len(state["selection_review_task_ids"]) == DEFAULT_PORTFOLIO_SIZE


def test_invalid_terminal_review_is_requeued_without_repeating_routes(
    tmp_path: Path,
) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, root)
    invalid_review = reviewed[-1][1]
    (tmp_path / invalid_review["owns_paths"][0]).write_text(
        "{}\n",
        encoding="utf-8",
    )

    ensure_idea_portfolio(tmp_path, direction="agent reliability")

    tasks = {task["task_id"]: task for task in task_board.snapshot(root)}
    assert tasks[invalid_review["task_id"]]["state"] == "pending"
    assert tasks[invalid_review["task_id"]]["attempts"] == 1
    assert all(
        task["state"] == "done"
        for task in tasks.values()
        if task["role"] == "idea-route"
    )
    _claim_complete_base(
        tmp_path,
        root,
        "review-repair",
        expected_role="idea-review",
    )
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    assert len(task_board.snapshot(_selection_root(tmp_path))) == 1


def test_invalid_terminal_selector_is_requeued(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    selection_root = _selection_root(tmp_path)
    selector = task_board.claim_top(selection_root, "bad-selector", now=time.time())
    assert selector is not None
    selection_path = tmp_path / selector["owns_paths"][0]
    selection_path.write_text("{}\n", encoding="utf-8")
    task_board.complete(
        selection_root,
        selector["task_id"],
        shard=_write_shard(selection_root, "bad-selector", selector),
    )

    ensure_idea_portfolio(tmp_path, direction="agent reliability")

    retried = task_board.snapshot(selection_root)[0]
    assert retried["state"] == "pending"
    assert retried["attempts"] == 1
    _complete_selection(
        tmp_path,
        selected_route=reviewed[0][0],
        selected_review=reviewed[0][1],
    )
    assert idea_portfolio_completion_issues(tmp_path) == ()


def test_first_valid_selection_is_frozen(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    selector = _complete_selection(
        tmp_path,
        selected_route=reviewed[0][0],
        selected_review=reviewed[0][1],
    )
    assert idea_portfolio_completion_issues(tmp_path) == ()
    canonical_path = tmp_path / "research" / "IDEA_SELECTION.json"
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))

    replacement_route, replacement_review = reviewed[1]
    selection_path = tmp_path / selector["owns_paths"][0]
    replacement = json.loads(selection_path.read_text(encoding="utf-8"))
    replacement.update({
        "route_id": replacement_route["target"],
        "route_task_id": replacement_route["task_id"],
        "review_task_id": replacement_review["task_id"],
        "route_artifact": replacement_route["owns_paths"][0],
        "review_artifact": replacement_review["owns_paths"][0],
    })
    selection_path.write_text(
        json.dumps(replacement, indent=2) + "\n",
        encoding="utf-8",
    )

    assert "conflicts with the frozen one-time decision" in " ".join(
        idea_portfolio_completion_issues(tmp_path)
    )
    assert json.loads(canonical_path.read_text(encoding="utf-8")) == canonical
    assert idea_portfolio_selection(tmp_path) == canonical
    assert (
        tmp_path / ".argus" / "IDEA_SELECTION_FREEZE.json"
    ).is_file()


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


def test_selector_can_choose_best_route_when_all_reviews_reject(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(
        tmp_path,
        root,
        verdicts=["rejected"] * DEFAULT_PORTFOLIO_SIZE,
    )
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    _complete_selection(
        tmp_path,
        selected_route=reviewed[0][0],
        selected_review=reviewed[0][1],
    )
    assert idea_portfolio_selection(tmp_path) is not None
    assert idea_portfolio_completion_issues(tmp_path) == ()


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

    assert "all twelve valid route/review pairs" in " ".join(
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
    assert not (tmp_path / ".argus" / "IDEA_SELECTION_FREEZE.json").exists()


def test_stale_old_direction_cannot_reinstall_selection(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    first = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    reviewed = _complete_review_set(tmp_path, first)
    ensure_idea_portfolio(tmp_path, direction="agent reliability")
    old_selection_root = _selection_root(tmp_path)
    _complete_selection(
        tmp_path,
        selected_route=reviewed[0][0],
        selected_review=reviewed[0][1],
    )
    stale_selection = idea_portfolio_selection(tmp_path)
    assert stale_selection is not None

    ensure_idea_portfolio(tmp_path, direction="agent memory")

    assert not _materialize_selection(
        tmp_path,
        first,
        old_selection_root,
        stale_selection,
    )
    assert not (tmp_path / "research" / "IDEA_SELECTION.json").exists()
    assert not (tmp_path / ".argus" / "IDEA_SELECTION_FREEZE.json").exists()


def test_stale_completion_cannot_restore_old_direction(tmp_path: Path) -> None:
    _pipeline(tmp_path)
    first = ensure_idea_portfolio(tmp_path, direction="agent reliability")
    old_state = json.loads(
        (tmp_path / "research" / "IDEA_PORTFOLIO.json").read_text(encoding="utf-8")
    )
    second = ensure_idea_portfolio(tmp_path, direction="agent memory")
    new_state_path = tmp_path / "research" / "IDEA_PORTFOLIO.json"
    new_state = json.loads(new_state_path.read_text(encoding="utf-8"))

    assert _ensure_selection_team(
        tmp_path,
        root=first,
        team_id=old_state["team_id"],
        artifact_root=old_state["artifact_root"],
        direction_digest=old_state["direction_sha256"],
    ) is None
    assert json.loads(new_state_path.read_text(encoding="utf-8")) == new_state
    assert pool.read(second)["state"] == "running"
    assert pool.read(first)["state"] == "dissolved"


def test_research_library_hook_forms_evidence_portfolio(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _pipeline(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_VENUE_RESEARCH", "0")
    monkeypatch.setenv("ARGUS_SKILL_IDEA_SEARCH", "1")
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
    assert events[0]["policy"] == "fixed_twelve_source_only_v5"
    assert "review_quorum" not in events[0]
    assert "formed fixed twelve-route portfolio" in events[0]["text"]
    roles = {task["role"] for task in task_board.snapshot(Path(events[0]["team_root"]))}
    assert roles == {"idea-route", "idea-review"}


def test_research_library_requires_training_and_alignment_guides_after_selection(
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
    assert "engineer/hypothesis-implementation-contract.md" in required


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
