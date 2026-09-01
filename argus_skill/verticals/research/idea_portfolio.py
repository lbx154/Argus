"""Durable evidence-based idea portfolios for broad paper research."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ...core.research_contract import (
    resolve_research_direction_mode,
    resolve_research_target_level,
)
from ...team import formation, pool, registry, task_board

TEAM_ID = "research-idea-pipeline-v6"
# An operating default, not a breadth quota or selection threshold. Callers may
# size the portfolio differently when the problem structure warrants it.
DEFAULT_PORTFOLIO_SIZE = 12
SELECTION_POLICY = "evidence_judgment_v3"
_REVIEW_SCHEMA_VERSION = 2
_SELECTION_SCHEMA_VERSION = 2
TEAM_ROOT = Path(".argus") / "teams"
_STATE_PATH = Path("research") / "IDEA_PORTFOLIO.json"
_SELECTION_PATH = Path("research") / "IDEA_SELECTION.json"
_REVIEW_VERDICTS = frozenset({"qualified", "rejected"})
_TEAM_TASK_ENV = "ARGUS_SKILL_TEAM_TASK_ID"
_NO_NESTED_TEAM = (
    "This task is already one worker in the parent idea portfolio. Do not create, "
    "ensure, launch, or delegate another Team or idea portfolio."
)
def portfolio_required(project_root: Path) -> bool:
    target = resolve_research_target_level(project_root)
    direction = resolve_research_direction_mode(project_root)
    return target in {"publishable", "doctoral"} and direction != "locked"


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
            "Choose a mechanism family genuinely distinct from the candidates already "
            "visible and important to the Manager's broad paper direction. Explain "
            "which key uncertainty it covers and why another route would not answer it. "
            f"Create `{output}` early and develop the strongest credible case for "
            "important, nontrivial new knowledge in whatever form the question supports, "
            "such as theory, measurement, a dataset, a method, a negative result, or a "
            "boundary condition. Do not prefer a route "
            "because it needs no training, has the shortest evidence path, is cheapest, "
            "or fits one local GPU. Feasibility is a staged resource plan, not the "
            "scientific ranking objective. "
            "Record the mechanism, primary-source trail, closest work, non-obvious gap, "
            "strongest kill argument, resource needs, and any useful probe evidence. "
            "Search the current frontier and relevant foundations deeply enough to make "
            "the novelty claim credible; preserve primary URLs and search boundaries. "
            f"{_NO_NESTED_TEAM}"
        ),
        "acceptance_check": (
            f"`{output}` makes an evidence-grounded case for a distinct mechanism family "
            "and exposes its strongest uncertainty or kill argument."
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
            "claim-critical prior art and attack the mechanism, attribution, and evidence "
            "plan. Judge whether the route could produce important, credible, nontrivial "
            "new knowledge; theory, measurements, datasets, methods, negative results, "
            "and boundary conditions are all eligible. Reject clear duplication, a "
            "trivial wrapper, an incoherent mechanism, or evidence that cannot support "
            "the claimed contribution. "
            "Do not award credit for no-training convenience, shortest evidence path, "
            "cheapness, or single-GPU fit; record resource gaps as requirements instead "
            "of using them to select a scientifically weaker route. Create one compact "
            "JSON review at "
            f"`{output}` with schema_version={_REVIEW_SCHEMA_VERSION}, route_id, "
            "verdict (`qualified` or `rejected`), a natural-language summary of the "
            "contribution and evidence, and fatal_concerns (array). Include probe "
            "evidence when it changes the judgment. "
            f"{_NO_NESTED_TEAM}"
        ),
        "acceptance_check": (
            f"`{output}` is valid review JSON with a decisive qualified/rejected "
            "verdict and an evidence-grounded contribution judgment."
        ),
        "role": "idea-review",
        "owns_paths": [output],
        "deps": [str(route_task["task_id"])],
        "target": route_id,
        "priority": 5,
    }


def portfolio_tasks(
    team_id: str = TEAM_ID,
    artifact_root: str = "research/ideation",
    portfolio_size: int = DEFAULT_PORTFOLIO_SIZE,
) -> list[dict[str, Any]]:
    if portfolio_size < 1:
        raise ValueError("portfolio_size must be positive")
    routes = [
        _route_task(
            team_id,
            artifact_root,
            f"route-{index:02d}",
        )
        for index in range(1, portfolio_size + 1)
    ]
    reviews = [_review_task(route, artifact_root) for route in routes]
    return [*routes, *reviews]


def _selection_tasks(
    team_id: str,
    artifact_root: str,
    available_review_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    specs = {task["task_id"]: task for task in portfolio_tasks(team_id, artifact_root)}
    candidates: list[dict[str, str]] = []
    for review_id in available_review_ids:
        review = specs[review_id]
        route_id = str(review_id.removesuffix("-review"))
        route = specs[route_id]
        candidates.append({
            "route_id": str(route["target"]),
            "route_task_id": route_id,
            "route_artifact": str(route["owns_paths"][0]),
            "review_task_id": review_id,
            "review_artifact": str(review["owns_paths"][0]),
        })
    selector_id = f"{team_id}-evidence-selector"
    return [
        {
            "task_id": selector_id,
            "title": "Adversarially select the strongest supported idea",
            "objective": (
                "Read every route/review pair in the manifest, then inspect all other "
                "relevant evidence that has arrived before you decide, including probes "
                "and later routes. First judge whether the portfolio covers the key "
                "uncertainties well enough to choose; if not, state what materially "
                "different evidence is missing instead of filling the selection record. "
                "When it is sufficient, choose the qualified route with the strongest "
                "case for important, credible, nontrivial new knowledge in whatever form "
                "fits the question. Let new evidence change the choice when it changes "
                "the contribution's credibility. Do not rank local convenience as "
                "scientific value; record resource gaps for the winning route. "
                "Evidence available when this selector was formed:\n"
                + json.dumps(candidates, ensure_ascii=True, indent=2)
                + "\nWrite `research/IDEA_SELECTION.json` as one JSON object with "
                f"schema_version={_SELECTION_SCHEMA_VERSION}, "
                f"policy=`{SELECTION_POLICY}`, route_id, "
                "route_task_id, review_task_id, route_artifact, review_artifact, "
                "rationale, evidence_considered, resource_requirements, and "
                "unresolved_risks (array). Select only a route whose independent review "
                "is qualified. This is a qualitative research decision, not a score. "
                f"{_NO_NESTED_TEAM}"
            ),
            "acceptance_check": (
                "`research/IDEA_SELECTION.json` records a fresh adversarial choice from "
                "the sufficiently broad evidence available."
            ),
            "role": "idea-selector",
            "owns_paths": [str(_SELECTION_PATH)],
            "target": "evidence-selection",
            "priority": 0,
        },
    ]


def _portfolio_identity(direction: str) -> tuple[str, str, str]:
    normalized = " ".join(str(direction or "").split())
    if not normalized:
        raise ValueError("broad research portfolio requires a direction")
    digest = hashlib.sha256(f"{TEAM_ID}\n{normalized}".encode("utf-8")).hexdigest()
    key = digest[:12]
    return (
        f"{TEAM_ID}-{key}",
        f"research/ideation/portfolios/{key}",
        digest,
    )


def _state_payload(project_root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((project_root / _STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(project_root: Path, payload: dict[str, Any]) -> None:
    path = project_root / _STATE_PATH
    previous_digest = str(_state_payload(project_root).get("direction_sha256") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    if previous_digest != str(payload.get("direction_sha256") or ""):
        (project_root / _SELECTION_PATH).unlink(missing_ok=True)


def _active_portfolio(
    project_root: Path,
) -> tuple[Path, str, str, str] | None:
    payload = _state_payload(project_root)
    team_id = str(payload.get("team_id") or "")
    artifact_root = str(payload.get("artifact_root") or "")
    digest = str(payload.get("direction_sha256") or "")
    key = digest[:12]
    if (
        team_id != f"{TEAM_ID}-{key}"
        or len(digest) != 64
        or artifact_root != f"research/ideation/portfolios/{key}"
    ):
        return None
    root = (project_root / TEAM_ROOT / team_id).resolve()
    try:
        root.relative_to((project_root / TEAM_ROOT).resolve())
    except ValueError:
        return None
    return root, team_id, artifact_root, digest


def _selection_team_root(project_root: Path, team_id: str) -> Path:
    return (project_root / TEAM_ROOT / f"{team_id}-selection").resolve()


def _valid_shard(root: Path, task: dict[str, Any]) -> bool:
    raw_path = str(task.get("result_shard") or "").strip()
    if not raw_path:
        return False
    path = Path(raw_path).expanduser().resolve()
    try:
        path.relative_to(root.resolve())
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    except (ValueError, OSError, IndexError):
        return False
    return bool(
        isinstance(row, dict)
        and row.get("success") is True
        and str(row.get("task_id") or "") == str(task.get("task_id") or "")
        and str(row.get("member_id") or "") == str(task.get("owner") or "")
    )


def _task_output_path(project_root: Path, task: dict[str, Any]) -> Path | None:
    owned = list(task.get("owns_paths") or [])
    if len(owned) != 1:
        return None
    path = project_root / str(owned[0])
    return path


def _json_object(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _review_payload(project_root: Path, task: dict[str, Any]) -> dict[str, Any] | None:
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


def _selection_payload(project_root: Path) -> dict[str, Any] | None:
    payload = _json_object(project_root / _SELECTION_PATH)
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
        or any(not str(payload.get(key) or "").strip() for key in required)
        or not isinstance(payload.get("unresolved_risks"), list)
    ):
        return None
    return payload


def _route_output_present(project_root: Path, task: dict[str, Any]) -> bool:
    path = _task_output_path(project_root, task)
    if path is None:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return (
        path.is_file()
        and bool(text.strip())
        and ("https://" in text or "http://" in text)
    )


def _valid_review_tasks(
    project_root: Path,
    root: Path,
    actual: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    reviews = [
        task
        for task in actual.values()
        if str(task.get("role") or "") == "idea-review"
        and task.get("state") == "done"
        and _valid_shard(root, task)
        and _review_payload(project_root, task) is not None
    ]
    reviews.sort(
        key=lambda task: (
            int(task.get("finish_seq") or 0),
            float(task.get("finished_ts") or 0),
            str(task.get("task_id") or ""),
        )
    )
    return reviews


def _available_review_ids(
    project_root: Path,
    root: Path,
    actual: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    reviews = _valid_review_tasks(project_root, root, actual)
    if not any(
        (_review_payload(project_root, task) or {}).get("verdict") == "qualified"
        for task in reviews
    ):
        return ()
    return tuple(str(task["task_id"]) for task in reviews)


def _base_state(
    project_root: Path,
    *,
    team_id: str,
    artifact_root: str,
    direction_digest: str,
) -> dict[str, Any]:
    current = _state_payload(project_root)
    payload = {
        "artifact_root": artifact_root,
        "direction_sha256": direction_digest,
        "team_id": team_id,
    }
    if (
        str(current.get("direction_sha256") or "") == direction_digest
        and str(current.get("team_id") or "") == team_id
    ):
        for key in ("selection_review_task_ids", "selection_team_id"):
            if key in current:
                payload[key] = current[key]
    return payload


def _ensure_selection_team(
    project_root: Path,
    *,
    root: Path,
    team_id: str,
    artifact_root: str,
    direction_digest: str,
) -> Path | None:
    actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(root)
    }
    state = _state_payload(project_root)
    raw_reviews = state.get("selection_review_task_ids")
    reviews = (
        tuple(str(item) for item in raw_reviews)
        if isinstance(raw_reviews, list) and raw_reviews
        else ()
    )
    if not reviews:
        reviews = _available_review_ids(project_root, root, actual)
    if not reviews:
        return None
    selection_team_id = f"{team_id}-selection"
    selection_root = _selection_team_root(project_root, team_id)
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
                "Judge whether the available independent evidence is broad enough, then "
                "adversarially select the strongest supported research contribution."
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
    payload = _base_state(
        project_root,
        team_id=team_id,
        artifact_root=artifact_root,
        direction_digest=direction_digest,
    )
    payload["selection_review_task_ids"] = list(reviews)
    payload["selection_team_id"] = selection_team_id
    _write_state(project_root, payload)
    return selection_root


def ensure_idea_portfolio(project_root: Path, *, direction: str) -> Path:
    nested_task_id = os.environ.get(_TEAM_TASK_ENV, "").strip()
    if nested_task_id:
        raise RuntimeError(
            "nested idea portfolio formation is disabled inside team task "
            f"{nested_task_id!r}"
        )
    project_root = Path(project_root).expanduser().resolve()
    team_id, artifact_root, direction_digest = _portfolio_identity(direction)
    root = project_root / TEAM_ROOT / team_id
    tasks = portfolio_tasks(team_id, artifact_root)
    route_count = sum(task.get("role") == "idea-route" for task in tasks)
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
                "Explore genuinely distinct mechanism families in parallel, review each "
                "independently, and let a fresh selector judge when the available "
                "evidence is broad enough to choose "
                f"for direction {direction_digest}."
            ),
            lead="engineer",
            cwd=project_root,
            tasks=tasks,
        )
        pool.update(root, width=route_count, state="running")
    elif (
        str(pool.read(root).get("state") or "") == "running"
        and int(pool.read(root).get("width", 0) or 0) != route_count
    ):
        pool.update(root, width=route_count, state="running")
    _write_state(
        project_root,
        _base_state(
            project_root,
            team_id=team_id,
            artifact_root=artifact_root,
            direction_digest=direction_digest,
        ),
    )
    selection_root = _ensure_selection_team(
        project_root,
        root=root,
        team_id=team_id,
        artifact_root=artifact_root,
        direction_digest=direction_digest,
    )
    selection = idea_portfolio_selection(project_root)
    if selection is not None and selection_root is not None:
        _materialize_selection(project_root, root, selection_root, selection)
    return root


def _selection_from_tasks(
    project_root: Path,
    root: Path,
    selection_root: Path,
    team_id: str,
    artifact_root: str,
    direction_digest: str,
    available_review_ids: tuple[str, ...],
) -> dict[str, Any] | None:
    selection_specs = _selection_tasks(team_id, artifact_root, available_review_ids)
    if not task_board.material_specs_match(selection_root, selection_specs):
        return None
    selection_actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(selection_root)
    }
    selector = selection_actual.get(f"{team_id}-evidence-selector", {})
    if selector.get("state") != "done":
        return None
    if not _valid_shard(selection_root, selector):
        return None
    selection = _selection_payload(project_root)
    if selection is None:
        return None
    route_task_id = str(selection.get("route_task_id") or "")
    review_task_id = str(selection.get("review_task_id") or "")
    if route_task_id != review_task_id.removesuffix("-review"):
        return None
    base_actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(root)
    }
    route = base_actual.get(route_task_id, {})
    review = base_actual.get(review_task_id, {})
    valid_review_ids = {
        str(task.get("task_id") or "")
        for task in _valid_review_tasks(project_root, root, base_actual)
    }
    review_payload = _review_payload(project_root, review)
    if (
        route.get("state") != "done"
        or review.get("state") != "done"
        or not _valid_shard(root, route)
        or not _valid_shard(root, review)
        or not _route_output_present(project_root, route)
        or review_payload is None
        or review_payload.get("verdict") != "qualified"
        or review_task_id not in valid_review_ids
        or str(selection.get("route_id") or "") != str(route.get("target") or "")
    ):
        return None
    owners = {
        str(task.get("owner") or "")
        for task in (route, review, selector)
    }
    finished_at = [
        float(task.get("finished_ts") or 0)
        for task in (route, review, selector)
    ]
    if "" in owners or len(owners) != 3 or not (
        0 < finished_at[0] <= finished_at[1] <= finished_at[2]
    ):
        return None
    return {
        **selection,
        "schema_version": _SELECTION_SCHEMA_VERSION,
        "policy": SELECTION_POLICY,
        "team_id": team_id,
        "selection_team_id": f"{team_id}-selection",
        "direction_sha256": direction_digest,
        "selected_at": float(selector.get("finished_ts") or 0),
    }


def idea_portfolio_selection(project_root: Path) -> dict[str, Any] | None:
    project_root = Path(project_root).expanduser().resolve()
    active = _active_portfolio(project_root)
    if active is None:
        return None
    root, team_id, artifact_root, direction_digest = active
    state = _state_payload(project_root)
    raw_reviews = state.get("selection_review_task_ids")
    if not isinstance(raw_reviews, list) or not raw_reviews:
        return None
    reviews = tuple(str(item) for item in raw_reviews)
    selection_root = _selection_team_root(project_root, team_id)
    return _selection_from_tasks(
        project_root,
        root,
        selection_root,
        team_id,
        artifact_root,
        direction_digest,
        reviews,
    )


def _materialize_selection(
    project_root: Path,
    root: Path,
    selection_root: Path,
    selection: dict[str, Any],
) -> None:
    path = project_root / _SELECTION_PATH
    current = _json_object(path) or {}
    merged = {**current, **selection}
    if current != merged:
        tmp = path.with_name(
            f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
        )
        try:
            tmp.write_text(
                json.dumps(merged, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    if str(pool.read(selection_root).get("state") or "") not in {
        "draining",
        "dissolved",
    }:
        pool.update(selection_root, state="draining")


def late_selection_reviews(
    project_root: Path,
) -> tuple[dict[str, str], ...]:
    """Qualified reviews that settled after the original selection evidence."""
    project_root = Path(project_root).expanduser().resolve()
    active = _active_portfolio(project_root)
    state = _state_payload(project_root)
    selection_reviews = {
        str(item) for item in state.get("selection_review_task_ids") or ()
    }
    if active is None or not selection_reviews:
        return ()
    root, team_id, artifact_root, _digest = active
    specs = {
        str(task["task_id"]): task
        for task in portfolio_tasks(team_id, artifact_root)
    }
    actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(root)
    }
    late_ids = tuple(
        sorted(
            task_id
            for task_id, spec in specs.items()
            if spec.get("role") == "idea-review" and task_id not in selection_reviews
        )
    )
    if not late_ids:
        return ()
    rows: list[dict[str, str]] = []
    for review_id in late_ids:
        review = actual.get(review_id, {})
        route_id = review_id.removesuffix("-review")
        route = actual.get(route_id, {})
        payload = _review_payload(project_root, review)
        if (
            review.get("state") != "done"
            or route.get("state") != "done"
            or not _valid_shard(root, review)
            or not _valid_shard(root, route)
            or payload is None
            or payload.get("verdict") != "qualified"
        ):
            continue
        rows.append({
            "route_task_id": route_id,
            "route_artifact": str(specs[route_id]["owns_paths"][0]),
            "review_task_id": review_id,
            "review_artifact": str(specs[review_id]["owns_paths"][0]),
            "summary": " ".join(str(payload.get("summary") or "").split()),
            "novelty_delta": " ".join(
                str(payload.get("novelty_delta") or "").split()
            ),
        })
    return tuple(rows)


def refresh_idea_portfolio(project_root: Path) -> None:
    """Keep late routes claimable after an evidence-based selection."""
    project_root = Path(project_root).expanduser().resolve()
    active = _active_portfolio(project_root)
    if active is None or idea_portfolio_selection(project_root) is None:
        return
    root, team_id, artifact_root, _digest = active
    specs = portfolio_tasks(team_id, artifact_root)
    actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(root)
    }
    state = _state_payload(project_root)
    selection_reviews = {
        str(item) for item in state.get("selection_review_task_ids") or ()
    }
    late_ids = [
        str(task["task_id"])
        for task in specs
        if task.get("role") == "idea-review"
        and str(task["task_id"]) not in selection_reviews
    ]
    unsettled = any(
        str(actual.get(task_id, {}).get("state") or "")
        not in {"done", "failed", "blocked"}
        for task_id in late_ids
    )
    if unsettled:
        marker = registry.marker_path(project_root, team_id)
        if not marker.exists():
            registry.write_marker(
                project_root,
                team_id=team_id,
                team_root=root,
                cwd=str(project_root),
                now=time.time(),
            )
        route_count = sum(task.get("role") == "idea-route" for task in specs)
        pool.update(root, width=route_count, state="running")
    elif str(pool.read(root).get("state") or "") != "dissolved":
        pool.update(root, state="draining")


def idea_portfolio_completion_issues(
    project_root: Path,
    *,
    state_root: Path | None = None,
) -> tuple[str, ...]:
    """Validate repository artifacts under ``project_root`` using state-root policy."""
    project_root = Path(project_root).expanduser().resolve()
    if not portfolio_required(state_root or project_root):
        return ()
    active = _active_portfolio(project_root)
    if active is None:
        return ("research idea portfolio state is missing or invalid",)
    root, team_id, artifact_root, direction_digest = active
    tasks = portfolio_tasks(team_id, artifact_root)
    if not task_board.material_specs_match(root, tasks):
        return ("research idea portfolio task board is missing or not canonical",)
    issues: list[str] = []
    selection_root = _ensure_selection_team(
        project_root,
        root=root,
        team_id=team_id,
        artifact_root=artifact_root,
        direction_digest=direction_digest,
    )
    if selection_root is None:
        issues.append(
            "research idea portfolio has no qualified independent review yet"
        )
        return tuple(issues)
    if int(pool.read(selection_root).get("width", 0) or 0) != 1:
        issues.append("research selection pipeline did not preserve width 1")
    selection = idea_portfolio_selection(project_root)
    if selection is not None:
        _materialize_selection(project_root, root, selection_root, selection)
        return tuple(issues)
    issues.append(
        "research adversarial selection is still incomplete"
    )
    return tuple(issues)


__all__ = [
    "DEFAULT_PORTFOLIO_SIZE",
    "SELECTION_POLICY",
    "TEAM_ID",
    "TEAM_ROOT",
    "ensure_idea_portfolio",
    "idea_portfolio_completion_issues",
    "idea_portfolio_selection",
    "late_selection_reviews",
    "portfolio_required",
    "portfolio_tasks",
    "refresh_idea_portfolio",
]
