"""Durable source-only idea portfolios stored entirely under ``.argus``."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ...core.file_lock import exclusive_file_lock
from ...core.pipeline_state import read_pipeline_state, write_pipeline_state
from ...core.research_contract import (
    resolve_research_direction_mode,
    resolve_research_target_level,
)
from ...team import formation, pool, roster, task_board

TEAM_ID = "research-idea-pipeline-v8"
DEFAULT_PORTFOLIO_SIZE = 12
SELECTION_POLICY = "fixed_twelve_source_only_v6"
SELECTION_TEAM_SUFFIX = "selection"
_REVIEW_SCHEMA_VERSION = 2
_SELECTION_SCHEMA_VERSION = 3
TEAM_ROOT = Path(".argus") / "teams"
_STATE_LOCK_PATH = Path(".argus") / "IDEA_PORTFOLIO.lock"
_HANDOFF_PATH = Path("HANDOFF.md")
_LEGACY_STATE_PATH = Path("research") / "IDEA_PORTFOLIO.json"
_LEGACY_SELECTION_PATH = Path("research") / "IDEA_SELECTION.json"
_REVIEW_VERDICTS = frozenset({"qualified", "rejected"})
_TEAM_TASK_ENV = "ARGUS_SKILL_TEAM_TASK_ID"
MAX_WINNER_EXPLANATION_CHARS = 1000
MAX_EVIDENCE_TEXT_CHARS = 1000
MAX_RESOURCE_TEXT_CHARS = 600
MAX_RISK_COUNT = 6
MAX_RISK_TEXT_CHARS = 240
MAX_REJECTION_TEXT_CHARS = 220
MAX_HANDOFF_CHARS = 9000
_NO_NESTED_TEAM = (
    "This task is already one worker in the parent idea portfolio. Do not create, "
    "ensure, launch, or delegate another Team or idea portfolio."
)


def portfolio_required(project_root: Path) -> bool:
    target = resolve_research_target_level(project_root)
    direction = resolve_research_direction_mode(project_root)
    return target in {"publishable", "doctoral"} and direction != "locked"


def _team_id(generation: int) -> str:
    return f"{TEAM_ID}-g{max(1, generation)}"


def _artifact_root(team_id: str) -> str:
    return f".argus/teams/{team_id}/artifacts"


def _selection_team_id(team_id: str) -> str:
    return f"{team_id}-{SELECTION_TEAM_SUFFIX}"


def _selection_artifact_root(team_id: str) -> str:
    return f".argus/teams/{_selection_team_id(team_id)}/artifacts"


def _route_task(
    team_id: str,
    artifact_root: str,
    route_id: str,
) -> dict[str, Any]:
    task_id = f"{team_id}-{route_id}"
    output = f"{artifact_root}/routes/{route_id}.md"
    return {
        "task_id": task_id,
        "title": f"Investigate ideation route {route_id}",
        "objective": (
            "Choose a mechanism family genuinely distinct from the other routes and "
            "important to the broad research direction. Develop the strongest "
            "source-grounded case for a nontrivial contribution. Record the mechanism, "
            "primary-source trail, closest work, non-obvious gap, strongest kill "
            "argument, resource needs, and future decisive experiment. Selection is "
            "source-only: inspect papers, documentation, and official source, but do "
            "not execute candidate code or run probe experiments. Create "
            f"`{output}`. {_NO_NESTED_TEAM}"
        ),
        "acceptance_check": (
            f"`{output}` makes a source-grounded case for a distinct mechanism and "
            "states its strongest uncertainty."
        ),
        "role": "idea-route",
        "owns_paths": [output],
        "target": route_id,
        "priority": 10,
    }


def _review_task(
    route_task: dict[str, Any],
    artifact_root: str,
) -> dict[str, Any]:
    route_id = str(route_task["target"])
    route_output = str(route_task["owns_paths"][0])
    output = f"{artifact_root}/reviews/{route_id}.json"
    return {
        "task_id": f"{route_task['task_id']}-review",
        "title": f"Independently review candidate {route_id}",
        "objective": (
            f"Act as a fresh research reviewer for `{route_output}`. Verify the nearest "
            "claim-critical prior art and attack the mechanism, attribution, and future "
            "evidence plan. Do not reward convenience or request an experiment during "
            f"selection. Write `{output}` with schema_version="
            f"{_REVIEW_SCHEMA_VERSION}, route_id, verdict (`qualified` or `rejected`), "
            "summary, and fatal_concerns (array). "
            f"{_NO_NESTED_TEAM}"
        ),
        "acceptance_check": (
            f"`{output}` is a decisive independent review of route {route_id}."
        ),
        "role": "idea-review",
        "owns_paths": [output],
        "deps": [str(route_task["task_id"])],
        "target": route_id,
        "priority": 5,
    }


def portfolio_tasks(
    team_id: str | None = None,
    artifact_root: str | None = None,
) -> list[dict[str, Any]]:
    resolved_team_id = team_id or _team_id(1)
    internal_root = artifact_root or _artifact_root(resolved_team_id)
    routes = [
        _route_task(resolved_team_id, internal_root, f"route-{index:02d}")
        for index in range(1, DEFAULT_PORTFOLIO_SIZE + 1)
    ]
    return [*routes, *(_review_task(route, internal_root) for route in routes)]


def _selection_tasks(
    team_id: str,
    artifact_root: str,
    available_review_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    specs = {
        task["task_id"]: task
        for task in portfolio_tasks(team_id, artifact_root)
    }
    candidates: list[dict[str, str]] = []
    for review_id in available_review_ids:
        review = specs[review_id]
        route_task_id = str(review_id.removesuffix("-review"))
        route = specs[route_task_id]
        candidates.append({
            "route_id": str(route["target"]),
            "route_task_id": route_task_id,
            "route_artifact": str(route["owns_paths"][0]),
            "review_task_id": review_id,
            "review_artifact": str(review["owns_paths"][0]),
        })
    selector_id = f"{team_id}-evidence-selector"
    output = f"{_selection_artifact_root(team_id)}/selection.json"
    return [{
        "task_id": selector_id,
        "title": "Select the strongest supported idea",
        "objective": (
            "Read all twelve route/review pairs below and choose exactly one route. "
            "The choice is source-only and happens once; do not run candidate code or "
            "experiments. Record why the winner survives the alternatives, resource "
            "needs, unresolved risks, and one single-line rejection reason for each "
            "of the other eleven routes.\n"
            + json.dumps(candidates, ensure_ascii=True, indent=2)
            + f"\nWrite `{output}` as one JSON object with schema_version="
            f"{_SELECTION_SCHEMA_VERSION}, policy=`{SELECTION_POLICY}`, route_id, "
            "route_task_id, review_task_id, route_artifact, review_artifact, rationale, "
            "evidence_considered, resource_requirements, unresolved_risks (array), and "
            "rejections (object mapping every unselected route_id to one line). "
            f"{_NO_NESTED_TEAM}"
        ),
        "acceptance_check": (
            f"`{output}` records one winner after all twelve routes and reviews and "
            "contains eleven single-line rejection reasons."
        ),
        "role": "idea-selector",
        "owns_paths": [output],
        "target": "evidence-selection",
        "priority": 0,
    }]


def _resolved_roots(
    project_root: Path,
    state_root: Path | None,
) -> tuple[Path, Path]:
    project = Path(project_root).expanduser().resolve()
    state = Path(state_root or project).expanduser().resolve()
    return project, state


@contextmanager
def _state_lock(state_root: Path) -> Iterator[None]:
    path = state_root / _STATE_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        with exclusive_file_lock(handle, lock_name="idea portfolio state"):
            yield


def _pipeline_payload(state_root: Path) -> dict[str, Any]:
    payload = read_pipeline_state(state_root)
    return payload if isinstance(payload, dict) else {}


def _portfolio_meta(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("idea_portfolio")
    return dict(value) if isinstance(value, dict) else {}


def _selected_idea(payload: dict[str, Any]) -> dict[str, Any] | None:
    value = payload.get("selected_idea")
    return dict(value) if isinstance(value, dict) else None


def _write_pipeline_unlocked(
    state_root: Path,
    payload: dict[str, Any],
) -> None:
    write_pipeline_state(state_root, payload)


def _meta_matches(
    meta: dict[str, Any],
    *,
    team_id: str,
    artifact_root: str,
) -> bool:
    try:
        generation = max(1, int(meta.get("generation") or 1))
    except (TypeError, ValueError):
        return False
    return bool(
        meta.get("team_id") == team_id
        and team_id == _team_id(generation)
        and meta.get("artifact_root") == artifact_root
        and meta.get("selection_policy") == SELECTION_POLICY
    )


def _selection_team_root(project_root: Path, team_id: str) -> Path:
    return (project_root / TEAM_ROOT / _selection_team_id(team_id)).resolve()


def _task_output_path(project_root: Path, task: dict[str, Any]) -> Path | None:
    owned = list(task.get("owns_paths") or [])
    if len(owned) != 1:
        return None
    path = (project_root / str(owned[0])).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError:
        return None
    return path


def _json_object(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _valid_shard(root: Path, task: dict[str, Any]) -> bool:
    raw_path = str(task.get("result_shard") or "").strip()
    if not raw_path:
        return False
    path = Path(raw_path).expanduser().resolve()
    try:
        path.relative_to(root.resolve())
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    except (ValueError, OSError, IndexError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(row, dict)
        and row.get("success") is True
        and str(row.get("task_id") or "") == str(task.get("task_id") or "")
        and str(row.get("member_id") or "") == str(task.get("owner") or "")
    )


def _route_output_present(project_root: Path, task: dict[str, Any]) -> bool:
    path = _task_output_path(project_root, task)
    if path is None:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(text.strip()) and ("https://" in text or "http://" in text)


def _review_payload(
    project_root: Path,
    task: dict[str, Any],
) -> dict[str, Any] | None:
    payload = _json_object(_task_output_path(project_root, task))
    target = str(task.get("target") or "")
    if (
        payload is None
        or payload.get("schema_version") != _REVIEW_SCHEMA_VERSION
        or str(payload.get("route_id") or "") != target
        or str(payload.get("verdict") or "") not in _REVIEW_VERDICTS
        or not str(payload.get("summary") or "").strip()
        or not isinstance(payload.get("fatal_concerns"), list)
    ):
        return None
    return payload


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _bounded_text(value: object, limit: int) -> str:
    text = _one_line(value)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _compact_selection(payload: dict[str, Any]) -> dict[str, Any]:
    route_id = _bounded_text(payload.get("route_id"), 120)
    rejections = payload.get("rejections")
    compact_rejections = {
        _bounded_text(key, 120): _bounded_text(value, MAX_REJECTION_TEXT_CHARS)
        for key, value in (
            rejections.items() if isinstance(rejections, dict) else ()
        )
        if _bounded_text(key, 120) != route_id and _one_line(value)
    }
    risks = payload.get("unresolved_risks")
    compact_risks = [
        _bounded_text(item, MAX_RISK_TEXT_CHARS)
        for item in (risks if isinstance(risks, list) else ())
        if _one_line(item)
    ][:MAX_RISK_COUNT]
    compact: dict[str, Any] = {
        "schema_version": _SELECTION_SCHEMA_VERSION,
        "policy": SELECTION_POLICY,
        "route_id": route_id,
        "route_task_id": _bounded_text(payload.get("route_task_id"), 240),
        "review_task_id": _bounded_text(payload.get("review_task_id"), 240),
        "route_artifact": _bounded_text(payload.get("route_artifact"), 500),
        "review_artifact": _bounded_text(payload.get("review_artifact"), 500),
        "rationale": _bounded_text(
            payload.get("rationale"),
            MAX_WINNER_EXPLANATION_CHARS,
        ),
        "evidence_considered": _bounded_text(
            payload.get("evidence_considered"),
            MAX_EVIDENCE_TEXT_CHARS,
        ),
        "resource_requirements": _bounded_text(
            payload.get("resource_requirements"),
            MAX_RESOURCE_TEXT_CHARS,
        ),
        "unresolved_risks": compact_risks,
        "rejections": compact_rejections,
    }
    for key in (
        "team_id",
        "selection_team_id",
    ):
        value = _bounded_text(payload.get(key), 240)
        if value:
            compact[key] = value
    for key in ("selected_at", "research_intent_generation"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            compact[key] = value
    if payload.get("migrated_from_legacy") is True:
        compact["migrated_from_legacy"] = True
    return compact


def _selection_payload(
    project_root: Path,
    path: Path,
) -> dict[str, Any] | None:
    payload = _json_object(path)
    required = (
        "route_id",
        "route_task_id",
        "review_task_id",
        "route_artifact",
        "review_artifact",
        "rationale",
        "evidence_considered",
        "resource_requirements",
    )
    if (
        payload is None
        or payload.get("schema_version") != _SELECTION_SCHEMA_VERSION
        or payload.get("policy") != SELECTION_POLICY
        or any(not _one_line(payload.get(key)) for key in required)
        or not isinstance(payload.get("unresolved_risks"), list)
        or not isinstance(payload.get("rejections"), dict)
    ):
        return None
    route_id = str(payload["route_id"])
    rejections = {
        _bounded_text(key, 120): _bounded_text(value, MAX_REJECTION_TEXT_CHARS)
        for key, value in payload["rejections"].items()
        if str(key) != route_id and _one_line(value)
    }
    if len(rejections) != DEFAULT_PORTFOLIO_SIZE - 1:
        return None
    payload["rejections"] = rejections
    return _compact_selection(payload)


def _valid_selected_idea(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    required = ("route_id", "rationale", "resource_requirements", "rejections")
    if any(not payload.get(key) for key in required):
        return None
    rejections = payload.get("rejections")
    if not isinstance(rejections, dict):
        return None
    normalized = {
        str(key): _one_line(value)
        for key, value in rejections.items()
        if str(key) != str(payload.get("route_id")) and _one_line(value)
    }
    if len(normalized) != DEFAULT_PORTFOLIO_SIZE - 1:
        return None
    selected = dict(payload)
    selected["rejections"] = normalized
    return _compact_selection(selected)


def _review_reason(review: dict[str, Any] | None) -> str:
    if not review:
        return "not selected by the authoritative portfolio comparison"
    concerns = review.get("fatal_concerns")
    if isinstance(concerns, list):
        first = next((_one_line(item) for item in concerns if _one_line(item)), "")
        if first:
            return first
    summary = _one_line(review.get("summary"))
    return summary or "not selected by the authoritative portfolio comparison"


def _available_review_ids(
    project_root: Path,
    root: Path,
    actual: dict[str, dict[str, Any]],
    *,
    team_id: str,
    artifact_root: str,
) -> tuple[str, ...]:
    specs = portfolio_tasks(team_id, artifact_root)
    route_ids = {
        str(task["task_id"])
        for task in specs
        if task.get("role") == "idea-route"
    }
    review_ids = {
        str(task["task_id"])
        for task in specs
        if task.get("role") == "idea-review"
    }
    if len(route_ids) != DEFAULT_PORTFOLIO_SIZE or len(review_ids) != DEFAULT_PORTFOLIO_SIZE:
        return ()
    if any(
        actual.get(task_id, {}).get("state") != "done"
        or not _valid_shard(root, actual.get(task_id, {}))
        or not _route_output_present(project_root, actual.get(task_id, {}))
        for task_id in route_ids
    ):
        return ()
    if any(
        actual.get(task_id, {}).get("state") != "done"
        or not _valid_shard(root, actual.get(task_id, {}))
        or _review_payload(project_root, actual.get(task_id, {})) is None
        for task_id in review_ids
    ):
        return ()
    for route_id in route_ids:
        route = actual.get(route_id, {})
        review = actual.get(f"{route_id}-review", {})
        route_owner = str(route.get("owner") or "")
        review_owner = str(review.get("owner") or "")
        if not route_owner or not review_owner or route_owner == review_owner:
            return ()
    return tuple(sorted(review_ids))


def _retry_invalid_terminal_tasks(
    project_root: Path,
    root: Path,
    actual: dict[str, dict[str, Any]],
    *,
    team_id: str,
    artifact_root: str,
) -> tuple[str, ...]:
    retried: list[str] = []
    specs = {
        str(task["task_id"]): task
        for task in portfolio_tasks(team_id, artifact_root)
    }
    for route_spec in (
        task for task in specs.values() if task.get("role") == "idea-route"
    ):
        route_id = str(route_spec["task_id"])
        review_id = f"{route_id}-review"
        route = actual.get(route_id, {})
        review = actual.get(review_id, {})
        route_valid = bool(
            route.get("state") == "done"
            and _valid_shard(root, route)
            and _route_output_present(project_root, route)
        )
        review_valid = bool(
            review.get("state") == "done"
            and _valid_shard(root, review)
            and _review_payload(project_root, review) is not None
        )
        if (
            route.get("state") in {"done", "failed"}
            and not route_valid
            and task_board.retry_terminal(root, route_id)
        ):
            retried.append(route_id)
        if (
            review.get("state") in {"done", "failed"}
            and (not route_valid or not review_valid)
            and task_board.retry_terminal(root, review_id)
        ):
            retried.append(review_id)
    return tuple(retried)


def _dissolve_team(root: Path, reason: str) -> None:
    if not root.is_dir():
        return
    for task in task_board.snapshot(root):
        if task.get("state") in {"pending", "claimed", "running"}:
            task_board.fail(root, str(task["task_id"]), reason=reason)
    roster.set_state(root, "dissolved")
    pool.update(root, width=0, state="dissolved")


def _ensure_selection_team(
    project_root: Path,
    *,
    root: Path,
    team_id: str,
    artifact_root: str,
    state_root: Path | None = None,
) -> Path | None:
    project_root, state_root = _resolved_roots(project_root, state_root)
    with _state_lock(state_root):
        payload = _pipeline_payload(state_root)
        meta = _portfolio_meta(payload)
        if not _meta_matches(
            meta,
            team_id=team_id,
            artifact_root=artifact_root,
        ):
            return None
        if _valid_selected_idea(_selected_idea(payload)) is not None:
            return _selection_team_root(project_root, team_id)

    actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(root)
    }
    if _retry_invalid_terminal_tasks(
        project_root,
        root,
        actual,
        team_id=team_id,
        artifact_root=artifact_root,
    ):
        actual = {
            str(task.get("task_id") or ""): task
            for task in task_board.snapshot(root)
        }
    if any(
        task.get("state") in {"pending", "claimed", "running"}
        for task in actual.values()
    ) and (
        str(pool.read(root).get("state") or "") != "running"
        or int(pool.read(root).get("width", 0) or 0) != DEFAULT_PORTFOLIO_SIZE
    ):
        pool.update(root, width=DEFAULT_PORTFOLIO_SIZE, state="running")

    reviews = _available_review_ids(
        project_root,
        root,
        actual,
        team_id=team_id,
        artifact_root=artifact_root,
    )
    if not reviews:
        return None

    selection_root = _selection_team_root(project_root, team_id)
    selection_team_id = _selection_team_id(team_id)
    tasks = _selection_tasks(team_id, artifact_root, reviews)
    existing = task_board.snapshot(selection_root)
    receipt = formation.load_receipt(selection_root)
    canonical = (
        existing
        and str(receipt.get("team_id") or "") == selection_team_id
        and task_board.material_specs_match(selection_root, tasks)
    )
    if not canonical:
        formation.form_team(
            project_root=project_root,
            root=selection_root,
            team_id=selection_team_id,
            mission=(
                "Select one idea exactly once after all twelve source-only routes "
                "and independent reviews finish."
            ),
            lead="engineer",
            cwd=project_root,
            tasks=tasks,
        )
        pool.update(selection_root, width=1, state="running")
    elif (
        str(pool.read(selection_root).get("state") or "") == "running"
        and int(pool.read(selection_root).get("width", 0) or 0) != 1
    ):
        pool.update(selection_root, width=1, state="running")

    selector = next(
        (
            task
            for task in task_board.snapshot(selection_root)
            if task.get("role") == "idea-selector"
        ),
        {},
    )
    if selector.get("state") in {"done", "failed"}:
        selection = _selection_from_tasks(
            project_root,
            root,
            selection_root,
            team_id,
            artifact_root,
            reviews,
        )
        if selection is None and task_board.retry_terminal(
            selection_root,
            str(selector.get("task_id") or ""),
        ):
            pool.update(selection_root, width=1, state="running")

    with _state_lock(state_root):
        payload = _pipeline_payload(state_root)
        meta = _portfolio_meta(payload)
        if not _meta_matches(
            meta,
            team_id=team_id,
            artifact_root=artifact_root,
        ):
            _dissolve_team(
                selection_root,
                "superseded by a newer research direction",
            )
            return None
        meta["selection_team_id"] = selection_team_id
        meta["selection_review_task_ids"] = list(reviews)
        payload["idea_portfolio"] = meta
        _write_pipeline_unlocked(state_root, payload)
    return selection_root


def ensure_idea_portfolio(
    project_root: Path,
    *,
    direction: str,
    state_root: Path | None = None,
) -> Path:
    nested_task_id = os.environ.get(_TEAM_TASK_ENV, "").strip()
    if nested_task_id:
        raise RuntimeError(
            "nested idea portfolio formation is disabled inside team task "
            f"{nested_task_id!r}"
        )
    project_root, state_root = _resolved_roots(project_root, state_root)
    migrate_legacy_idea_selection(project_root, state_root=state_root)

    stale_roots: list[Path] = []
    with _state_lock(state_root):
        payload = _pipeline_payload(state_root)
        selected = _valid_selected_idea(_selected_idea(payload))
        meta = _portfolio_meta(payload)
        if selected is not None:
            team = str(
                meta.get("team_id")
                or _team_id(
                    max(
                        1,
                        int(payload.get("research_intent_generation") or 1),
                    )
                )
            )
            root = project_root / TEAM_ROOT / team
            _write_handoff(project_root, selected)
            return root

        previous_direction = _one_line(meta.get("direction"))
        normalized_direction = _one_line(direction)
        try:
            generation = max(
                1,
                int(payload.get("research_intent_generation") or 1),
                int(meta.get("generation") or 1),
            )
        except (TypeError, ValueError):
            generation = 1
        if meta and previous_direction and previous_direction != normalized_direction:
            old_team = str(meta.get("team_id") or "")
            if old_team:
                stale_roots.extend((
                    project_root / TEAM_ROOT / old_team,
                    _selection_team_root(project_root, old_team),
                ))
            generation += 1
            payload["research_intent_generation"] = generation
        team_id = _team_id(generation)
        artifact_root = _artifact_root(team_id)
        payload["research_intent_generation"] = generation
        payload["idea_portfolio"] = {
            "schema_version": 1,
            "generation": generation,
            "team_id": team_id,
            "artifact_root": artifact_root,
            "direction": normalized_direction,
            "selection_policy": SELECTION_POLICY,
        }
        payload["current_verdict"] = "idea_selection_pending"
        payload["next_action"] = (
            "Complete twelve source-only routes, twelve independent reviews, "
            "and the one-time selector."
        )
        _write_pipeline_unlocked(state_root, payload)

    for stale in stale_roots:
        _dissolve_team(stale, "superseded by a newer research direction")

    root = project_root / TEAM_ROOT / team_id
    tasks = portfolio_tasks(team_id, artifact_root)
    existing = task_board.snapshot(root)
    receipt = formation.load_receipt(root)
    canonical = (
        existing
        and str(receipt.get("team_id") or "") == team_id
        and task_board.material_specs_match(root, tasks)
    )
    if not canonical:
        formation.form_team(
            project_root=project_root,
            root=root,
            team_id=team_id,
            mission=(
                "Complete exactly twelve distinct source-only routes and one "
                "independent review for each before one selector chooses."
            ),
            lead="engineer",
            cwd=project_root,
            tasks=tasks,
        )
        pool.update(root, width=DEFAULT_PORTFOLIO_SIZE, state="running")
    elif (
        str(pool.read(root).get("state") or "") == "running"
        and int(pool.read(root).get("width", 0) or 0) != DEFAULT_PORTFOLIO_SIZE
    ):
        pool.update(root, width=DEFAULT_PORTFOLIO_SIZE, state="running")

    selection_root = _ensure_selection_team(
        project_root,
        root=root,
        team_id=team_id,
        artifact_root=artifact_root,
        state_root=state_root,
    )
    selection = idea_portfolio_selection(project_root, state_root=state_root)
    if selection is not None and selection_root is not None:
        _materialize_selection(
            project_root,
            root,
            selection_root,
            selection,
            state_root=state_root,
        )
    return root


def _selection_from_tasks(
    project_root: Path,
    root: Path,
    selection_root: Path,
    team_id: str,
    artifact_root: str,
    available_review_ids: tuple[str, ...],
) -> dict[str, Any] | None:
    base_actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(root)
    }
    canonical_review_ids = _available_review_ids(
        project_root,
        root,
        base_actual,
        team_id=team_id,
        artifact_root=artifact_root,
    )
    if tuple(sorted(available_review_ids)) != canonical_review_ids:
        return None
    selection_specs = _selection_tasks(team_id, artifact_root, available_review_ids)
    if not task_board.material_specs_match(selection_root, selection_specs):
        return None
    selector = next(
        (
            task
            for task in task_board.snapshot(selection_root)
            if task.get("role") == "idea-selector"
        ),
        {},
    )
    if selector.get("state") != "done" or not _valid_shard(selection_root, selector):
        return None
    selection_path = _task_output_path(project_root, selector)
    if selection_path is None:
        return None
    selection = _selection_payload(project_root, selection_path)
    if selection is None:
        return None

    route_task_id = str(selection.get("route_task_id") or "")
    review_task_id = str(selection.get("review_task_id") or "")
    route = base_actual.get(route_task_id, {})
    review = base_actual.get(review_task_id, {})
    review_payload = _review_payload(project_root, review)
    if (
        route_task_id != review_task_id.removesuffix("-review")
        or route.get("state") != "done"
        or review.get("state") != "done"
        or not _valid_shard(root, route)
        or not _valid_shard(root, review)
        or not _route_output_present(project_root, route)
        or review_payload is None
        or review_task_id not in canonical_review_ids
        or str(selection.get("route_id") or "") != str(route.get("target") or "")
        or str(selection.get("route_artifact") or "")
        != str((route.get("owns_paths") or [""])[0])
        or str(selection.get("review_artifact") or "")
        != str((review.get("owns_paths") or [""])[0])
    ):
        return None

    route_owner = str(route.get("owner") or "")
    review_owner = str(review.get("owner") or "")
    finished = [
        float(task.get("finished_ts") or 0)
        for task in (route, review, selector)
    ]
    latest_review_finished = max(
        float(base_actual[review_id].get("finished_ts") or 0)
        for review_id in canonical_review_ids
    )
    if (
        not route_owner
        or not review_owner
        or route_owner == review_owner
        or not str(selector.get("owner") or "")
        or not (0 < finished[0] <= finished[1] <= finished[2])
        or finished[2] < latest_review_finished
    ):
        return None
    return {
        **selection,
        "team_id": team_id,
        "selection_team_id": _selection_team_id(team_id),
        "selected_at": float(selector.get("finished_ts") or 0),
    }


def _task_selection(
    project_root: Path,
    state_root: Path,
    meta: dict[str, Any],
) -> dict[str, Any] | None:
    team_id = str(meta.get("team_id") or "")
    artifact_root = str(meta.get("artifact_root") or "")
    raw_reviews = meta.get("selection_review_task_ids")
    if (
        not team_id
        or not artifact_root
        or not isinstance(raw_reviews, list)
        or len(raw_reviews) != DEFAULT_PORTFOLIO_SIZE
    ):
        return None
    root = project_root / TEAM_ROOT / team_id
    selection_root = _selection_team_root(project_root, team_id)
    selection = _selection_from_tasks(
        project_root,
        root,
        selection_root,
        team_id,
        artifact_root,
        tuple(str(item) for item in raw_reviews),
    )
    if selection is None:
        return None
    try:
        generation = max(1, int(meta.get("generation") or 1))
    except (TypeError, ValueError):
        return None
    selection["research_intent_generation"] = generation
    return _valid_selected_idea(selection)


def idea_portfolio_selection(
    project_root: Path,
    *,
    state_root: Path | None = None,
) -> dict[str, Any] | None:
    project_root, state_root = _resolved_roots(project_root, state_root)
    migrate_legacy_idea_selection(project_root, state_root=state_root)
    payload = _pipeline_payload(state_root)
    selected = _valid_selected_idea(_selected_idea(payload))
    if selected is not None:
        return selected
    return _task_selection(project_root, state_root, _portfolio_meta(payload))


def _write_handoff(project_root: Path, selection: dict[str, Any]) -> None:
    from ...manager.source_writeback import atomic_write

    selection = _valid_selected_idea(selection) or {}
    rejections = selection.get("rejections")
    rejection_lines = "\n".join(
        f"- **{route_id}**: {_one_line(reason)}"
        for route_id, reason in sorted(
            rejections.items() if isinstance(rejections, dict) else ()
        )
    )
    unresolved = selection.get("unresolved_risks")
    unresolved_lines = "\n".join(
        f"- {_one_line(item)}"
        for item in (unresolved if isinstance(unresolved, list) else ())
        if _one_line(item)
    ) or "- None recorded at selection."
    text = (
        "# HANDOFF — IDEA\n\n"
        "## Selected idea\n"
        f"- Route: `{selection.get('route_id')}`\n"
        f"- Why it won: {selection.get('rationale')}\n"
        f"- Evidence considered: {selection.get('evidence_considered')}\n"
        f"- Resource needs: {selection.get('resource_requirements')}\n\n"
        "## Unresolved build obligations\n"
        f"{unresolved_lines}\n\n"
        "## Rejected routes\n"
        f"{rejection_lines}\n"
    )
    if len(text) > MAX_HANDOFF_CHARS:
        raise ValueError(
            f"compact research HANDOFF exceeds {MAX_HANDOFF_CHARS} characters"
        )
    atomic_write(project_root / _HANDOFF_PATH, text)


def _materialize_selection(
    project_root: Path,
    root: Path,
    selection_root: Path,
    selection: dict[str, Any],
    *,
    state_root: Path | None = None,
) -> bool:
    project_root, state_root = _resolved_roots(project_root, state_root)
    team_id = str(selection.get("team_id") or "")
    if (
        root.resolve() != (project_root / TEAM_ROOT / team_id).resolve()
        or selection_root.resolve() != _selection_team_root(project_root, team_id)
        or selection.get("selection_team_id") != _selection_team_id(team_id)
    ):
        return False
    selected = _valid_selected_idea(selection)
    if selected is None:
        return False

    with _state_lock(state_root):
        payload = _pipeline_payload(state_root)
        try:
            current_generation = max(
                1,
                int(payload.get("research_intent_generation") or 1),
            )
        except (TypeError, ValueError):
            return False
        if int(selected.get("research_intent_generation") or 0) != current_generation:
            return False
        existing = _valid_selected_idea(_selected_idea(payload))
        if existing is not None:
            if (
                existing.get("route_id") != selected.get("route_id")
                or existing.get("route_task_id") != selected.get("route_task_id")
            ):
                return False
            selected = existing
        else:
            meta = _portfolio_meta(payload)
            if not _meta_matches(
                meta,
                team_id=team_id,
                artifact_root=str(meta.get("artifact_root") or ""),
            ):
                return False
            payload["selected_idea"] = selected
            payload["current_verdict"] = "idea_selected"
            payload["next_action"] = (
                "Build the selected mechanism and strongest fair baseline, then "
                "rewrite HANDOFF.md for Experiment."
            )
            meta["selection_complete"] = True
            payload["idea_portfolio"] = meta
            _write_pipeline_unlocked(state_root, payload)

    _write_handoff(project_root, selected)
    if str(pool.read(selection_root).get("state") or "") not in {
        "draining",
        "dissolved",
    }:
        pool.update(selection_root, state="draining")
    if str(pool.read(root).get("state") or "") not in {"draining", "dissolved"}:
        pool.update(root, state="draining")
    return True


def _legacy_selector_payload(
    project_root: Path,
    legacy_meta: dict[str, Any],
) -> dict[str, Any] | None:
    direct = _json_object(project_root / _LEGACY_SELECTION_PATH)
    if direct is not None:
        return direct
    selection_team = str(legacy_meta.get("selection_team_id") or "")
    if not selection_team:
        return None
    selection_root = project_root / TEAM_ROOT / selection_team
    for task in task_board.snapshot(selection_root):
        if task.get("role") != "idea-selector" or task.get("state") != "done":
            continue
        payload = _json_object(_task_output_path(project_root, task))
        if payload is not None:
            return payload
    return None


def _legacy_selection(
    project_root: Path,
) -> dict[str, Any] | None:
    legacy_meta = _json_object(project_root / _LEGACY_STATE_PATH) or {}
    source = _legacy_selector_payload(project_root, legacy_meta)
    if source is None:
        return None
    route_id = _one_line(source.get("route_id"))
    if not route_id:
        return None

    team_id = _one_line(
        source.get("team_id")
        or legacy_meta.get("team_id")
        or "legacy-research-idea-portfolio"
    )
    root = project_root / TEAM_ROOT / team_id
    tasks = task_board.snapshot(root)
    routes = {
        str(task.get("target") or ""): task
        for task in tasks
        if task.get("role") == "idea-route"
    }
    reviews = {
        str(task.get("target") or ""): task
        for task in tasks
        if task.get("role") == "idea-review"
    }
    route_ids = set(routes) | set(reviews)
    if len(route_ids) < DEFAULT_PORTFOLIO_SIZE:
        route_ids.update(
            f"route-{index:02d}"
            for index in range(1, DEFAULT_PORTFOLIO_SIZE + 1)
        )
    route_ids.discard(route_id)
    rejection_ids = sorted(route_ids)[: DEFAULT_PORTFOLIO_SIZE - 1]
    rejections: dict[str, str] = {}
    old_rejections = source.get("rejections")
    if isinstance(old_rejections, dict):
        rejections.update(
            {
                candidate: _one_line(old_rejections.get(candidate))
                for candidate in rejection_ids
                if _one_line(old_rejections.get(candidate))
            }
        )
    for candidate in rejection_ids:
        if candidate in rejections:
            continue
        review = _review_payload(project_root, reviews.get(candidate, {}))
        rejections[candidate] = _review_reason(review)

    selected_route = routes.get(route_id, {})
    route_artifact = _one_line(
        source.get("route_artifact")
        or next(iter(selected_route.get("owns_paths") or ()), "")
    )
    return {
        "schema_version": _SELECTION_SCHEMA_VERSION,
        "policy": SELECTION_POLICY,
        "route_id": route_id,
        "route_task_id": _one_line(source.get("route_task_id")),
        "review_task_id": _one_line(source.get("review_task_id")),
        "route_artifact": route_artifact,
        "review_artifact": _one_line(source.get("review_artifact")),
        "rationale": _one_line(source.get("rationale"))
        or "Selected by the prior authoritative twelve-route selector.",
        "evidence_considered": _one_line(source.get("evidence_considered"))
        or "The completed prior twelve-route portfolio and its independent reviews.",
        "resource_requirements": _one_line(source.get("resource_requirements"))
        or "Carry forward the resource requirements recorded by the selected route.",
        "unresolved_risks": (
            list(source.get("unresolved_risks"))
            if isinstance(source.get("unresolved_risks"), list)
            else []
        ),
        "rejections": rejections,
        "team_id": team_id,
        "selection_team_id": _one_line(legacy_meta.get("selection_team_id")),
        "migrated_from_legacy": True,
    }


def migrate_legacy_idea_selection(
    project_root: Path,
    *,
    state_root: Path | None = None,
    materialize_handoff: bool = True,
) -> bool:
    """Move an old completed selector into pipeline state without rerunning it."""
    project_root, state_root = _resolved_roots(project_root, state_root)
    handoff: dict[str, Any] | None = None
    migrated_selection = False
    with _state_lock(state_root):
        payload = _pipeline_payload(state_root)
        if str(payload.get("vertical") or "").strip().lower() != "research":
            return False
        selected = _valid_selected_idea(_selected_idea(payload))
        if payload.get("legacy_selection_consumed") is True:
            handoff = selected
        else:
            payload["legacy_selection_consumed"] = True
            if selected is not None:
                handoff = selected
                _write_pipeline_unlocked(state_root, payload)
            else:
                migrated = _valid_selected_idea(_legacy_selection(project_root))
                if migrated is None:
                    _write_pipeline_unlocked(state_root, payload)
                else:
                    generation = max(
                        1,
                        int(payload.get("research_intent_generation") or 1),
                    )
                    payload["research_intent_generation"] = generation
                    migrated["research_intent_generation"] = generation
                    migrated = _valid_selected_idea(migrated)
                    if migrated is None:
                        _write_pipeline_unlocked(state_root, payload)
                    else:
                        payload["selected_idea"] = migrated
                        payload["current_verdict"] = "idea_selected"
                        payload["next_action"] = (
                            "Resume the mapped research stage with the selected idea; "
                            "mapped stages must be reviewed under the current five-stage "
                            "checklist."
                        )
                        meta = _portfolio_meta(payload)
                        meta.update({
                            "schema_version": 1,
                            "generation": generation,
                            "team_id": migrated.get("team_id"),
                            "selection_team_id": migrated.get("selection_team_id"),
                            "selection_policy": SELECTION_POLICY,
                            "selection_complete": True,
                            "migrated_from_legacy": True,
                        })
                        payload["idea_portfolio"] = meta
                        _write_pipeline_unlocked(state_root, payload)
                        handoff = migrated
                        migrated_selection = True
    if materialize_handoff and handoff is not None:
        _write_handoff(project_root, handoff)
    return migrated_selection


def idea_portfolio_completion_issues(
    project_root: Path,
    *,
    state_root: Path | None = None,
) -> tuple[str, ...]:
    """Validate the internal portfolio and materialize its sole visible handoff."""
    project_root, state_root = _resolved_roots(project_root, state_root)
    if not portfolio_required(state_root):
        return ()
    migrate_legacy_idea_selection(project_root, state_root=state_root)
    payload = _pipeline_payload(state_root)
    selected = _valid_selected_idea(_selected_idea(payload))
    if selected is not None:
        _write_handoff(project_root, selected)
        return ()

    meta = _portfolio_meta(payload)
    team_id = str(meta.get("team_id") or "")
    artifact_root = str(meta.get("artifact_root") or "")
    if not _meta_matches(meta, team_id=team_id, artifact_root=artifact_root):
        return ("internal research idea portfolio state is missing or invalid",)
    root = project_root / TEAM_ROOT / team_id
    tasks = portfolio_tasks(team_id, artifact_root)
    if not task_board.material_specs_match(root, tasks):
        return ("internal research idea portfolio task board is missing or invalid",)
    selection_root = _ensure_selection_team(
        project_root,
        root=root,
        team_id=team_id,
        artifact_root=artifact_root,
        state_root=state_root,
    )
    if selection_root is None:
        return (
            "research idea portfolio has not completed all twelve route/review pairs",
        )
    selection = _task_selection(
        project_root,
        state_root,
        _portfolio_meta(_pipeline_payload(state_root)),
    )
    if selection is None:
        return ("the one-time idea selector has not completed validly",)
    if not _materialize_selection(
        project_root,
        root,
        selection_root,
        selection,
        state_root=state_root,
    ):
        return ("the selector conflicts with the selected idea in pipeline state",)
    return ()


__all__ = [
    "DEFAULT_PORTFOLIO_SIZE",
    "MAX_HANDOFF_CHARS",
    "MAX_REJECTION_TEXT_CHARS",
    "MAX_RISK_COUNT",
    "MAX_RISK_TEXT_CHARS",
    "MAX_WINNER_EXPLANATION_CHARS",
    "SELECTION_POLICY",
    "TEAM_ID",
    "ensure_idea_portfolio",
    "idea_portfolio_completion_issues",
    "idea_portfolio_selection",
    "migrate_legacy_idea_selection",
    "portfolio_required",
    "portfolio_tasks",
]
