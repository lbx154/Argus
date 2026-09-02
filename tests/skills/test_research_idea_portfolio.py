from __future__ import annotations

import json
import time
from pathlib import Path

from argus_skill.core.vertical_contract import VerticalLibraryContext
from argus_skill.skills.vertical_select import reset_stage_for_new_intent
from argus_skill.team import task_board
from argus_skill.verticals.research.idea_portfolio import (
    MAX_HANDOFF_CHARS,
    SELECTION_POLICY,
    TEAM_ID,
    ensure_idea_portfolio,
    idea_portfolio_completion_issues,
    idea_portfolio_selection,
    portfolio_tasks,
)
from argus_skill.verticals.research.library_preparation import (
    prepare_skill_libraries,
)


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
                for index in range(1, 13)
                if f"route-{index:02d}" != selected["target"]
            },
        }),
        encoding="utf-8",
    )
    task_board.complete(root, task["task_id"], shard=_shard(root, owner, task))
    return output


def test_portfolio_is_twelve_source_only_routes_plus_twelve_reviews() -> None:
    tasks = portfolio_tasks()

    assert sum(task["role"] == "idea-route" for task in tasks) == 12
    assert sum(task["role"] == "idea-review" for task in tasks) == 12
    assert all(task["owns_paths"][0].startswith(".argus/teams/") for task in tasks)
    text = " ".join(task["objective"] for task in tasks).lower()
    assert "do not execute candidate code" in text
    assert "request an experiment during selection" in text


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
    _state(tmp_path)
    root = ensure_idea_portfolio(tmp_path, direction="reliable agents")
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
    selected = idea_portfolio_selection(tmp_path)
    assert selected is not None
    assert "winner_detail" not in selected
    handoff = (tmp_path / "HANDOFF.md").read_text(encoding="utf-8")
    assert handoff.startswith("# HANDOFF — IDEA\n")
    assert handoff.count("\n- **route-") == 11
    assert len(handoff) <= MAX_HANDOFF_CHARS


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


def test_direct_research_still_prepares_the_idea_portfolio(tmp_path: Path) -> None:
    _state(tmp_path)
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

    assert "engineer/idea-discovery.md" in required
    root = tmp_path / ".argus" / "teams" / f"{TEAM_ID}-g1"
    assert len(task_board.snapshot(root)) == 24
