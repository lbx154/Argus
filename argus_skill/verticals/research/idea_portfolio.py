"""Durable evidence-based idea portfolios for broad paper research."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ...core.file_lock import exclusive_file_lock
from ...core.research_contract import (
    resolve_research_direction_mode,
    resolve_research_target_level,
)
from ...team import formation, pool, roster, task_board

TEAM_ID = "research-idea-pipeline-v7"
DEFAULT_PORTFOLIO_SIZE = 12
SELECTION_POLICY = "fixed_twelve_source_only_v5"
SELECTION_TEAM_SUFFIX = "selection-v2"
_REVIEW_SCHEMA_VERSION = 2
_SELECTION_SCHEMA_VERSION = 2
TEAM_ROOT = Path(".argus") / "teams"
_STATE_PATH = Path("research") / "IDEA_PORTFOLIO.json"
_SELECTION_PATH = Path("research") / "IDEA_SELECTION.json"
_SELECTION_FREEZE_PATH = Path(".argus") / "IDEA_SELECTION_FREEZE.json"
_STATE_LOCK_PATH = Path(".argus") / "IDEA_PORTFOLIO.lock"
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
            "strongest kill argument, resource needs, and the future decisive experiment. "
            "Search the current frontier and relevant foundations deeply enough to make "
            "the novelty claim credible; preserve primary URLs and search boundaries. "
            "Idea selection is read-only: do not execute candidate code or run toy, "
            "premise, feasibility, smoke, or other probe experiments. Describe how the "
            "selected idea should later be tested without producing result evidence now. "
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
            "contribution and evidence, and fatal_concerns (array). Review only the "
            "primary-source trail, official source inspection, mechanism, and future "
            "evidence plan. Do not request or run an experiment during idea selection. "
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
) -> list[dict[str, Any]]:
    routes = [
        _route_task(
            team_id,
            artifact_root,
            f"route-{index:02d}",
        )
        for index in range(1, DEFAULT_PORTFOLIO_SIZE + 1)
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
    output = f"{artifact_root}/{SELECTION_TEAM_SUFFIX}.json"
    return [
        {
            "task_id": selector_id,
            "title": "Adversarially select the strongest supported idea",
            "objective": (
                "Read all twelve route/review pairs in the manifest and choose exactly "
                "one route with the strongest "
                "case for important, credible, nontrivial new knowledge in whatever form "
                "fits the question. The twelve independent reviews inform the comparison; "
                "their concerns become explicit plan and implementation obligations rather "
                "than a reason to run another search round. Do not rank local convenience "
                "as scientific value; record resource gaps for the winning route. "
                "Selection must precede candidate execution. Do not request or run a "
                "toy, premise, feasibility, smoke, or other probe experiment, and do not "
                "use a legacy pre-selection probe outcome to rank candidates. "
                "Evidence available when this selector was formed:\n"
                + json.dumps(candidates, ensure_ascii=True, indent=2)
                + f"\nWrite `{output}` as one JSON object with "
                f"schema_version={_SELECTION_SCHEMA_VERSION}, "
                f"policy=`{SELECTION_POLICY}`, route_id, "
                "route_task_id, review_task_id, route_artifact, review_artifact, "
                "rationale, evidence_considered, resource_requirements, and "
                "unresolved_risks (array). This is the portfolio's one selection decision: "
                "after writing it, advance to plan and do not reopen idea search unless "
                "the operator explicitly changes the research direction. "
                "This is a qualitative research decision, not a score. "
                f"{_NO_NESTED_TEAM}"
            ),
            "acceptance_check": (
                f"`{output}` records exactly one adversarial choice after all twelve "
                "routes and all twelve independent reviews were completed."
            ),
            "role": "idea-selector",
            "owns_paths": [output],
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


@contextmanager
def _state_lock(project_root: Path) -> Iterator[None]:
    path = project_root / _STATE_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        with exclusive_file_lock(handle, lock_name="idea portfolio state"):
            yield


def _write_state_unlocked(project_root: Path, payload: dict[str, Any]) -> None:
    path = project_root / _STATE_PATH
    previous = _state_payload(project_root)
    previous_digest = str(previous.get("direction_sha256") or "")
    previous_policy = str(previous.get("selection_policy") or "")
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
    if (
        previous_digest != str(payload.get("direction_sha256") or "")
        or previous_policy != str(payload.get("selection_policy") or "")
    ):
        (project_root / _SELECTION_PATH).unlink(missing_ok=True)
        (project_root / _SELECTION_FREEZE_PATH).unlink(missing_ok=True)


def _write_state(project_root: Path, payload: dict[str, Any]) -> None:
    with _state_lock(project_root):
        _write_state_unlocked(project_root, payload)


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
    return (project_root / TEAM_ROOT / f"{team_id}-{SELECTION_TEAM_SUFFIX}").resolve()


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


def _selection_payload(
    project_root: Path,
    path: Path = _SELECTION_PATH,
) -> dict[str, Any] | None:
    payload = _json_object(project_root / path)
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


def _selection_digest(selection: dict[str, Any]) -> str:
    encoded = json.dumps(
        selection,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _selection_freeze(project_root: Path) -> dict[str, Any] | None:
    return _json_object(project_root / _SELECTION_FREEZE_PATH)


def _selection_is_frozen(
    project_root: Path,
    *,
    team_id: str,
    direction_digest: str,
) -> bool:
    frozen = _selection_freeze(project_root)
    return bool(
        frozen
        and frozen.get("schema_version") == 1
        and frozen.get("team_id") == team_id
        and frozen.get("direction_sha256") == direction_digest
        and len(str(frozen.get("selection_sha256") or "")) == 64
    )


def _freeze_selection(
    project_root: Path,
    selection: dict[str, Any],
) -> tuple[bool, str]:
    digest = _selection_digest(selection)
    expected = {
        "schema_version": 1,
        "team_id": str(selection.get("team_id") or ""),
        "direction_sha256": str(selection.get("direction_sha256") or ""),
        "route_id": str(selection.get("route_id") or ""),
        "selection_sha256": digest,
    }
    path = project_root / _SELECTION_FREEZE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(expected, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            pass
        except OSError:
            path.unlink(missing_ok=True)
            raise
    return _selection_freeze(project_root) == expected, digest


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
    valid_reviews = {
        str(task["task_id"])
        for task in _valid_review_tasks(project_root, root, actual)
    }
    if valid_reviews != review_ids:
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
        "selection_policy": SELECTION_POLICY,
        "team_id": team_id,
    }
    if (
        str(current.get("direction_sha256") or "") == direction_digest
        and str(current.get("team_id") or "") == team_id
        and str(current.get("selection_policy") or "") == SELECTION_POLICY
    ):
        expected_review_ids = sorted(
            str(task["task_id"])
            for task in portfolio_tasks(team_id, artifact_root)
            if task.get("role") == "idea-review"
        )
        raw_review_ids = current.get("selection_review_task_ids")
        if (
            isinstance(raw_review_ids, list)
            and sorted(str(item) for item in raw_review_ids) == expected_review_ids
        ):
            payload["selection_review_task_ids"] = raw_review_ids
        selection_team_id = str(current.get("selection_team_id") or "")
        if selection_team_id in {
            f"{team_id}-selection",
            f"{team_id}-{SELECTION_TEAM_SUFFIX}",
        }:
            payload["selection_team_id"] = selection_team_id
        selection_sha256 = str(current.get("selection_sha256") or "")
        if len(selection_sha256) == 64 and all(
            char in "0123456789abcdef" for char in selection_sha256
        ):
            payload["selection_sha256"] = selection_sha256
            payload["selected_route_id"] = str(
                current.get("selected_route_id") or ""
            )
    return payload


def _state_identifies(
    state: dict[str, Any],
    *,
    team_id: str,
    artifact_root: str,
    direction_digest: str,
) -> bool:
    return bool(
        state.get("team_id") == team_id
        and state.get("artifact_root") == artifact_root
        and state.get("direction_sha256") == direction_digest
    )


def _dissolve_stale_transition(*roots: Path) -> None:
    for root in roots:
        if not root.is_dir():
            continue
        for task in task_board.snapshot(root):
            if (
                task.get("role") == "idea-selector"
                and task.get("state") in {"pending", "claimed", "running"}
            ):
                task_board.fail(
                    root,
                    str(task["task_id"]),
                    reason="superseded by a newer research direction",
                )
        roster.set_state(root, "dissolved")
        pool.update(root, width=0, state="dissolved")


def _ensure_selection_team(
    project_root: Path,
    *,
    root: Path,
    team_id: str,
    artifact_root: str,
    direction_digest: str,
) -> Path | None:
    selection_team_id = f"{team_id}-{SELECTION_TEAM_SUFFIX}"
    selection_root = _selection_team_root(project_root, team_id)
    with _state_lock(project_root):
        state = _state_payload(project_root)
        if not _state_identifies(
            state,
            team_id=team_id,
            artifact_root=artifact_root,
            direction_digest=direction_digest,
        ):
            return None
        previous_selection_ids = {
            str(state.get("selection_team_id") or ""),
            f"{team_id}-selection",
        }
        normalized_state = _base_state(
            project_root,
            team_id=team_id,
            artifact_root=artifact_root,
            direction_digest=direction_digest,
        )
        if state != normalized_state:
            _write_state_unlocked(project_root, normalized_state)
            state = normalized_state
    previous_selection_ids.discard("")
    previous_selection_ids.discard(selection_team_id)
    teams_root = (project_root / TEAM_ROOT).resolve()
    for previous_selection_id in previous_selection_ids:
        previous_root = (teams_root / previous_selection_id).resolve()
        try:
            previous_root.relative_to(teams_root)
        except ValueError:
            continue
        if previous_root.is_dir():
            for task in task_board.snapshot(previous_root):
                if (
                    task.get("role") == "idea-selector"
                    and task.get("state") in {"pending", "claimed", "running"}
                ):
                    task_board.fail(
                        previous_root,
                        str(task["task_id"]),
                        reason="superseded by fixed twelve-route selection policy",
                    )
            roster.set_state(previous_root, "dissolved")
            pool.update(previous_root, width=0, state="dissolved")
    actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(root)
    }
    if not _selection_is_frozen(
        project_root,
        team_id=team_id,
        direction_digest=direction_digest,
    ):
        retried = _retry_invalid_terminal_tasks(
            project_root,
            root,
            actual,
            team_id=team_id,
            artifact_root=artifact_root,
        )
        if retried:
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
            pool.update(
                root,
                width=DEFAULT_PORTFOLIO_SIZE,
                state="running",
            )
    reviews = _available_review_ids(
        project_root,
        root,
        actual,
        team_id=team_id,
        artifact_root=artifact_root,
    )
    if not reviews:
        with _state_lock(project_root):
            current = _state_payload(project_root)
            stale = not _state_identifies(
                current,
                team_id=team_id,
                artifact_root=artifact_root,
                direction_digest=direction_digest,
            )
        if stale:
            _dissolve_stale_transition(root, selection_root)
        return None
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
                "After all twelve routes and reviews finish, select the single strongest "
                "research contribution once and advance to planning."
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
    if (
        not _selection_is_frozen(
            project_root,
            team_id=team_id,
            direction_digest=direction_digest,
        )
        and selector.get("state") in {"done", "failed"}
        and _selection_from_tasks(
            project_root,
            root,
            selection_root,
            team_id,
            artifact_root,
            direction_digest,
            reviews,
        )
        is None
        and task_board.retry_terminal(selection_root, str(selector["task_id"]))
    ):
        pool.update(selection_root, width=1, state="running")
    with _state_lock(project_root):
        current = _state_payload(project_root)
        if not _state_identifies(
            current,
            team_id=team_id,
            artifact_root=artifact_root,
            direction_digest=direction_digest,
        ):
            stale = True
        else:
            stale = False
            payload = _base_state(
                project_root,
                team_id=team_id,
                artifact_root=artifact_root,
                direction_digest=direction_digest,
            )
            payload["selection_review_task_ids"] = list(reviews)
            payload["selection_team_id"] = selection_team_id
            if current != payload:
                _write_state_unlocked(project_root, payload)
    if stale:
        _dissolve_stale_transition(root, selection_root)
        return None
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
    with _state_lock(project_root):
        previous = _state_payload(project_root)
        previous_team_id = str(previous.get("team_id") or "")
        if previous_team_id and previous_team_id != team_id:
            previous_ids = {
                previous_team_id,
                str(previous.get("selection_team_id") or ""),
            }
            teams_root = (project_root / TEAM_ROOT).resolve()
            for previous_id in previous_ids:
                if not previous_id:
                    continue
                previous_root = (teams_root / previous_id).resolve()
                try:
                    previous_root.relative_to(teams_root)
                except ValueError:
                    continue
                if previous_root.is_dir():
                    for task in task_board.snapshot(previous_root):
                        if (
                            task.get("role") == "idea-selector"
                            and task.get("state") in {"pending", "claimed", "running"}
                        ):
                            task_board.fail(
                                previous_root,
                                str(task["task_id"]),
                                reason="superseded by source-only selection policy",
                            )
                    roster.set_state(previous_root, "dissolved")
                    pool.update(previous_root, width=0, state="dissolved")
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
                    "Complete exactly twelve distinct mechanism routes and one independent "
                    "review for each, then let one fresh selector choose exactly once "
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
        _write_state_unlocked(
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
    if (
        selection is not None
        and selection_root is not None
        and selection.get("team_id") == team_id
    ):
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
    selection_actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(selection_root)
    }
    selector = selection_actual.get(f"{team_id}-evidence-selector", {})
    if selector.get("state") != "done":
        return None
    if not _valid_shard(selection_root, selector):
        return None
    selection_path = _task_output_path(project_root, selector)
    if selection_path is None:
        return None
    selection = _selection_payload(
        project_root,
        selection_path.relative_to(project_root),
    )
    if selection is None:
        return None
    route_task_id = str(selection.get("route_task_id") or "")
    review_task_id = str(selection.get("review_task_id") or "")
    if route_task_id != review_task_id.removesuffix("-review"):
        return None
    route = base_actual.get(route_task_id, {})
    review = base_actual.get(review_task_id, {})
    review_payload = _review_payload(project_root, review)
    if (
        route.get("state") != "done"
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
    selector_owner = str(selector.get("owner") or "")
    finished_at = [
        float(task.get("finished_ts") or 0)
        for task in (route, review, selector)
    ]
    latest_review_finished_at = max(
        float(base_actual[review_id].get("finished_ts") or 0)
        for review_id in canonical_review_ids
    )
    if (
        not route_owner
        or not review_owner
        or not selector_owner
        or route_owner == review_owner
        or not (0 < finished_at[0] <= finished_at[1] <= finished_at[2])
        or finished_at[2] < latest_review_finished_at
    ):
        return None
    return {
        **selection,
        "schema_version": _SELECTION_SCHEMA_VERSION,
        "policy": SELECTION_POLICY,
        "team_id": team_id,
        "selection_team_id": f"{team_id}-{SELECTION_TEAM_SUFFIX}",
        "direction_sha256": direction_digest,
        "selected_at": float(selector.get("finished_ts") or 0),
    }


def _task_selection(
    project_root: Path,
    active: tuple[Path, str, str, str],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    root, team_id, artifact_root, direction_digest = active
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


def idea_portfolio_selection(project_root: Path) -> dict[str, Any] | None:
    project_root = Path(project_root).expanduser().resolve()
    with _state_lock(project_root):
        active = _active_portfolio(project_root)
        if active is None:
            return None
        _root, team_id, _artifact_root, direction_digest = active
        state = _state_payload(project_root)
        freeze_path = project_root / _SELECTION_FREEZE_PATH
        if freeze_path.exists():
            frozen = _selection_freeze(project_root)
            canonical = _selection_payload(project_root)
            if (
                frozen is None
                or canonical is None
                or state.get("selection_policy") != SELECTION_POLICY
                or frozen.get("schema_version") != 1
                or frozen.get("team_id") != team_id
                or frozen.get("direction_sha256") != direction_digest
                or frozen.get("route_id") != canonical.get("route_id")
                or frozen.get("selection_sha256") != _selection_digest(canonical)
                or canonical.get("team_id") != team_id
                or canonical.get("direction_sha256") != direction_digest
            ):
                return None
            return canonical
        return _task_selection(project_root, active, state)


def _materialize_selection(
    project_root: Path,
    root: Path,
    selection_root: Path,
    selection: dict[str, Any],
) -> bool:
    selection_team_id = str(selection.get("team_id") or "")
    expected_root = (project_root / TEAM_ROOT / selection_team_id).resolve()
    expected_selection_root = _selection_team_root(project_root, selection_team_id)
    if (
        root.resolve() != expected_root
        or selection_root.resolve() != expected_selection_root
        or selection.get("selection_team_id") != expected_selection_root.name
    ):
        return False
    with _state_lock(project_root):
        state = _state_payload(project_root)
        if (
            state.get("team_id") != selection.get("team_id")
            or state.get("direction_sha256") != selection.get("direction_sha256")
            or state.get("selection_policy") != SELECTION_POLICY
            or selection.get("policy") != SELECTION_POLICY
        ):
            return False
        frozen, selection_sha256 = _freeze_selection(project_root, selection)
        if not frozen:
            return False
        if (
            state.get("selection_sha256") != selection_sha256
            or state.get("selected_route_id") != selection.get("route_id")
        ):
            state["selection_sha256"] = selection_sha256
            state["selected_route_id"] = selection["route_id"]
            _write_state_unlocked(project_root, state)
        path = project_root / _SELECTION_PATH
        current = _json_object(path) or {}
        merged = dict(selection)
        if current != merged:
            tmp = path.with_name(
                f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}."
                f"{uuid.uuid4().hex[:8]}"
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
    if str(pool.read(root).get("state") or "") not in {"draining", "dissolved"}:
        pool.update(root, state="draining")
    return True


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
            "research idea portfolio has not completed all twelve valid route/review pairs"
        )
        return tuple(issues)
    if int(pool.read(selection_root).get("width", 0) or 0) != 1:
        issues.append("research selection pipeline did not preserve width 1")
    state = _state_payload(project_root)
    task_selection = _task_selection(project_root, active, state)
    if _selection_is_frozen(
        project_root,
        team_id=team_id,
        direction_digest=direction_digest,
    ):
        frozen = _selection_freeze(project_root) or {}
        if (
            task_selection is None
            or frozen.get("selection_sha256") != _selection_digest(task_selection)
            or not _materialize_selection(
                project_root,
                root,
                selection_root,
                task_selection,
            )
        ):
            issues.append(
                "research idea selection conflicts with the frozen one-time decision"
            )
        return tuple(issues)
    if task_selection is not None:
        if not _materialize_selection(
            project_root,
            root,
            selection_root,
            task_selection,
        ):
            issues.append(
                "research idea selection conflicts with the frozen one-time decision"
            )
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
    "portfolio_required",
    "portfolio_tasks",
]
