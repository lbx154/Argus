"""Bounded canonical state supplied when the project Manager changes provider thread."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

SESSION_CONTROL_CAPSULE_BYTES = 12 * 1024
MAX_CONTINUITY_BACKLOG_BYTES = 2 * 1024 * 1024


class ManagerSessionContinuityUnavailable(RuntimeError):
    """A provider reset cannot proceed without an honest continuity handoff."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _has_current_evidence(prompt: str, run_label: str) -> bool:
    from .observation_projection import EVIDENCE_PREAMBLE

    if run_label != "manager-supervision":
        return False
    _, marker, body = prompt.partition(EVIDENCE_PREAMBLE)
    if not marker:
        return False
    try:
        facts, _end = json.JSONDecoder().raw_decode(body)
        return (
            isinstance(facts, dict) and isinstance(facts.get("objective"), str)
            and isinstance(facts.get("tasks"), list)
            and isinstance(facts.get("evidence_revision"), str)
            and re.fullmatch(r"[0-9a-f]{64}", facts["evidence_revision"]) is not None
        )
    except (ValueError, TypeError):
        return False


def _control_capsule(root: Path, *, cancelled: Callable[[], bool] | None = None) -> tuple[dict[str, Any], str]:
    """Refresh durable controls without consuming or delivering any of them."""
    from ..core.json_codec import loads_finite_json
    from ..core.pipeline_state import pipeline_state_path
    from ..core.scoped_file import open_regular_file
    from ..core.secret_guard import known_secret_values, redact_secrets_record
    from ..life.memory import Backlog, BacklogItem
    from .observation import _read_object
    from .observation_projection import MAX_ITEMS, bounded_facts, render_facts

    diagnostics: list[str] = []
    unobserved: dict[str, str] = {}
    sources: dict[str, Any] = {}

    def read(path: Path) -> dict[str, Any]:
        value = _read_object(path, limitations=diagnostics, unobserved=unobserved)
        reference = str(path.relative_to(root))
        sources[reference] = {"status": "unobserved" if str(path) in unobserved else "observed" if path.exists() else "absent"}
        if str(path) in unobserved:
            sources[reference]["unobserved_source_signature"] = unobserved[str(path)]
        else:
            sources[reference]["semantic_sha256"] = hashlib.sha256(_json(value).encode()).hexdigest()
        return value

    continuous = read(root / "continuous.json")
    directive = read(root / "active_manager_directive.json")
    latest = read(root / "manager-supervision" / "latest.json")
    receipt = latest
    identity = latest.get("id")
    if isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity):
        durable = read(root / "manager-supervision" / f"{identity}.json")
        if durable.get("id") == identity:
            receipt = durable
    pipeline = read(pipeline_state_path(root))
    campaign = read(root / "campaign-control" / "HEAD.json")

    objective = str(continuous.get("objective") or "").strip()
    objective_hash = hashlib.sha256(objective.encode()).hexdigest() if objective else ""
    recorded = str(directive.get("objective_sha256") or "")
    authorized = str(directive.get("authorized_objective") or "")
    active_directive = None
    if directive:
        if (directive.get("version") != 1 or not isinstance(directive.get("text"), str)
                or not directive["text"].strip()
                or directive.get("operator_question_policy", "unchanged") not in {"allow", "forbid", "unchanged"}):
            diagnostics.append("active_manager_directive.json: invalid directive was not projected")
        elif str(root / "continuous.json") in unobserved:
            diagnostics.append("active_manager_directive.json: objective scope could not be verified")
        elif (recorded or authorized) and not (
            (recorded and recorded == objective_hash) or (authorized and authorized == objective)
        ):
            # A historical directive for another objective is not active.
            active_directive = None
        else:
            active_directive = {key: directive[key] for key in (
                "text", "source", "revision", "operator_question_policy", "authorized_objective", "objective_sha256",
            ) if key in directive}

    # Backlog's ordinary reader tolerates a malformed trailing row. A rotation
    # must not reinterpret that as "no unanswered question", so validate its
    # recovered live file strictly while holding the same canonical lock.
    backlog = Backlog(root / "backlog.jsonl")
    raw_backlog = b""
    with backlog._locked(timeout_seconds=0.25, cancelled=cancelled):
        try:
            with open_regular_file(backlog.path) as handle:
                raw_backlog = handle.read(MAX_CONTINUITY_BACKLOG_BYTES + 1)
        except FileNotFoundError:
            pass
        if len(raw_backlog) > MAX_CONTINUITY_BACKLOG_BYTES:
            raise ValueError("live backlog exceeds the continuity read budget")
        items = [BacklogItem.from_jsonable(loads_finite_json(row))
                 for row in raw_backlog.splitlines() if row.strip()]
    questions = [{"item_id": item.id, "status": item.status, "pending_question": item.pending_question}
                 for item in items if item.pending_question]
    sources["backlog.jsonl"] = {"pending_questions_sha256": hashlib.sha256(_json(questions).encode()).hexdigest(),
                                 "pending_question_count": len(questions),
                                 "sha256": hashlib.sha256(raw_backlog).hexdigest()}
    capsule: dict[str, Any] = {"sources": sources, "unobserved": diagnostics, "pending_questions": []}
    secrets = known_secret_values()

    def fits() -> bool:
        return len(_json(capsule).encode()) <= SESSION_CONTROL_CAPSULE_BYTES - 2048

    def put(name: str, value: Any) -> None:
        capsule[name] = redact_secrets_record(value, known_values=secrets)
        if not fits():
            capsule[name] = None
            diagnostics.append(f"{name}: complete field omitted because the continuity byte budget was exceeded; consult its canonical source before acting")

    put("active_manager_directive", active_directive)
    put("supervision_receipt", {key: receipt[key] for key in (
        "id", "status", "decision", "effects", "waiting_questions", "evidence_revision", "control_revision",
    ) if key in receipt})
    omitted_questions = 0
    for question in questions:
        capsule["pending_questions"].append(redact_secrets_record(question, known_values=secrets))
        if not fits():
            capsule["pending_questions"].pop()
            omitted_questions += 1
    if omitted_questions:
        capsule["omitted_pending_question_count"] = omitted_questions
        diagnostics.append("Additional unanswered questions remain in backlog.jsonl; omitted questions are not resolved.")
    put("continuous", continuous)
    put("pipeline", pipeline)
    put("campaign", campaign)
    selected = sorted(items, key=lambda item: (item.status != "running", item.priority, item.ts))[:MAX_ITEMS]
    facts: dict[str, Any] = {
        "objective": objective,
        "tasks": [{name: getattr(item, name) for name in (
            "id", "title", "status", "objective", "pending_question", "acceptance_check", "deps",
        )} for item in selected],
        "recent_events": [],
        "evidence_refs": [{"path": path, **identity} for path, identity in sources.items()],
        "evidence_revision": hashlib.sha256(_json(sources).encode()).hexdigest(),
        "limitations": [*diagnostics, "Only the listed control and backlog sources were refreshed for this continuity handoff."],
    }
    if len(items) > len(selected):
        facts["limitations"].append(f"showing {len(selected)} of {len(items)} live tasks; unanswered questions are listed separately")
    bounded, incomplete = bounded_facts(redact_secrets_record(facts, known_values=secrets))
    capsule["incomplete_observation_requirement_count"] = len(incomplete)
    # Reuse this already recovered read. Calling observe_project here would
    # re-enter Backlog/mission-view locks with their normal unbounded waits.
    return capsule, render_facts(bounded)


def build_continuity_handoff(
    root: Path, prompt: str, run_label: str, *, cancelled: Callable[[], bool] | None = None,
) -> str:
    try:
        capsule, snapshot = _control_capsule(root, cancelled=cancelled)
        evidence = "" if _has_current_evidence(prompt, run_label) else snapshot + "\n\n"
        if len(_json(capsule).encode()) > SESSION_CONTROL_CAPSULE_BYTES:
            raise ValueError("continuity metadata exceeds its byte budget")
    except Exception as exc:
        # The session wrapper must not downgrade this to a fresh provider call
        # without the controls/questions that this handoff failed to preserve.
        raise ManagerSessionContinuityUnavailable(
            f"Manager continuity is unavailable ({type(exc).__name__}); provider session was not reset"
        ) from exc
    return (
        "## Current durable Manager continuity\n"
        "This is current project state, not a replay of old instructions. Current OperatorContext "
        "governs permissions. Historical conversation must not revive revoked directives or resolved "
        "questions. An issued supervision receipt is pending delivery, not applied; its existing "
        "recovery path owns delivery, so do not repeat or re-decide its effects. Missing fields are "
        "unknown, not permission to discard a constraint or unblock work.\n"
        + _json(capsule) + "\n\n" + evidence
    )
