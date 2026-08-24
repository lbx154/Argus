"""Durable quorum-based idea pipelines for broad paper research."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from ...core.research_contract import (
    resolve_research_direction_mode,
    resolve_research_target_level,
)
from ...team import formation, pool, task_board

TEAM_ID = "research-idea-pipeline-v5"
TEAM_WIDTH = 12
QUORUM_COUNT = math.ceil(TEAM_WIDTH * 0.8)
SELECTION_POLICY = "frontier_ambition_v2"
_REVIEW_SCHEMA_VERSION = 2
_SELECTION_SCHEMA_VERSION = 2
_CONTRIBUTION_MODES = frozenset(
    {"high_novelty_method", "large_scale_empirical", "both"}
)
TEAM_ROOT = Path(".argus") / "teams"
_STATE_PATH = Path("research") / "IDEA_PORTFOLIO.json"
_SELECTION_PATH = Path("research") / "IDEA_SELECTION.json"
_REVIEW_VERDICTS = frozenset({"qualified", "rejected"})
_PROBE_DECISIONS = frozenset({"continue", "skipped"})
_ROUTE_TIMEOUT_S = 20 * 60
_REVIEW_TIMEOUT_S = 10 * 60
_SELECTION_TIMEOUT_S = 10 * 60
_PROBE_TIMEOUT_S = 10 * 60
_TEAM_TASK_ENV = "ARGUS_SKILL_TEAM_TASK_ID"
_NO_NESTED_TEAM = (
    "This task is already one worker in the parent idea portfolio. Do not create, "
    "ensure, launch, or delegate another Team or idea portfolio."
)
_ROUTE_THEMES = (
    ("mechanism", "method mechanisms and algorithmic interventions"),
    ("systems", "systems architecture, runtime, and deployment failures"),
    ("learning-theory", "probability and learning-theoretic limits"),
    ("information-theory", "information-theoretic bounds and measurements"),
    ("control", "control and dynamical-systems mechanisms"),
    ("causal", "causal identification and intervention design"),
    ("game-theory", "game-theoretic incentives and multi-agent effects"),
    ("formal-methods", "formal methods, contracts, and verification limits"),
    ("evaluation", "evaluation and benchmark blind spots"),
    ("data", "data, measurement, and trace-grounded opportunities"),
    ("negative", "impossibility, negative, and boundary results"),
    ("incidents", "cross-domain incidents and unmet practitioner needs"),
)


def portfolio_required(project_root: Path) -> bool:
    target = resolve_research_target_level(project_root)
    direction = resolve_research_direction_mode(project_root)
    return target in {"publishable", "doctoral"} and direction != "locked"


def _route_task(
    team_id: str,
    artifact_root: str,
    route_id: str,
    theme: str,
) -> dict[str, Any]:
    task_id = f"{team_id}-{route_id}"
    output = f"{artifact_root}/routes/{route_id}.md"
    return {
        "task_id": task_id,
        "title": f"Investigate ideation route {route_id}",
        "objective": (
            f"Time-box this independent route to {_ROUTE_TIMEOUT_S // 60} minutes. "
            f"Investigate {theme} for the Manager's current broad paper direction. "
            f"Create `{output}` immediately, then update it progressively with one "
            "general, high-upside contribution in at least one of two modes: "
            "(A) a genuinely new method, architecture, training objective, or algorithm "
            "with a nontrivial technical delta; or (B) a publication-scale empirical "
            "study across multiple model families, datasets/tasks, strongest current "
            "baselines, and statistically defensible repeated trials. A small diagnostic, "
            "benchmark audit, or negative result qualifies only with a field-changing "
            "question and a publication-scale evidence plan. Do not prefer a route "
            "because it needs no training, has the shortest evidence path, is cheapest, "
            "or fits one local GPU. Feasibility is a staged resource plan, not the "
            "scientific ranking objective. "
            "Record a primary-source trail, closest work, non-obvious gap, strongest kill "
            "argument, and one tiny advisory observation. Use headings `## Mechanism`, "
            "`## Frontier search`, `## Primary sources`, `## Closest work`, "
            "`## Kill argument`, and `## Faithful probe`; include primary URLs. Under "
            "`## Frontier search`, record the search date, a date-sorted arXiv query "
            "covering at least the latest 12 months, the current-year proceedings or "
            "accepted-paper lists for the nearest major venues (ICLR/ICML/NeurIPS, "
            "ACL/EMNLP/NAACL, AAAI/AAMAS, as relevant), and the newest close neighbors "
            "found. Inspect foundational mathematics/physics/statistics/ML "
            "when they bear on the claim. If no close recent work exists, preserve the "
            "query and cutoff as evidence instead of silently falling back to classics. "
            "Inspect official code/benchmarks and practitioner signals when useful. "
            f"{_NO_NESTED_TEAM}"
        ),
        "acceptance_check": (
            f"`{output}` exists and contains the mechanism, dated frontier search, "
            "sources, closest work, contribution mode, publication-scale evidence plan, "
            "kill argument, and short probe sketch."
        ),
        "role": "idea-route",
        "owns_paths": [output],
        "target": route_id,
        "priority": 10,
        "timeout_s": _ROUTE_TIMEOUT_S,
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
            f"Time-box this review to {_REVIEW_TIMEOUT_S // 60} minutes. Act as a "
            f"fresh research reviewer for `{route_output}`. Verify the nearest "
            "claim-critical prior art and independently repeat a date-sorted search over "
            "the latest 12 months/current major-venue cycle. Reject stale frontier "
            "coverage, a clear prior-art duplicate, trivial wrapper, incoherent "
            "mechanism, or a proposal that is neither a high-novelty method nor a "
            "publication-scale empirical contribution. A small diagnostic or benchmark "
            "audit is not top-conference-shaped without a field-changing question and a "
            "large, decisive evaluation plan. Decide primarily from frontier freshness, "
            "technical novelty, mechanism, generality, and top-conference contribution. "
            "Do not award credit for no-training convenience, shortest evidence path, "
            "cheapness, or single-GPU fit; record resource gaps as requirements instead "
            "of using them to select a scientifically weaker route. Missing engineering "
            "detail or an untested premise alone is not a rejection reason. Create the "
            "output early, then finish "
            "exactly one JSON object at "
            f"`{output}` with schema_version={_REVIEW_SCHEMA_VERSION}, route_id, "
            "verdict (`qualified` or "
            "`rejected`), summary, technical_depth, originality, "
            "theoretical_grounding, field_significance, generality, "
            "top_conference_case, local_feasibility, contribution_mode "
            "(`high_novelty_method`, `large_scale_empirical`, or `both`), "
            "frontier_freshness, novelty_delta, publication_scale_plan, "
            "resource_requirements, fatal_concerns (array), and probe (object). "
            f"{_NO_NESTED_TEAM} A qualified probe object contains "
            "premise, evaluator_identity, comparison_identity, minimum_signal, and "
            "stop_rules."
        ),
        "acceptance_check": (
            f"`{output}` is valid review JSON with a decisive qualified/rejected "
            "verdict and a compact probe contract when qualified."
        ),
        "role": "idea-review",
        "owns_paths": [output],
        "deps": [str(route_task["task_id"])],
        "target": route_id,
        "priority": 5,
        "timeout_s": _REVIEW_TIMEOUT_S,
    }


def portfolio_tasks(
    team_id: str = TEAM_ID,
    artifact_root: str = "research/ideation",
) -> list[dict[str, Any]]:
    routes = [
        _route_task(
            team_id,
            artifact_root,
            f"route-{index:02d}-{slug}",
            theme,
        )
        for index, (slug, theme) in enumerate(_ROUTE_THEMES, 1)
    ]
    reviews = [_review_task(route, artifact_root) for route in routes]
    return [*routes, *reviews]


def _selection_tasks(
    team_id: str,
    artifact_root: str,
    quorum_review_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    specs = {task["task_id"]: task for task in portfolio_tasks(team_id, artifact_root)}
    candidates: list[dict[str, str]] = []
    for review_id in quorum_review_ids:
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
    selector_id = f"{team_id}-quorum-selector"
    probe_id = f"{team_id}-advisory-probe"
    probe_root = f"{artifact_root}/selected-probe"
    return [
        {
            "task_id": selector_id,
            "title": f"Select the strongest idea from the first {QUORUM_COUNT} reviews",
            "objective": (
                f"Read exactly the {QUORUM_COUNT} route/review pairs listed below and "
                "choose the strongest review-qualified idea by qualitative Agent "
                "judgment. Rank frontier freshness, genuine method novelty or "
                "publication-scale empirical contribution, technical depth, generality, "
                "a compelling top-conference thesis, and balanced AI-frontier and "
                "foundation grounding above local convenience. The "
                "winner must be `high_novelty_method`, `large_scale_empirical`, or "
                "`both`. Do not prefer no-training, shortest-evidence-path, cheapest, "
                "smallest-model, or single-GPU ideas. Treat local feasibility only as a "
                "requirement for a credible staged resource/compute plan; if the strongest "
                "idea needs more compute, record that gap rather than substituting a "
                "scientifically weaker diagnostic. Small diagnostics, benchmark audits, "
                "and negative results are ineligible unless their planned empirical "
                "coverage is publication-scale and the conclusion would change a "
                "field-level belief. Require independently checked current-year/latest-"
                "12-month arXiv and major-venue coverage, not merely a search performed "
                "today over older known papers. "
                "Do not inspect probe results and do not wait for the final "
                f"{TEAM_WIDTH - QUORUM_COUNT} routes. Candidate manifest:\n"
                + json.dumps(candidates, ensure_ascii=True, indent=2)
                + "\nWrite `research/IDEA_SELECTION.json` as one JSON object with "
                f"schema_version={_SELECTION_SCHEMA_VERSION}, "
                f"policy=`{SELECTION_POLICY}`, route_id, "
                "route_task_id, review_task_id, route_artifact, review_artifact, "
                "rationale, theory_strength, novelty, generality, top_conference_case, "
                "contribution_mode, frontier_freshness, novelty_delta, "
                "publication_scale_plan, resource_requirements, and unresolved_risks "
                "(array). Select only a route whose review verdict is qualified. "
                "This is a qualitative paper decision, not a metric rank. "
                f"{_NO_NESTED_TEAM}"
            ),
            "acceptance_check": (
                "`research/IDEA_SELECTION.json` selects one qualified quorum route "
                "with current-frontier evidence and either a high-novelty method or a "
                "publication-scale empirical contribution."
            ),
            "role": "idea-selector",
            "owns_paths": [str(_SELECTION_PATH)],
            "target": "quorum-selection",
            "priority": 0,
            "timeout_s": _SELECTION_TIMEOUT_S,
        },
        {
            "task_id": probe_id,
            "title": "Record an advisory feasibility note for the selected idea",
            "objective": (
                "Read `research/IDEA_SELECTION.json` and the selected route/review. "
                f"Own only `{probe_root}/`. Research does not decide whether a "
                "large-scale empirical idea succeeds; plan/benchmark/run own that "
                "question. If a representative observation below "
                f"{_PROBE_TIMEOUT_S // 60} minutes can cheaply verify plumbing, data "
                "shape, or evaluator availability without pretending to test the "
                "publication hypothesis, run it. Otherwise skip it without consuming "
                "model/API/GPU calls. Never run a full benchmark, training, broad sweep, "
                "or publication-scale multi-seed study here. Preserve any raw evidence "
                f"and write `{probe_root}/EVIDENCE.json` with decision=`continue` for "
                "an executed feasibility observation or decision=`skipped` plus "
                "idea_status=`untested` when deferring evidence downstream. A weak, null, "
                "or absent research-stage observation cannot kill, block, or downgrade "
                "the selected idea. "
                f"{_NO_NESTED_TEAM}"
            ),
            "acceptance_check": (
                f"`{probe_root}/EVIDENCE.json` honestly records one bounded feasibility "
                "observation or an untested skip; neither decides scientific success."
            ),
            "role": "idea-probe",
            "owns_paths": [probe_root],
            "deps": [selector_id],
            "target": "quorum-selection",
            "priority": 0,
            "timeout_s": _PROBE_TIMEOUT_S,
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
    if str(task.get("role") or "") == "idea-probe":
        path /= "EVIDENCE.json"
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
    required_scores = (
        "technical_depth",
        "originality",
        "theoretical_grounding",
        "field_significance",
        "generality",
        "top_conference_case",
        "local_feasibility",
        "frontier_freshness",
        "novelty_delta",
        "publication_scale_plan",
        "resource_requirements",
    )
    if (
        payload is None
        or payload.get("schema_version") != _REVIEW_SCHEMA_VERSION
        or str(payload.get("route_id") or "") != target
        or str(payload.get("verdict") or "") not in _REVIEW_VERDICTS
        or str(payload.get("contribution_mode") or "") not in _CONTRIBUTION_MODES
        or not str(payload.get("summary") or "").strip()
        or any(not str(payload.get(key) or "").strip() for key in required_scores)
        or not isinstance(payload.get("fatal_concerns"), list)
    ):
        return None
    if payload["verdict"] == "qualified":
        probe = payload.get("probe")
        required = (
            "premise",
            "evaluator_identity",
            "comparison_identity",
            "minimum_signal",
            "stop_rules",
        )
        if not isinstance(probe, dict) or any(
            not str(probe.get(key) or "").strip() for key in required
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
        "theory_strength",
        "novelty",
        "generality",
        "top_conference_case",
        "frontier_freshness",
        "novelty_delta",
        "publication_scale_plan",
        "resource_requirements",
    )
    if (
        payload is None
        or payload.get("schema_version") != _SELECTION_SCHEMA_VERSION
        or payload.get("policy") != SELECTION_POLICY
        or str(payload.get("contribution_mode") or "") not in _CONTRIBUTION_MODES
        or any(not str(payload.get(key) or "").strip() for key in required)
        or not isinstance(payload.get("unresolved_risks"), list)
    ):
        return None
    return payload


def _probe_payload(project_root: Path, task: dict[str, Any]) -> dict[str, Any] | None:
    payload = _json_object(_task_output_path(project_root, task))
    if (
        payload is None
        or not str(payload.get("idea_id") or "").strip()
        or str(payload.get("decision") or "") not in _PROBE_DECISIONS
    ):
        return None
    from .idea_evidence import validate_idea_evidence

    if validate_idea_evidence(payload):
        return None
    if payload["decision"] == "skipped" and payload.get("idea_status") != "untested":
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
    required = (
        "## Mechanism",
        "## Primary sources",
        "## Closest work",
        "## Kill argument",
        "## Faithful probe",
    )
    return (
        path.is_file()
        and not any(heading not in text for heading in required)
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


def _quorum_review_ids(
    project_root: Path,
    root: Path,
    actual: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    reviews = _valid_review_tasks(project_root, root, actual)
    if len(reviews) < QUORUM_COUNT:
        return ()
    first = reviews[:QUORUM_COUNT]
    if any((_review_payload(project_root, task) or {}).get("verdict") == "qualified"
           for task in first):
        return tuple(str(task["task_id"]) for task in first)
    later = next(
        (
            task for task in reviews[QUORUM_COUNT:]
            if (_review_payload(project_root, task) or {}).get("verdict") == "qualified"
        ),
        None,
    )
    if later is None:
        return ()
    return tuple(str(task["task_id"]) for task in [*first[:-1], later])


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
        for key in ("quorum_review_task_ids", "selection_team_id"):
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
    raw_quorum = state.get("quorum_review_task_ids")
    quorum = (
        tuple(str(item) for item in raw_quorum)
        if isinstance(raw_quorum, list) and len(raw_quorum) == QUORUM_COUNT
        else ()
    )
    if not quorum:
        quorum = _quorum_review_ids(project_root, root, actual)
    if not quorum:
        return None
    selection_team_id = f"{team_id}-selection"
    selection_root = _selection_team_root(project_root, team_id)
    tasks = _selection_tasks(team_id, artifact_root, quorum)
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
                f"Select one ICLR-grade idea after {QUORUM_COUNT}/{TEAM_WIDTH} "
                "independent reviews, then record or skip one advisory feasibility note."
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
    payload["quorum_review_task_ids"] = list(quorum)
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
                "Explore 12 broad research routes in parallel, independently review "
                f"them, and trigger selection at {QUORUM_COUNT}/{TEAM_WIDTH} reviews "
                f"for direction {direction_digest}."
            ),
            lead="engineer",
            cwd=project_root,
            tasks=tasks,
        )
        pool.update(root, width=TEAM_WIDTH, state="running")
    elif (
        str(pool.read(root).get("state") or "") == "running"
        and int(pool.read(root).get("width", 0) or 0) != TEAM_WIDTH
    ):
        pool.update(root, width=TEAM_WIDTH, state="running")
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
    quorum_review_ids: tuple[str, ...],
) -> dict[str, Any] | None:
    selection_specs = _selection_tasks(team_id, artifact_root, quorum_review_ids)
    if not task_board.material_specs_match(selection_root, selection_specs):
        return None
    selection_actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(selection_root)
    }
    selector = selection_actual.get(f"{team_id}-quorum-selector", {})
    probe = selection_actual.get(f"{team_id}-advisory-probe", {})
    if selector.get("state") != "done" or probe.get("state") != "done":
        return None
    if not _valid_shard(selection_root, selector) or not _valid_shard(selection_root, probe):
        return None
    selection = _selection_payload(project_root)
    probe_payload = _probe_payload(project_root, probe)
    if selection is None or probe_payload is None or probe_payload.get("decision") != "continue":
        return None
    route_task_id = str(selection.get("route_task_id") or "")
    review_task_id = str(selection.get("review_task_id") or "")
    if review_task_id not in quorum_review_ids or route_task_id != review_task_id.removesuffix(
        "-review"
    ):
        return None
    base_actual = {
        str(task.get("task_id") or ""): task
        for task in task_board.snapshot(root)
    }
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
        or review_payload.get("verdict") != "qualified"
        or str(selection.get("route_id") or "") != str(route.get("target") or "")
        or str(probe_payload.get("idea_id") or "") != str(route.get("target") or "")
    ):
        return None
    owners = {
        str(task.get("owner") or "")
        for task in (route, review, selector, probe)
    }
    finished_at = [
        float(task.get("finished_ts") or 0)
        for task in (route, review, selector, probe)
    ]
    if "" in owners or len(owners) != 4 or not (
        0 < finished_at[0] <= finished_at[1] <= finished_at[2] <= finished_at[3]
    ):
        return None
    return {
        **selection,
        "schema_version": _SELECTION_SCHEMA_VERSION,
        "policy": SELECTION_POLICY,
        "team_id": team_id,
        "selection_team_id": f"{team_id}-selection",
        "direction_sha256": direction_digest,
        "probe_task_id": str(probe.get("task_id") or ""),
        "probe_artifact": str(
            (_task_output_path(project_root, probe) or Path()).relative_to(project_root)
        ),
        "selected_at": float(selector.get("finished_ts") or 0),
    }


def idea_portfolio_selection(project_root: Path) -> dict[str, Any] | None:
    project_root = Path(project_root).expanduser().resolve()
    active = _active_portfolio(project_root)
    if active is None:
        return None
    root, team_id, artifact_root, direction_digest = active
    state = _state_payload(project_root)
    raw_quorum = state.get("quorum_review_task_ids")
    if not isinstance(raw_quorum, list) or len(raw_quorum) != QUORUM_COUNT:
        return None
    quorum = tuple(str(item) for item in raw_quorum)
    selection_root = _selection_team_root(project_root, team_id)
    return _selection_from_tasks(
        project_root,
        root,
        selection_root,
        team_id,
        artifact_root,
        direction_digest,
        quorum,
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
    for campaign_root in (root, selection_root):
        if str(pool.read(campaign_root).get("state") or "") not in {
            "draining",
            "dissolved",
        }:
            pool.update(campaign_root, state="draining")


def idea_portfolio_completion_issues(project_root: Path) -> tuple[str, ...]:
    project_root = Path(project_root).expanduser().resolve()
    if not portfolio_required(project_root):
        return ()
    active = _active_portfolio(project_root)
    if active is None:
        return ("research idea portfolio state is missing or invalid",)
    root, team_id, artifact_root, direction_digest = active
    tasks = portfolio_tasks(team_id, artifact_root)
    if not task_board.material_specs_match(root, tasks):
        return ("research idea portfolio task board is missing or not canonical",)
    issues: list[str] = []
    if int(pool.read(root).get("width", 0) or 0) != TEAM_WIDTH:
        issues.append("research idea portfolio did not preserve width 12")
    selection_root = _ensure_selection_team(
        project_root,
        root=root,
        team_id=team_id,
        artifact_root=artifact_root,
        direction_digest=direction_digest,
    )
    if selection_root is None:
        issues.append(
            f"research idea pipeline has fewer than {QUORUM_COUNT} completed "
            "independent reviews or no qualified candidate yet"
        )
        return tuple(issues)
    if int(pool.read(selection_root).get("width", 0) or 0) != 1:
        issues.append("research selection pipeline did not preserve width 1")
    selection = idea_portfolio_selection(project_root)
    if selection is not None:
        _materialize_selection(project_root, root, selection_root, selection)
        return tuple(issues)
    issues.append(
        "research quorum selection or its short advisory probe is still incomplete"
    )
    return tuple(issues)


__all__ = [
    "QUORUM_COUNT",
    "SELECTION_POLICY",
    "TEAM_ID",
    "TEAM_ROOT",
    "TEAM_WIDTH",
    "ensure_idea_portfolio",
    "idea_portfolio_completion_issues",
    "idea_portfolio_selection",
    "portfolio_required",
    "portfolio_tasks",
]
