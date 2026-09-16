from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from argus.core.vertical_contract import VerticalLibraryContext
from argus.skills.loop_skill_library import SkillLibraryMixin
from argus.skills.loop_state import MissionContext
from argus.skills.vertical_select import reset_stage_for_new_intent
from argus.team import task_board
from argus.verticals.research.idea_portfolio import (
    SELECTION_POLICY,
    TEAM_ID,
    ensure_idea_portfolio,
    idea_portfolio_completion_issues,
    idea_portfolio_selection,
    portfolio_route_count,
    portfolio_size,
    portfolio_tasks,
)
from argus.verticals.research.library_preparation import (
    prepare_skill_libraries,
)
from argus.verticals.research.stages import planner_task_issues


def _state(root: Path) -> None:
    path = root / ".argus" / "PIPELINE_STATE.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "vertical": "research",
            "current_stage": "idea",
            "research_target_level": "publishable",
            "research_direction_mode": "broad",
            "selected_idea": None,
            "current_verdict": "in_progress",
            "next_action": "select",
        }),
        encoding="utf-8",
    )


def _shard(root: Path, owner: str, task: dict) -> str:
    path = root / "shards" / f"{task['task_id']}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "member_id": owner,
            "task_id": task["task_id"],
            "success": True,
        }) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _complete_routes_and_reviews(
    project: Path,
    root: Path,
    *,
    first_owner: str = "",
) -> list[dict]:
    routes: list[dict] = []
    index = 0
    while task := task_board.claim_top(
        root,
        first_owner if index == 0 and first_owner else f"worker-{index:02d}",
        now=time.time(),
    ):
        owner = first_owner if index == 0 and first_owner else f"worker-{index:02d}"
        index += 1
        output = project / task["owns_paths"][0]
        output.parent.mkdir(parents=True, exist_ok=True)
        if task["role"] == "idea-route":
            output.write_text(
                f"# {task['target']}\nhttps://example.org/primary\n",
                encoding="utf-8",
            )
            routes.append(task)
        else:
            output.write_text(
                json.dumps({
                    "schema_version": 2,
                    "route_id": task["target"],
                    "verdict": "qualified",
                    "summary": "Plausible, but not the strongest route.",
                    "fatal_concerns": [],
                }),
                encoding="utf-8",
            )
        task_board.complete(
            root,
            task["task_id"],
            shard=_shard(root, owner, task),
        )
    return routes


def _complete_selector(
    project: Path,
    selected: dict,
    *,
    owner: str = "selector",
    long_text: str = "",
) -> Path:
    state = json.loads(
        (project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    root = project / ".argus" / "teams" / state["idea_portfolio"]["selection_team_id"]
    task = task_board.claim_top(root, owner, now=time.time())
    assert task is not None
    output = project / task["owns_paths"][0]
    output.parent.mkdir(parents=True, exist_ok=True)
    rationale = long_text or "Strongest mechanism and decisive future test."
    output.write_text(
        json.dumps({
            "schema_version": 3,
            "policy": SELECTION_POLICY,
            "route_id": selected["target"],
            "route_task_id": selected["task_id"],
            "review_task_id": f"{selected['task_id']}-review",
            "route_artifact": selected["owns_paths"][0],
            "review_artifact": (
                selected["owns_paths"][0]
                .replace("/routes/", "/reviews/")
                .replace(".md", ".json")
            ),
            "rationale": rationale,
            "evidence_considered": long_text or "All route/review pairs.",
            "resource_requirements": long_text or "One controlled campaign.",
            "unresolved_risks": [long_text or "Scale"] * 20,
            "rejections": {
                f"route-{index:02d}": long_text or "Weaker direct case."
                for index in range(1, portfolio_size() + 1)
                if f"route-{index:02d}" != selected["target"]
            },
        }),
        encoding="utf-8",
    )
    task_board.complete(root, task["task_id"], shard=_shard(root, owner, task))
    return output


def test_portfolio_defaults_to_three_source_only_routes_plus_reviews() -> None:
    tasks = portfolio_tasks()

    assert portfolio_size() == 3
    assert sum(task["role"] == "idea-route" for task in tasks) == 3
    assert sum(task["role"] == "idea-review" for task in tasks) == 3
    assert all(task["owns_paths"][0].startswith(".argus/teams/") for task in tasks)
    text = " ".join(task["objective"] for task in tasks).lower()
    assert "do not execute candidate code" in text
    assert "request an experiment during selection" in text


def test_portfolio_size_is_an_operator_setting_with_bounds(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_ROUTES", "5")
    tasks = portfolio_tasks()
    assert sum(task["role"] == "idea-route" for task in tasks) == 5
    selector = [task for task in tasks if task["role"] == "idea-route"]
    assert selector[-1]["target"] == "route-05"
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_ROUTES", "40")
    assert portfolio_size() == 12
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_ROUTES", "1")
    assert portfolio_size() == 2
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_ROUTES", "many")
    assert portfolio_size() == 3


def test_a_portfolio_already_on_disk_keeps_its_route_count(tmp_path: Path, monkeypatch) -> None:
    """Lowering the default must not re-form a live twelve-route team."""
    _state(tmp_path)
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_ROUTES", "12")
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
    assert portfolio_route_count(root) == 12
    first = task_board.snapshot(root)

    monkeypatch.delenv("ARGUS_RESEARCH_PORTFOLIO_ROUTES")
    assert ensure_idea_portfolio(tmp_path, direction="reliable agents") == root
    assert portfolio_route_count(root) == 12
    assert [task["task_id"] for task in task_board.snapshot(root)] == [
        task["task_id"] for task in first
    ]
    # ...and it still completes with twelve rejections, not the new default.
    routes = _complete_routes_and_reviews(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_ROUTES", "12")
    _complete_selector(tmp_path, routes[0])
    monkeypatch.delenv("ARGUS_RESEARCH_PORTFOLIO_ROUTES")
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    assert idea_portfolio_completion_issues(tmp_path) == ()
    selected = idea_portfolio_selection(tmp_path)
    assert selected is not None and len(selected["rejections"]) == 11


def test_selector_does_not_exist_until_all_route_reviews_finish(
    tmp_path: Path,
) -> None:
    _state(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
    task = task_board.claim_top(root, "w1", now=time.time())
    assert task is not None
    output = tmp_path / task["owns_paths"][0]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("https://example.org/primary", encoding="utf-8")
    task_board.complete(root, task["task_id"], shard=_shard(root, "w1", task))

    ensure_idea_portfolio(tmp_path, direction="reliable agents")

    payload = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert "selection_team_id" not in payload["idea_portfolio"]
    assert idea_portfolio_selection(tmp_path) is None


def test_team_local_owner_ids_and_compact_handoff(tmp_path: Path) -> None:
    from argus.life.supervisor._planning_cycle_enqueue import (
        _automatic_stage_target,
    )
    from argus.verticals.research.stages import CHECKLIST_STAGE_ORDER

    _state(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
    assert _automatic_stage_target(
        state_root=tmp_path, evidence_root=tmp_path,
    ) == ""
    routes = _complete_routes_and_reviews(tmp_path, root, first_owner="w1")
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    _complete_selector(
        tmp_path,
        routes[-1],
        owner="w1",
        long_text="evidence " * 4000,
    )
    ensure_idea_portfolio(tmp_path, direction="reliable agents")

    assert idea_portfolio_completion_issues(tmp_path) == ()
    assert _automatic_stage_target(
        state_root=tmp_path, evidence_root=tmp_path,
    ) == CHECKLIST_STAGE_ORDER[1]
    selected = idea_portfolio_selection(tmp_path)
    assert selected is not None
    assert "winner_detail" not in selected
    notes = (tmp_path / "RESEARCH_NOTES.md").read_text(encoding="utf-8")
    assert notes.startswith("# Research notes — Idea stage\n")
    assert notes.count("\n- **route-") == portfolio_size() - 1
    assert ("evidence " * 4000).strip() in notes


def test_first_valid_selection_remains_authoritative(tmp_path: Path) -> None:
    _state(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
    routes = _complete_routes_and_reviews(tmp_path, root)
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    output = _complete_selector(tmp_path, routes[0])
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    first = idea_portfolio_selection(tmp_path)

    replacement = json.loads(output.read_text(encoding="utf-8"))
    replacement["route_id"] = routes[1]["target"]
    replacement["route_task_id"] = routes[1]["task_id"]
    output.write_text(json.dumps(replacement), encoding="utf-8")

    assert idea_portfolio_selection(tmp_path) == first


def test_new_intent_uses_a_fresh_generation_and_cannot_import_legacy(
    tmp_path: Path,
) -> None:
    _state(tmp_path)
    legacy = tmp_path / "research" / "IDEA_SELECTION.json"
    legacy.parent.mkdir()
    legacy.write_text(
        json.dumps({
            "route_id": "route-03",
            "rationale": "stale winner",
            "evidence_considered": "old evidence",
            "resource_requirements": "old resources",
        }),
        encoding="utf-8",
    )

    assert reset_stage_for_new_intent(
        tmp_path,
        old_vertical="research",
        new_vertical="research",
        force_replacement=True,
        evidence_root=tmp_path,
    )
    root = ensure_idea_portfolio(tmp_path, direction="new objective")
    payload = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )

    assert root.name == f"{TEAM_ID}-g2"
    assert payload["research_intent_generation"] == 2
    assert payload["legacy_selection_consumed"] is True
    assert payload["selected_idea"] is None


def test_split_state_root_keeps_team_artifacts_in_the_workdir(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _state(state)

    root = ensure_idea_portfolio(
        workdir,
        direction="reliable agents",
        state_root=state,
    )

    assert root.is_relative_to(workdir / ".argus" / "teams")
    assert (state / ".argus" / "PIPELINE_STATE.json").is_file()
    assert not (state / ".argus" / "teams").exists()


def test_direct_idea_only_research_does_not_prepare_a_paper_portfolio(
    tmp_path: Path,
) -> None:
    _state(tmp_path)
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["workflow_mode"] = "direct"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    required: list[str] = []

    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=tmp_path,
            state_root=tmp_path,
            stage="idea",
            objective="select one strong idea",
            direction="reliable agents",
            workflow_mode="direct",
            paper_mission=True,
            team_task_id=None,
            runner=None,
            model=None,
            emit=lambda _event: None,
            required_skill_paths=required,
        )
    )

    assert required == ["research-idea-playbook.md"]
    root = tmp_path / ".argus" / "teams" / f"{TEAM_ID}-g1"
    assert not root.exists()
    assert idea_portfolio_completion_issues(tmp_path) == ()


def test_preparation_exposes_the_only_canonical_portfolio_to_engineer(
    tmp_path: Path,
) -> None:
    _state(tmp_path)
    prompt_blocks: list[str] = []

    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=tmp_path,
            state_root=tmp_path,
            stage="idea",
            objective="select one strong idea",
            direction="reliable agents",
            workflow_mode="staged",
            paper_mission=True,
            team_task_id=None,
            runner=None,
            model=None,
            emit=lambda _event: None,
            prompt_blocks=prompt_blocks,
        )
    )

    assert len(prompt_blocks) == 1
    assert f".argus/teams/{TEAM_ID}-g1" in prompt_blocks[0]
    assert "only authorized Idea portfolio" in prompt_blocks[0]
    assert "Do not call `team form`" in prompt_blocks[0]


def test_skill_library_state_includes_vertical_prompt_blocks() -> None:
    class Harness(SkillLibraryMixin):
        engineer_mission = SimpleNamespace(
            libraries=lambda **_kwargs: SimpleNamespace(block="library index")
        )
        reviewer = SimpleNamespace(
            mission=SimpleNamespace(
                libraries=lambda: SimpleNamespace(block="reviewer index")
            )
        )

        def _prepare_vertical_libraries(
            self,
            _mission: MissionContext,
        ) -> tuple[tuple[str, ...], tuple[str, ...]]:
            return (("required.md",), ("runtime-owned portfolio",))

    state = Harness()._prepare_skill_libraries(
        MissionContext(
            workdir=Path("/project"),
            run_id="run-1",
            task="task",
            skill_task="task",
            request_anchor="request",
            active_vertical="research",
            engineer_role_banner="",
            seed_thread_id=None,
            scope="",
        )
    )

    assert state.skill_text == "library index\n\nruntime-owned portfolio"
    assert state.reviewer_skill_block == "reviewer index"


def test_planner_cannot_claim_a_second_idea_portfolio_path(
    tmp_path: Path,
) -> None:
    task = SimpleNamespace(
        title="Select an idea tournament",
        objective="Produce twelve routes and twelve independent reviews.",
        acceptance_check="The portfolio has one selector.",
        owns_paths=[".argus/teams/idea-tournament-20260913", "RESEARCH_NOTES.md"],
    )

    issues = planner_task_issues("idea", tmp_path, task)

    assert len(issues) == 1
    assert "runtime owns the canonical Idea portfolio" in issues[0]
    assert planner_task_issues("experiment", tmp_path, task) == ()


def test_planner_may_use_nonportfolio_team_in_idea_stage(tmp_path: Path) -> None:
    task = SimpleNamespace(
        title="Audit one source",
        objective="Delegate one bounded citation audit.",
        acceptance_check="The citation is checked.",
        owns_paths=[".argus/teams/citation-audit"],
    )

    assert planner_task_issues("idea", tmp_path, task) == ()


def test_planner_may_benchmark_twelve_items_in_a_nonportfolio_team(
    tmp_path: Path,
) -> None:
    task = SimpleNamespace(
        title="Benchmark kernels",
        objective="Benchmark 12 kernels in parallel.",
        acceptance_check="All measurements are recorded.",
        owns_paths=[".argus/teams/kernel-benchmark"],
    )

    assert planner_task_issues("idea", tmp_path, task) == ()


def test_locked_paper_idea_uses_playbook_without_reselection(
    tmp_path: Path,
) -> None:
    _state(tmp_path)
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["workflow_mode"] = "staged"
    state["research_direction_mode"] = "locked"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    required: list[str] = []

    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=tmp_path,
            state_root=tmp_path,
            stage="idea",
            objective="write a paper from the supplied method",
            direction="supplied method",
            workflow_mode="staged",
            paper_mission=True,
            team_task_id=None,
            runner=None,
            model=None,
            emit=lambda _event: None,
            required_skill_paths=required,
        )
    )

    assert required == ["research-idea-playbook.md"]
    assert not (tmp_path / ".argus" / "teams" / f"{TEAM_ID}-g1").exists()
    assert idea_portfolio_completion_issues(tmp_path) == ()


def test_direct_nonpaper_research_still_requires_idea_playbook(
    tmp_path: Path,
) -> None:
    required: list[str] = []

    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=tmp_path,
            state_root=tmp_path,
            stage="idea",
            objective="propose one idea",
            direction="broad",
            workflow_mode="direct",
            paper_mission=False,
            team_task_id=None,
            runner=None,
            model=None,
            emit=lambda _event: None,
            required_skill_paths=required,
        )
    )

    assert required == ["research-idea-playbook.md"]


def test_routes_grounded_by_arxiv_doi_or_fetched_sources_are_valid(tmp_path: Path) -> None:
    """Three finished routes cited arXiv ids and `.argus/sources/` texts and no
    URL; the URL-only check reopened all six tasks (2026-09-16 04:02)."""
    from argus.verticals.research.idea_portfolio import _route_output_present

    task = {"owns_paths": ["routes/r.md"]}
    route = tmp_path / "routes" / "r.md"
    route.parent.mkdir(parents=True)
    for text in (
        "Avron et al. (ICML 2014 / arXiv:1412.8293): QMC feature maps.",
        "See doi 10.1137/22M1466244 for the SISC version.",
        "Primary source: https://arxiv.org/abs/1506.02785",
    ):
        route.write_text(text, encoding="utf-8")
        assert _route_output_present(tmp_path, task), text
    (tmp_path / ".argus" / "sources").mkdir(parents=True)
    (tmp_path / ".argus" / "sources" / "abc123.txt").write_text("fetched", encoding="utf-8")
    route.write_text("Source: `.argus/sources/abc123.txt` (fetched 2026-09-16).", encoding="utf-8")
    assert _route_output_present(tmp_path, task)
    route.write_text("Source: `.argus/sources/missing.txt`.", encoding="utf-8")
    assert not _route_output_present(tmp_path, task)
    route.write_text("A mechanism with no sources at all.", encoding="utf-8")
    assert not _route_output_present(tmp_path, task)


def test_reopened_portfolio_tasks_say_why(tmp_path: Path) -> None:
    _state(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
    task = task_board.claim_top(root, "w1", now=time.time())
    assert task is not None and task["role"] == "idea-route"
    output = tmp_path / task["owns_paths"][0]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("A mechanism with no sources at all.", encoding="utf-8")
    task_board.complete(root, task["task_id"], shard=_shard(root, "w1", task))

    ensure_idea_portfolio(tmp_path, direction="reliable agents")

    reopened = next(t for t in task_board.snapshot(root) if t["task_id"] == task["task_id"])
    assert reopened["state"] == "pending" and reopened["attempts"] == 1
    assert reopened["reason"].startswith("reopened: route file names no source")


def test_ensure_claims_runtime_ownership_of_a_marker_written_without_one(tmp_path: Path) -> None:
    """The trial's portfolio marker predated ownership; without it the lead
    polled the team instead of waiting (2026-09-16 04:08)."""
    from argus.team import registry

    _state(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
    team_id = root.name
    marker = registry.marker_path(tmp_path, team_id)
    stale = json.loads(marker.read_text(encoding="utf-8"))
    stale.pop("owner", None)
    marker.write_text(json.dumps(stale), encoding="utf-8")

    ensure_idea_portfolio(tmp_path, direction="reliable agents")

    fresh = json.loads(marker.read_text(encoding="utf-8"))
    assert fresh["owner"] == "runtime"
    assert fresh["created_ts"] == stale["created_ts"] and fresh["team_root"] == stale["team_root"]


def _complete_routes_with_verdicts(
    project: Path,
    root: Path,
    verdict_for: "dict[str, str]",
) -> list[dict]:
    """Finish every route/review pair, rejecting routes named in ``verdict_for``."""
    routes: list[dict] = []
    index = 0
    while task := task_board.claim_top(root, f"worker-{index:02d}", now=time.time()):
        owner = f"worker-{index:02d}"
        index += 1
        output = project / task["owns_paths"][0]
        output.parent.mkdir(parents=True, exist_ok=True)
        if task["role"] == "idea-route":
            output.write_text(
                f"# {task['target']}\nhttps://example.org/primary\n", encoding="utf-8"
            )
            routes.append(task)
        else:
            verdict = verdict_for.get(task["target"], "qualified")
            output.write_text(
                json.dumps({
                    "schema_version": 2,
                    "route_id": task["target"],
                    "verdict": verdict,
                    "summary": f"{task['target']} {verdict}",
                    "fatal_concerns": (
                        [f"{task['target']} rests on a benchmark that cannot separate the mechanism"]
                        if verdict == "rejected"
                        else []
                    ),
                }),
                encoding="utf-8",
            )
        task_board.complete(root, task["task_id"], shard=_shard(root, owner, task))
    return routes


def test_all_rejected_routes_regenerate_once_with_reviewer_feedback(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_RESEARCH_PORTFOLIO_MAX_REGENERATIONS", raising=False)
    _state(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
    routes = _complete_routes_with_verdicts(
        tmp_path, root, {f"route-{i:02d}": "rejected" for i in range(1, portfolio_size() + 1)}
    )
    ensure_idea_portfolio(tmp_path, direction="reliable agents")

    payload = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert payload["idea_portfolio"]["regenerations"] == 1
    assert "selection_team_id" not in payload["idea_portfolio"]
    board = {t["task_id"]: t for t in task_board.snapshot(root)}
    for route in routes:
        reopened = board[route["task_id"]]
        assert reopened["state"] == "pending"
        assert "every route in this portfolio was rejected" in reopened["reason"]
        assert "cannot separate the mechanism" in reopened["reason"]
        assert board[f"{route['task_id']}-review"]["state"] == "pending"

    # The regenerated routes are rejected again: the bounded budget is spent,
    # so selection proceeds and the selector is told to pick the most
    # repairable route.
    _complete_routes_with_verdicts(
        tmp_path, root, {f"route-{i:02d}": "rejected" for i in range(1, portfolio_size() + 1)}
    )
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    payload = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert payload["idea_portfolio"]["regenerations"] == 1
    selection_root = tmp_path / ".argus" / "teams" / payload["idea_portfolio"]["selection_team_id"]
    selector = next(t for t in task_board.snapshot(selection_root) if t["role"] == "idea-selector")
    assert "most repairable" in selector["objective"]
    assert '"review_verdict": "rejected"' in selector["objective"]


def test_selection_is_restricted_to_qualified_routes(tmp_path: Path) -> None:
    _state(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
    routes = _complete_routes_with_verdicts(tmp_path, root, {"route-01": "rejected"})
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    payload = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert "regenerations" not in payload["idea_portfolio"]
    selection_root = tmp_path / ".argus" / "teams" / payload["idea_portfolio"]["selection_team_id"]
    selector = next(t for t in task_board.snapshot(selection_root) if t["role"] == "idea-selector")
    assert "Only routes whose independent review outcome is `qualified` are eligible" in selector["objective"]
    assert "route-01" not in selector["objective"].split("eligible (")[1].split(")")[0]

    rejected = next(r for r in routes if r["target"] == "route-01")
    _complete_selector(tmp_path, rejected)
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    assert idea_portfolio_selection(tmp_path) is None
    selector = next(t for t in task_board.snapshot(selection_root) if t["role"] == "idea-selector")
    assert selector["state"] == "pending"

    qualified = next(r for r in routes if r["target"] != "route-01")
    _complete_selector(tmp_path, qualified)
    ensure_idea_portfolio(tmp_path, direction="reliable agents")
    selected = idea_portfolio_selection(tmp_path)
    assert selected is not None and selected["route_id"] == qualified["target"]


def test_regeneration_budget_is_an_operator_setting(monkeypatch) -> None:
    from argus.verticals.research.idea_portfolio import max_regenerations

    monkeypatch.delenv("ARGUS_RESEARCH_PORTFOLIO_MAX_REGENERATIONS", raising=False)
    assert max_regenerations() == 1
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_MAX_REGENERATIONS", "9")
    assert max_regenerations() == 3
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_MAX_REGENERATIONS", "0")
    assert max_regenerations() == 0
    monkeypatch.setenv("ARGUS_RESEARCH_PORTFOLIO_MAX_REGENERATIONS", "many")
    assert max_regenerations() == 1
