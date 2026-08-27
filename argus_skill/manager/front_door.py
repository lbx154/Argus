"""Manager front-door routing shared by the Ink TUI and Web API."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from contextlib import nullcontext
from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Callable

from ..core.knobs import resolve_role_model
from ..core.progress_step import REPLY_KINDS
from ..core.runner_errors import is_pre_provider_refusal_error
from ..core.secret_guard import known_secret_values, redact_secrets_text

log = logging.getLogger(__name__)


class ManagerHandoffError(RuntimeError):
    """Manager did not produce a safe Planner/Engineer execution handoff."""


class ManagerHandoffSupersededError(ManagerHandoffError):
    """A newer continuous command superseded an in-flight Manager handoff."""


class ManagerModelCapabilityMismatchError(ManagerHandoffError):
    """The resolved model repeatedly failed a Manager role contract."""


def _manager_model_capability_mismatch_message(
    *,
    model_id: str,
    clause: str,
    consecutive_count: int,
) -> str:
    return (
        "[not dispatched] Manager model role-capability mismatch: "
        f"model `{model_id}` failed the Manager classification contract "
        f"{consecutive_count} consecutive times; the latest failed clause was "
        f"\"{clause}\". "
        "This is a role-capability mismatch, not a provider outage. No task was "
        "queued and no daemon was started. Change the Manager model by setting "
        "`ARGUS_SKILL_MANAGER_MODEL=<capable-model-id>` before starting Argus, "
        "or open Settings → Advanced settings, set `manager_model` to a capable "
        "model, and restart the affected Argus process. There is no `/model` "
        "command."
    )


def _publish_manager_model_capability_mismatch(
    mem: Any,
    *,
    text: str,
    model_id: str,
    clause: str,
    consecutive_count: int,
) -> None:
    """Publish through the established transcript + operator-alert event path."""
    try:
        import hashlib

        from ..core.operator_messages import publish_operator_message

        signature = hashlib.sha256(
            f"{model_id}\0{clause}\0{consecutive_count}".encode("utf-8")
        ).hexdigest()[:16]
        publish_operator_message(
            _life_dir_for(mem),
            text=text,
            message_id=f"manager-model-capability-mismatch-{signature}",
            event_fields={
                "operator_alert": True,
                "manager_model_capability_mismatch": True,
                "model_id": model_id,
                "failed_clause": clause,
                "consecutive_count": consecutive_count,
            },
        )
    except Exception:  # noqa: BLE001 - alerting must not mask fail-closed routing
        log.exception("could not publish Manager model capability mismatch alert")


def objective_update_requires_stage_reset(
    previous_objective: str,
    *updated_objectives: str,
) -> bool:
    """Return whether a continuous-objective update replaces prior work.

    Continuous objectives are commonly extended with operator clarifications,
    authorizations, or constraints.  Those monotonic additions must update the
    standing objective without resetting a certified pipeline back to its first
    stage.  A genuinely different objective still requires the replacement
    reset.  Whitespace-only rewrites are treated as the same objective.

    Callers may provide both the raw operator objective and the Manager-clean
    execution task; an additive relationship in either representation is
    sufficient to preserve the current stage.
    """

    previous = " ".join(str(previous_objective or "").split())
    if not previous:
        return False
    for candidate in updated_objectives:
        current = " ".join(str(candidate or "").split())
        if current == previous or current.startswith(f"{previous} "):
            return False
    return True


def require_manager_execution_task(division: Any) -> str:
    execution_task = str(
        getattr(division, "execution_task", "") or ""
    ).strip()
    if not execution_task:
        raise ManagerHandoffError(
            "Manager did not produce a non-empty execution_task; task was not dispatched"
        )
    return execution_task


def _life_dir_for(mem: Any) -> Path:
    """Resolve the per-project life-dir that holds ``events.jsonl``.

    Works for both ``MemoryBundle`` (``.project.root`` / ``.project_root``)
    and the bare ``LifeMemory`` facade (``.root``) used in tests.
    """
    project_root = getattr(mem, "project_root", None)
    if project_root is None:
        project = getattr(mem, "project", None)
        project_root = getattr(project, "root", None)
    if project_root is None:
        project_root = getattr(mem, "root", None)
    if project_root is None:
        raise AttributeError(
            "cannot resolve life-dir: memory has no project_root / project.root / root"
        )
    return Path(project_root)


def mission_is_running(mem: Any) -> bool:
    try:
        return any(
            str(getattr(item, "status", "") or "") == "running"
            for item in mem.backlog.active()
        )
    except Exception:  # noqa: BLE001 - routing must remain available
        return False


# ---------------------------------------------------------------------------
# Slash-command helpers (in-process; mirror the public CLI subcommands)
# ---------------------------------------------------------------------------

# Sentinel stored in chat_state when a Manager runner cannot be built (or is
# not applicable, e.g. the memory backend). Lets us cache the "no front-end
# triage" decision so we don't retry the build on every line typed.
_MANAGER_RUNNER_UNAVAILABLE = object()


class WorkspaceResolutionError(RuntimeError):
    """No trustworthy operator workspace could be resolved.

    Raised instead of guessing. The caller should treat front-door triage as
    unavailable for this turn — ``_ensure_manager_runner`` already reports a
    build failure to the operator with its reason — rather than proceed against
    a root the Manager runner must not be given write access to.
    """


def _cwd_as_workspace() -> Path:
    """The process cwd, but only when it can serve as a project workspace.

    The cwd is not a safe default here. ``spawn_detached_daemon`` runs
    ``os.chdir("/")`` (``daemon/process.py``), so a daemonized front door would
    hand the Manager runner a workspace rooted at the filesystem root — the very
    hazard ``core.sandbox.fail_closed_workdir`` exists to prevent for spawned
    roles, reintroduced one layer up. The gate brain (``~/.argus-skill``), the
    package source and the active venv are equally off-limits, for the reasons
    ``core.sandbox.forbidden_write_roots`` sets out.
    """
    from ..core.sandbox import forbidden_write_roots

    try:
        cwd = Path.cwd().resolve()
    except OSError as exc:  # cwd unlinked out from under the process
        raise WorkspaceResolutionError(
            f"cannot resolve an operator workspace: the current directory is "
            f"unavailable ({type(exc).__name__}: {exc}) and no session root was "
            f"supplied by the caller"
        ) from exc
    if cwd == Path(cwd.anchor):
        raise WorkspaceResolutionError(
            f"refusing to use {cwd} as the operator workspace: a detached daemon "
            f"chdirs to the filesystem root, so this is what an unresolved "
            f"workspace looks like, not a project. Pass an explicit session root "
            f"(mem.project_root / life_dir) from the caller."
        )
    # Same containment test as ``core.sandbox._is_forbidden``, applied to the
    # workspace we are about to hand the Manager runner.
    for root in forbidden_write_roots():
        real = os.path.realpath(root)
        if str(cwd) == real or str(cwd).startswith(real.rstrip("/") + os.sep):
            raise WorkspaceResolutionError(
                f"refusing to use {cwd} as the operator workspace: it lies under "
                f"{real}, which a Manager runner must never be able to write. "
                f"Pass an explicit session root from the caller."
            )
    return cwd


def _operator_workspace(chat_state: dict[str, Any], session_root: Any) -> Path:
    fallback = (
        Path(session_root).expanduser()
        if session_root
        else _cwd_as_workspace()
    )
    sid = str(chat_state.get("session_id") or "").strip()
    global_root = chat_state.get("global_root")
    if not sid or global_root is None:
        return fallback
    from ..core.session import read_session_meta, resolve_session_workdir

    meta = read_session_meta(Path(global_root).expanduser(), sid)
    return resolve_session_workdir(meta, state_dir=fallback)


def _ensure_manager_runner(chat_state: dict[str, Any], mem: Any) -> Any:
    """Lazily build (and cache) a Manager-front-end runner for chat triage.

    The runner is used ONLY to classify free text as chat-vs-task and, when
    chat, to reply in-band BEFORE anything reaches the backlog. It is built
    once per Manager session and cached on ``chat_state["manager_runner"]``.

    Returns the runner, or ``None`` when front-end triage is not available.
    The memory backend is permanently marked unavailable; transient build
    failures are not cached so the next operator turn can recover.
    """
    backend = chat_state.get("backend")
    # Cleared per call: the reason belongs to this attempt, and a build that
    # succeeds (or a cache hit, which is a build that already succeeded) must
    # not leave the previous turn's failure behind for the caller to report.
    chat_state.pop("manager_runner_error", None)
    # The memory backend has no real LLM runner; never triage — every line is
    # a task (preserves existing memory-backend behaviour and its tests).
    if backend == "memory":
        chat_state["manager_runner"] = _MANAGER_RUNNER_UNAVAILABLE
        return None

    try:
        # ``manager_session_root`` MUST match the daemon's own
        # ``ns.manager_session_root = str(cfg.life_dir)`` (see
        # ``daemon/life_worker.py:_runner_namespace``) — otherwise this
        # front-door Manager (built once per cockpit session, used for
        # SELF/TEAM routing + ``divide()``) reads/writes
        # ``.argus/PIPELINE_STATE.json`` and ``research/DOMAINS/*.json``
        # against a DIFFERENT root than the daemon that actually executes
        # the mission. That mismatch silently drops a Manager-authored
        # custom domain (e.g. an operator task that doesn't match any
        # built-in vertical) and logs a spurious
        # ``load_vertical(...): unknown/half-built vertical`` warning the
        # next time the daemon resolves the vertical from ITS (correct,
        # session-scoped) root. ``mem.project_root`` is the per-project
        # session dir; ``mem.root`` (used below for ``life_dir``, a
        # differently-scoped, currently-unread-by-this-path field) is the
        # GLOBAL ``~/.argus-skill`` root — do not conflate the two.
        session_root = getattr(mem, "project_root", None)
        operator_workspace = _operator_workspace(chat_state, session_root)
        workspace_key = str(operator_workspace)
        cached = chat_state.get("manager_runner")
        if (
            cached is not None
            and chat_state.get("manager_runner_workdir") == workspace_key
        ):
            return None if cached is _MANAGER_RUNNER_UNAVAILABLE else cached
        if cached is not None:
            chat_state.pop("manager_runner", None)
            chat_state.pop("manager_runner_workdir", None)
        from ..apps._runtime_construction import _resolve_role_runner_backend_name

        runner_backend = backend or "codex"
        engineer_backend = _resolve_role_runner_backend_name(
            "engineer",
            runner_backend,
        )
        reviewer_backend = _resolve_role_runner_backend_name(
            "reviewer",
            runner_backend,
        )
        ns = argparse.Namespace(
            backend=runner_backend,
            engineer_model=resolve_role_model(
                "engineer",
                role_env="ARGUS_SKILL_ENGINEER_MODEL",
                backend=engineer_backend,
            ),
            reviewer_model=resolve_role_model(
                "reviewer",
                role_env="ARGUS_SKILL_REVIEWER_MODEL",
                backend=reviewer_backend,
            ),
            engineer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh"
            ),
            reviewer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high"
            ),
            plan_mode="auto",
            plan_model=None,
            max_rounds=0,
            # The Manager uses the same persisted workdir as Planner, Engineer,
            # and Reviewer. Session state remains rooted at session_root.
            workdir=workspace_key,
            operator_workspace=str(operator_workspace),
            manager_session_root=str(session_root) if session_root else None,
            project_state_dir=str(session_root) if session_root else None,
            global_root=str(mem.global_root),
            skills_dir=os.environ.get(
                "ARGUS_SKILL_SKILLS_DIR",
                str(Path(mem.global_root) / "skills"),
            ),
            manager_memory=mem,
            life_dir=getattr(mem, "root", None),
            stop_event=None,
        )
        from ..apps._runtime import build_life_runner

        runner = build_life_runner(ns)
        acp_scope = f"manager:{chat_state.get('session_id') or workspace_key}"
        backends: list[Any] = []
        for backend in (
            getattr(runner, "_backend", None),
            getattr(runner, "manager_backend", None),
        ):
            if backend is not None and not any(backend is item for item in backends):
                backends.append(backend)
        for backend in backends:
            set_acp_scope = getattr(backend, "set_acp_scope", None)
            if callable(set_acp_scope):
                set_acp_scope(acp_scope)
    except Exception as exc:  # noqa: BLE001 — retry on the next operator turn
        # Not cached (a transient build failure must not disable triage for the
        # rest of the session), but not silent either. Everything below this
        # line — the vertical resolver, the state migration, the backend
        # construction — reports precisely what is wrong, and returning a bare
        # ``None`` collapses all of it into "classifier unavailable", which the
        # operator is shown as "please retry". Some of those faults are
        # permanent, so retrying is advice that can never work; the reason is
        # logged with its traceback and handed to the caller so the operator
        # turn can say what actually broke.
        log.exception("Manager front-door runner build failed")
        chat_state["manager_runner_error"] = f"{type(exc).__name__}: {exc}"
        return None

    chat_state["manager_runner"] = runner
    chat_state["manager_runner_workdir"] = str(operator_workspace)
    return runner


def _derive_session_name(text: str, *, limit: int = 48) -> str:
    """Create a safe deterministic fallback label from the first real task.

    The normal front-door path asks Manager to distill a concise semantic title.
    This fallback guarantees a usable label when that cosmetic model output is
    unavailable: take the first non-empty line, normalize it, and truncate.
    """
    for raw in (text or "").splitlines():
        line = " ".join(raw.split()).strip().strip("`\"'“”‘’")
        line = line.rstrip("。.!！?？;；:：")
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _maybe_name_session(
    chat_state: dict[str, Any],
    task_text: str,
    *,
    suggested_name: str = "",
    replacing: bool = False,
    promote_task_name: bool = False,
) -> str:
    """Name the current session after its first real task (once, fail-soft).

    A resumed session keeps its original name (``session_named`` is already
    True). Only the first task in a freshly-minted, still-unnamed session sets
    the display_name shown in the resume picker. Prefer Manager's concise title;
    use the deterministic first-line label only when no title was produced.

    ``replacing`` renames an already-named session, and is passed only when the
    Manager has recorded that a new operator objective *supersedes* the standing
    one. An Argus session is a long-lived daemon rather than a short chat, so a
    label taken from the very first task goes stale: an operator reported a
    session still called after a toy arithmetic question long after it had moved
    on to unrelated complex work. Renaming on every task would churn the picker;
    renaming when the session's stated purpose is replaced is the same event the
    Manager already resets the pipeline for.
    """
    provisional = str(chat_state.get("_provisional_session_name") or "").strip()
    if (
        chat_state.get("session_named")
        and not replacing
        and not (promote_task_name and provisional)
    ):
        return ""
    sid = chat_state.get("session_id")
    gr = chat_state.get("global_root")
    if not sid or gr is None:
        return ""
    try:
        from ..core.session import read_session_meta, touch_session

        persisted = read_session_meta(gr, sid)
        if persisted is not None and persisted.display_name.strip() and not replacing:
            if not (
                promote_task_name
                and provisional
                and persisted.display_name.strip() == provisional
            ):
                chat_state["session_named"] = True
                chat_state.pop("_provisional_session_name", None)
                return ""
            replacing = True
        name = (
            _derive_session_name(suggested_name, limit=32)
            or _derive_session_name(task_text)
        )
        if not name:
            return ""
        if replacing:
            # touch_session only fills an *empty* name, so it silently cannot
            # rename. A replacement has to go through the update path.
            from ..core.session import normalize_session_name, update_session_meta

            def _rename(meta: Any) -> None:
                meta.display_name = normalize_session_name(name)

            update_session_meta(gr, sid, _rename)
        else:
            touch_session(gr, sid, display_name=name)
        chat_state["session_named"] = True
        chat_state.pop("_provisional_session_name", None)
        return name
    except Exception:  # noqa: BLE001 — naming is cosmetic, never block the task
        return ""


def _emit_manager_event(mem: Any, event: dict[str, Any]) -> None:
    try:
        from ..life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=_life_dir_for(mem)).append(event)
    except Exception:  # noqa: BLE001
        pass


def _manager_current_stage(manager: Any) -> str:
    resolver = getattr(manager, "current_stage", None)
    if not callable(resolver):
        return ""
    try:
        return str(resolver() or "").strip()
    except Exception:  # noqa: BLE001 - event enrichment must never break handoff
        return ""


def _accepts_parameter(fn: Any, name: str) -> bool:
    try:
        parameters = signature(fn).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == name or parameter.kind == Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _allow_manager_route_contract_change(
    mem: Any,
    chat_state: dict[str, Any],
) -> bool:
    """Whether this operator handoff may revise the persisted route contract.

    A finite supplemental task inside an active campaign inherits that
    campaign's vertical, topology, and research bar. Outside an active campaign,
    a fresh operator handoff is allowed to select a new topology or success bar.
    An explicit standing handoff may replace the active campaign and therefore
    also needs that authority. State-read failures preserve the contract.
    """
    try:
        from ..daemon.state import read_continuous_state

        active = read_continuous_state(_life_dir_for(mem))
    except Exception:  # noqa: BLE001 - a corrupt control state must fail closed
        return False
    if not active.enabled or not active.objective.strip():
        return True
    lifetime = str(
        chat_state.get("_frontdoor_lifetime", "bounded") or "bounded"
    ).strip().lower()
    return lifetime == "standing"


def _record_goal_contract(mem: Any, body: str, decision: Any) -> None:
    """Persist the operator-originated GoalContract for this handoff.

    The Manager's parsed ``VerticalDecision`` is the only object that still
    carries operator-stated constraints. The committed ``Division`` is a runtime
    routing record and intentionally drops them, so recording from it makes the
    contract look empty even when the Manager saw requirements.

    Additive today — nothing gates completion on the contract yet (operator
    decision §9.6 exempts existing projects), so a failure to record one must
    never take down the otherwise-valid handoff. It is still surfaced as an
    event because an invisible contract write failure leaves every downstream
    role reading stale authority.
    """
    try:
        from ..core.project_contract import (
            CLAUSE_PRECISE,
            CLAUSE_SEMANTIC,
            ContractConfirmation,
            issue_confirmation,
            load_contract,
            make_clause,
            new_contract,
            revise_contract,
            save_contract,
        )

        state_dir = _life_dir_for(mem)
        clauses = []
        for text in getattr(decision, "precise_constraints", ()) or ():
            clauses.append(make_clause(CLAUSE_PRECISE, text))
        target = str(getattr(decision, "research_target_level", "") or "").strip()
        if target:
            clauses.append(
                make_clause(CLAUSE_SEMANTIC, f"research target level: {target}")
            )
        venue = str(getattr(decision, "target_venue", "") or "").strip()
        if venue:
            clauses.append(make_clause(CLAUSE_SEMANTIC, f"target venue: {venue}"))
        exclusions = tuple(getattr(decision, "exclusions", ()) or ())
        ambiguities = tuple(getattr(decision, "ambiguities", ()) or ())
        current = load_contract(state_dir)
        if current is None:
            save_contract(
                state_dir,
                contract=new_contract(
                    objective=body,
                    clauses=clauses,
                    exclusions=exclusions,
                    ambiguities=ambiguities,
                ),
            )
            return

        new_objective = str(body or "").strip()
        objective_changed = new_objective != current.objective
        # Constraints from a previous operator task are not standing policy.
        # On a new objective, replace them with what the Manager extracted from
        # the new instruction; on the same objective, an omitted field means
        # "no new information" and preserves the committed value.
        proposed_clauses = (
            tuple(clauses)
            if objective_changed or clauses
            else current.clauses
        )
        proposed_exclusions = (
            exclusions
            if objective_changed or exclusions
            else current.exclusions
        )
        proposed_ambiguities = (
            ambiguities
            if objective_changed or ambiguities
            else current.ambiguities
        )
        if (
            new_objective == current.objective
            and proposed_clauses == current.clauses
            and proposed_exclusions == current.exclusions
            and proposed_ambiguities == current.ambiguities
        ):
            return

        confirmation: ContractConfirmation | None = None
        before_precise = {clause.id for clause in current.precise()}
        after_precise = {
            clause.id
            for clause in proposed_clauses
            if clause.kind == CLAUSE_PRECISE
        }
        changed = tuple(sorted(before_precise ^ after_precise))
        if objective_changed:
            changed += ("objective",)
        if changed:
            confirmation = issue_confirmation(
                contract=current,
                covers=changed,
                issued_by="operator-front-door",
            )
        updated, revision = revise_contract(
            current=current,
            objective=new_objective,
            clauses=proposed_clauses,
            exclusions=proposed_exclusions,
            ambiguities=proposed_ambiguities,
            by="manager",
            confirmation=confirmation,
            note="operator front-door handoff",
        )
        save_contract(state_dir, contract=updated, revision=revision)
    except Exception as exc:  # noqa: BLE001 — see docstring; recording is additive
        log.debug("could not record goal contract", exc_info=True)
        _emit_manager_event(
            mem,
            {
                "type": "life.manager.goal_contract.failed",
                "agent_layer": "manager",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "text": "manager could not record goal contract",
            },
        )


@dataclass
class PreparedManagerHandoff:
    mem: Any
    body: str
    manager: Any
    decision: Any
    intent_id: str
    root_task_id: str | None
    lifetime: str = "bounded"
    continuous: bool | None = None
    open_ended: bool | None = None

    @property
    def execution_task(self) -> str:
        return require_manager_execution_task(self.decision)

    def commit(
        self,
        *,
        acquire_lock: bool = True,
        force_stage_reset: bool = False,
    ) -> Any:
        kwargs = {} if acquire_lock else {"_lock_held": True}
        division = self.manager.commit_vertical_decision(
            self.body,
            self.decision,
            ask_on_new_domain=False,
            force_stage_reset=force_stage_reset,
            **kwargs,
        )
        execution_task = require_manager_execution_task(division)
        _record_goal_contract(self.mem, execution_task, self.decision)
        return division

    def completed(
        self,
        division: Any,
        *,
        continuous_generation: int | None = None,
    ) -> None:
        workflow_mode = str(
            getattr(division, "workflow_mode", "staged") or "staged"
        ).strip().lower()
        lifetime = str(self.lifetime or "bounded").strip().lower()
        inferred_continuous = lifetime == "standing" or (
            lifetime == "bounded" and workflow_mode == "staged"
        )
        continuous = (
            self.continuous
            if self.continuous is not None
            else inferred_continuous
        )
        open_ended = (
            self.open_ended
            if self.open_ended is not None
            else lifetime == "standing"
        )
        event = {
            "type": "life.manager.intent.completed",
            "agent_layer": "manager",
            "intent_id": self.intent_id,
            "item_id": self.root_task_id,
            "source": "user",
            "objective": self.body,
            "execution_task": self.execution_task,
            "vertical": getattr(division, "vertical", ""),
            "domain": getattr(division, "domain", ""),
            "route": "team",
            "workflow_mode": workflow_mode,
            "require_independent_review": bool(
                getattr(division, "require_independent_review", True)
            ),
            "lifetime": lifetime,
            "continuous": continuous,
            "open_ended": open_ended,
            "kind": getattr(division, "kind", ""),
            "learned_vertical_status": getattr(
                division,
                "learned_vertical_status",
                "",
            ),
            "stages": list(getattr(division, "stages", []) or []),
            "reason": (
                str(getattr(self.decision, "adaptation_reason", "") or "").strip()
                or getattr(division, "headline", lambda: "")()
            ),
            "text": (
                "manager routed TEAM · "
                f"{getattr(division, 'vertical', '')} · {workflow_mode} · {lifetime}"
            ),
        }
        if continuous_generation is not None:
            event["continuous_generation"] = continuous_generation
        current_stage = _manager_current_stage(self.manager)
        if current_stage:
            event["current_stage"] = current_stage
        _emit_manager_event(self.mem, event)

    def failed(self, exc: Exception) -> None:
        raw_cause = str(getattr(exc, "cause", "") or str(exc)).strip()
        phase = str(getattr(exc, "phase", "") or "").strip()
        contract_field = str(
            getattr(exc, "contract_field", "") or ""
        ).strip()
        if not phase:
            if isinstance(exc, TimeoutError):
                phase = "timeout"
            elif "execution_task" in raw_cause:
                phase = "contract"
                contract_field = contract_field or "execution_task"
            else:
                phase = "backend"
        event = {
            "type": "life.manager.intent.failed",
            "agent_layer": "manager",
            "intent_id": self.intent_id,
            "item_id": self.root_task_id,
            "source": "user",
            "objective": self.body,
            "error": f"{type(exc).__name__}: {exc}",
            "phase": phase,
            "cause": raw_cause,
            "contract_field": contract_field,
            "attempts": max(1, int(getattr(exc, "attempts", 1) or 1)),
            "model_reply_snippet": str(
                getattr(exc, "model_reply_snippet", "") or ""
            )[:300],
            "backend_error": str(
                getattr(exc, "backend_error", "") or ""
            ).strip(),
            "text": "manager intent interpretation failed",
        }
        _emit_manager_event(self.mem, event)

    def superseded(self) -> None:
        _emit_manager_event(self.mem, {
            "type": "life.manager.intent.superseded",
            "agent_layer": "manager",
            "intent_id": self.intent_id,
            "item_id": self.root_task_id,
            "source": "user",
            "text": "newer continuous command superseded Manager handoff",
        })


def prepare_manager_execution_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
) -> PreparedManagerHandoff:
    intent_id = f"intent-{time.time_ns()}"
    lifetime = str(
        chat_state.get("_frontdoor_lifetime", "bounded") or "bounded"
    ).strip().lower()
    configured_continuous = bool(
        chat_state.get("config", {}).get("continuous", False)
    )
    configured_open_ended = bool(
        chat_state.get("_continuous_open_ended", False)
    )
    _emit_manager_event(mem, {
        "type": "life.manager.intent.started",
        "agent_layer": "manager",
        "intent_id": intent_id,
        "item_id": root_task_id,
        "source": "user",
        "objective": body,
        "text": "manager interpreting user task",
    })
    try:
        runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
        if runner is None:
            raise ManagerHandoffError("Manager runner unavailable")
        manager = runner.manager
        if manager is None:
            raise ManagerHandoffError("runner was constructed without a Manager")

        decision_kwargs: dict[str, Any] = {}
        if root_task_id is not None and _accepts_parameter(
            manager.decide_vertical,
            "root_task_id",
        ):
            decision_kwargs["root_task_id"] = root_task_id
        if _accepts_parameter(
            manager.decide_vertical,
            "allow_route_contract_change",
        ):
            decision_kwargs["allow_route_contract_change"] = (
                _allow_manager_route_contract_change(mem, chat_state)
            )
        decision = manager.decide_vertical(body, **decision_kwargs)
        require_manager_execution_task(decision)
        return PreparedManagerHandoff(
            mem=mem,
            body=body,
            manager=manager,
            decision=decision,
            intent_id=intent_id,
            root_task_id=root_task_id,
            lifetime=lifetime,
            continuous=configured_continuous,
            open_ended=configured_open_ended,
        )
    except Exception as exc:
        prepared = PreparedManagerHandoff(
            mem=mem,
            body=body,
            manager=None,
            decision=None,
            intent_id=intent_id,
            root_task_id=root_task_id,
            lifetime=lifetime,
            continuous=configured_continuous,
            open_ended=configured_open_ended,
        )
        prepared.failed(exc)
        from .classification_contract import MANAGER_CONTRACT_MISMATCH_THRESHOLD
        from .domain_author import ManagerClassificationContractError

        if (
            isinstance(exc, ManagerClassificationContractError)
            and exc.consecutive_count >= MANAGER_CONTRACT_MISMATCH_THRESHOLD
        ):
            message = _manager_model_capability_mismatch_message(
                model_id=exc.model_id,
                clause=exc.clause,
                consecutive_count=exc.consecutive_count,
            )
            _publish_manager_model_capability_mismatch(
                mem,
                text=message,
                model_id=exc.model_id,
                clause=exc.clause,
                consecutive_count=exc.consecutive_count,
            )
            raise ManagerModelCapabilityMismatchError(message) from exc
        if isinstance(exc, ManagerHandoffError):
            raise
        raise ManagerHandoffError(f"Manager handoff failed: {exc}") from exc


def _manager_divide_user_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
) -> Any:
    """Run Manager division for an operator-submitted task before enqueue.

    This is intentionally a USER-ENTRY gate. Planner-generated backlog items are
    already the Planner's decomposition and must not be routed back through
    Manager again.

    The caller surfaces progress through the Web/TUI event stream while this
    blocking Manager decision runs.
    """
    try:
        prepared = prepare_manager_execution_task(
            mem,
            body,
            chat_state,
            root_task_id=root_task_id,
            ensure_runner=ensure_runner,
        )
    except ManagerHandoffError:
        return None
    try:
        division = prepared.commit()
        prepared.completed(division)
        return division
    except Exception as exc:  # noqa: BLE001
        prepared.failed(exc)
        return None


def manager_execution_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
) -> str:
    """Return Manager's role-clean Planner/Engineer handoff or fail closed."""
    division = _manager_divide_user_task(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
    )
    return require_manager_execution_task(division)


def manager_bounded_handoff(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    persist: Callable[[str, Any], Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    prepare_persist: Callable[[str], None] | None = None,
    validate_persist: Callable[[str], None] | None = None,
    prepared_handoff: PreparedManagerHandoff | None = None,
) -> Any:
    """Commit Manager state and durable task enqueue under one pipeline lock.

    A bounded operator task submitted while a continuous campaign is active is
    supplemental work inside that campaign. The Manager may rewrite the task
    into a role-clean execution handoff, but it must not replace the standing
    campaign's vertical, stage, target level, or workflow mode.
    """
    prepared = prepared_handoff
    if prepared is None:
        prepared = prepare_manager_execution_task(
            mem,
            body,
            chat_state,
            root_task_id=root_task_id,
            ensure_runner=ensure_runner,
        )
    elif (
        prepared.mem is not mem
        or prepared.body != body
        or prepared.root_task_id != root_task_id
    ):
        raise ManagerHandoffError(
            "prepared Manager handoff does not match the bounded dispatch"
        )
    lock_factory = getattr(prepared.manager, "pipeline_lock", None)
    pipeline_lock = lock_factory() if callable(lock_factory) else nullcontext()
    try:
        if prepare_persist is not None:
            prepare_persist(prepared.execution_task)
        with pipeline_lock:
            if validate_persist is not None:
                validate_persist(prepared.execution_task)
            division = _bounded_handoff_division(
                prepared,
                chat_state=chat_state,
            )
            if division is None:
                division = prepared.commit(acquire_lock=False)
            result = persist(prepared.execution_task, division)
            prepared.completed(division)
            return result
    except Exception as exc:
        prepared.failed(exc)
        if isinstance(exc, ManagerHandoffError):
            raise
        raise ManagerHandoffError(f"Manager bounded handoff failed: {exc}") from exc


def _bounded_handoff_division(
    prepared: PreparedManagerHandoff,
    *,
    chat_state: dict[str, Any],
) -> Any | None:
    """Return a non-mutating Division for supplemental continuous work."""
    from ..daemon.state import read_continuous_state

    life_dir = _life_dir_for(prepared.mem)
    continuous = read_continuous_state(life_dir)
    if not continuous.enabled or not continuous.objective.strip():
        return None

    from ..skills.vertical_select import resolve_vertical, resolve_workflow_mode
    from ._core import Division

    project_root = Path(
        getattr(prepared.manager, "project_root", None)
        or _operator_workspace(chat_state, life_dir)
    )
    vertical = resolve_vertical(project_root)
    return Division(
        task=prepared.body,
        vertical=vertical,
        kind=prepared.manager._kind_for(vertical),
        stages=list(prepared.manager.plan_stages(vertical)),
        workflow_mode=resolve_workflow_mode(project_root),
        execution_task=prepared.execution_task,
        require_independent_review=bool(
            getattr(prepared.decision, "require_independent_review", True)
        ),
    )


def manager_continuous_handoff(
    mem: Any,
    requested_objective: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
    prepared_handoff: PreparedManagerHandoff | None = None,
    persist: Callable[[str, Any], Any] | None = None,
) -> str:
    """Atomically enable a Manager-authored continuous objective and first task."""
    from ..daemon.state import (
        compare_and_swap_continuous_config,
        read_continuous_state,
    )

    life_dir = _life_dir_for(mem)
    expected = read_continuous_state(life_dir)
    body = requested_objective.strip() or expected.objective.strip()
    if not body:
        raise ValueError("continuous mode requires a non-empty objective")
    prepared = prepared_handoff
    if prepared is None:
        prepared = prepare_manager_execution_task(
            mem,
            body,
            chat_state,
            root_task_id=root_task_id,
            ensure_runner=ensure_runner,
        )
    elif (
        prepared.mem is not mem
        or prepared.body != body
        or prepared.root_task_id != root_task_id
    ):
        raise ManagerHandoffError(
            "prepared Manager handoff does not match the continuous dispatch"
        )
    committed: dict[str, Any] = {}
    replacement_intent = bool(
        expected.objective.strip()
        and requested_objective.strip()
        and objective_update_requires_stage_reset(
            expected.objective,
            body,
            prepared.execution_task,
        )
    )

    def _commit() -> None:
        if callable(cancelled) and cancelled():
            raise ManagerHandoffError("Manager request cancelled before commit")
        committed["division"] = prepared.commit(
            acquire_lock=False,
            force_stage_reset=replacement_intent,
        )
        if replacement_intent:
            backlog = getattr(mem, "backlog", None)
            supersede = getattr(
                backlog,
                "supersede_pending_for_replacement",
                None,
            )
            if callable(supersede):
                committed["superseded_ids"] = supersede(
                    reason="operator replaced the standing Manager objective",
                    replacement_id=prepared.intent_id,
                )
            # The session's stated purpose has been replaced, so the label taken
            # from its first task is now wrong in the resume picker. This is the
            # only rename point: every other task keeps the existing name.
            _maybe_name_session(
                chat_state,
                prepared.execution_task or body,
                replacing=True,
            )
        if persist is not None:
            committed["persisted"] = persist(
                prepared.execution_task,
                committed["division"],
            )

    from ._session_ops import (
        clear_manager_pipeline_yield,
        request_manager_pipeline_yield,
    )

    yield_token = request_manager_pipeline_yield(life_dir)
    try:
        lock_factory = getattr(prepared.manager, "pipeline_lock", None)
        pipeline_lock = lock_factory() if callable(lock_factory) else nullcontext()
        with pipeline_lock:
            resolved_open_ended = bool(
                chat_state.get("_continuous_open_ended", expected.open_ended)
            )
            swapped = compare_and_swap_continuous_config(
                life_dir,
                expected=expected,
                enabled=True,
                objective=prepared.execution_task,
                open_ended=resolved_open_ended,
                before_write=_commit,
            )
    except Exception as exc:
        prepared.failed(exc)
        if isinstance(exc, ManagerHandoffError):
            raise
        raise ManagerHandoffError(f"Manager handoff commit failed: {exc}") from exc
    finally:
        clear_manager_pipeline_yield(life_dir, yield_token)
    if not swapped:
        prepared.superseded()
        current = read_continuous_state(life_dir)
        if current.generation == expected.generation:
            raise ManagerHandoffError(
                "Manager execution handoff could not be persisted"
            )
        raise ManagerHandoffSupersededError(
            "newer continuous command superseded Manager handoff"
        )
    prepared.continuous = True
    prepared.open_ended = resolved_open_ended
    prepared.lifetime = "standing" if resolved_open_ended else "bounded"
    prepared.completed(
        committed["division"],
        continuous_generation=expected.generation + 1,
    )
    for item_id in committed.get("superseded_ids", ()):
        _emit_manager_event(mem, {
            "type": "life.plan.node.superseded",
            "item_id": item_id,
            "superseded_by_plan_id": prepared.intent_id,
            "reason": "operator replaced the standing Manager objective",
            "source": "manager_intent_replacement",
        })
    return prepared.execution_task


def _fallback_request_excerpt(body: str) -> str:
    compact = " ".join(str(body or "").split())
    return compact if len(compact) <= 160 else compact[:157] + "..."


def _pre_provider_refusal_reply(exc: Exception, body: str) -> str:
    return (
        "[not dispatched] The Manager could not classify this message because "
        f"the provider call was refused before start: {exc}. "
        f"Request: {_fallback_request_excerpt(body)}"
    )


def manager_triage(mem: Any, body: str, chat_state: dict[str, Any],
                   *, on_phase: Any = None, on_fragment: Any = None,
                   route: str | None = None,
                   self_mode: str = "inspect",
                   root_task_id: str | None = None,
                   ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
                   ) -> str | None:
    """Front-door route: one-Codex SELF work returns a reply; TEAM work returns
    ``None`` so the caller queues the Argus Planner/Engineer/Reviewer pipeline.

    ``on_phase(label, *, role=...)`` — optional callback invoked at the REAL
    phase transitions (classify → reply), so a live status line reflects what
    the Manager is actually doing rather than a timed cosmetic rotation.
    ``role`` is a best-effort extra (falls back to the plain one-arg call for
    any callback that does not accept it) naming which of the four roles
    drove this update, so the caller can retint a live spinner to match.

    ``on_fragment(kind, payload)`` — optional streaming callback for a live
    front-end (the web/TUI SSE bridge). Fires ``("delta", {"text", "message_id"})``
    for each assistant reply block the instant it arrives, and ``("phase",
    {"role", "label"})`` at each phase transition.
    """
    if route is None and mission_is_running(mem):
        route = "simple"
    from ..provider_integrations.authorization_retry import BackendLoginRequired

    runner = (ensure_runner or _ensure_manager_runner)(chat_state, mem)
    if runner is None or not hasattr(runner, "chat_reply_if_conversational"):
        return None
    captured: list[str] = []
    empty_reply = (
        "[Manager reply unavailable] The SELF turn completed without an assistant "
        "message. No task was dispatched and the current mission was not changed. "
        f"Request: {_fallback_request_excerpt(body)}"
    )

    def _empty_reply_for_outcome() -> str:
        outcome = getattr(runner, "last_chat_outcome", None)
        stop_reason = _redact_live_text(
            getattr(outcome, "stop_reason", "")
        ).strip()
        if not stop_reason:
            return empty_reply
        return (
            "[Manager reply unavailable] The SELF turn stopped before producing an "
            f"assistant message: {stop_reason}. No task was dispatched and the "
            "current mission was not changed. "
            f"Request: {_fallback_request_excerpt(body)}"
        )

    def _redact_live_text(text: Any) -> str:
        return redact_secrets_text(str(text or ""), known_values=known_secret_values())

    def _fragment(kind: str, payload: dict[str, Any]) -> None:
        if not callable(on_fragment):
            return
        if kind in {"delta", "phase"}:
            payload = dict(payload)
            for key in ("text", "label", "detail"):
                if key in payload:
                    payload[key] = _redact_live_text(payload[key])
        try:
            on_fragment(kind, payload)
        except Exception:  # noqa: BLE001 — a UI callback must never break triage
            pass

    def _progress_label(event: dict[str, Any]) -> tuple[str, str] | None:
        try:
            from ..apps.cli._follow import _clean_follow_text
            txt = _redact_live_text(
                event.get("text")
                or event.get("title")
                or event.get("reason")
                or event.get("kind")
                or ""
            ).strip()
            if not txt:
                return None
            role = str(event.get("agent_layer") or "manager").strip() or "manager"
            title = {
                "manager": "Manager",
                "planner": "Planner",
                "engineer": "Engineer",
                "reviewer": "Reviewer",
            }.get(role, role.title())
            return role, title + " · " + _clean_follow_text(txt, limit=64)
        except Exception:  # noqa: BLE001
            return None

    def _emit_phase(role: str, label: str, *, kind: str = "", detail: str = "") -> None:
        # Relay every real runner phase to both callback styles so SSE sees
        # classify/direct-reply transitions instead of a generic spinner.
        safe_label = _redact_live_text(label)
        safe_detail = _redact_live_text(detail)
        if callable(on_phase):
            for kwargs in (
                {"role": role, "kind": kind, "detail": safe_detail},
                {"role": role},
                {},
            ):
                try:
                    on_phase(safe_label, **kwargs)
                    break
                except TypeError:
                    continue
                except Exception:  # noqa: BLE001 — a UI callback must never break triage
                    break
        payload: dict[str, Any] = {"role": role, "label": safe_label}
        if kind:
            payload["kind"] = kind
        if safe_detail:
            payload["detail"] = safe_detail
        _fragment("phase", payload)

    def _runner_phase(
        label: str,
        *,
        role: str = "manager",
        kind: str = "",
        detail: str = "",
    ) -> None:
        _emit_phase(
            str(role or "manager"),
            str(label or ""),
            kind=str(kind or ""),
            detail=str(detail or ""),
        )

    class _Capture:
        def __init__(self, *, progress_phases: bool) -> None:
            self._progress_phases = progress_phases
            self._last_reply_message_id = ""

        def handle_event(self, event: dict[str, Any]) -> None:
            try:
                etype = str(event.get("type") or "")
                # Tool-capable SELF turns emit narration before/between tool
                # calls and then one authoritative final answer. Sending every
                # assistant message as a reply delta glues process narration into
                # the answer. Keep only the latest id; round.main.completed below
                # carries the final text and is streamed exactly once.
                if etype == "engineer.progress" and str(event.get("kind") or "") in REPLY_KINDS:
                    self._last_reply_message_id = str(event.get("message_id") or "")
                    return
                if etype in {"loop.start", "engineer.progress"}:
                    # The current runner reports these same events through its
                    # phase_cb wrapper, already normalized as Manager activity.
                    # Only legacy runners (which reject phase_cb and hit the
                    # fallback below) need the capture sink to synthesize them.
                    if self._progress_phases:
                        parsed = _progress_label(event)
                        if parsed:
                            _emit_phase(*parsed)
                    return
                if etype != "round.main.completed":
                    return
                text = _redact_live_text(
                    _extract_chat_reply_text(str(event.get("last_message") or ""))
                )
                if text:
                    captured.append(text)
                    _fragment("delta", {
                        "text": text,
                        "message_id": self._last_reply_message_id,
                        "fragment_mode": "snapshot",
                    })
            except Exception:  # noqa: BLE001
                pass

    try:
        mode = str(self_mode or "inspect").strip().lower()
        execution_modes = {
            "micro", "implement", "debug", "review", "synthesize",
        }
        if mode not in {"reply", "inspect", *execution_modes}:
            mode = "inspect"
        triage_kwargs: dict[str, Any] = {
            "objective": body,
            "sink": _Capture(progress_phases=False),
            "seed_thread_id": (
                None
                if mode == "reply" or mode in execution_modes
                else chat_state.get("last_thread_id")
            ),
            "phase_cb": _runner_phase,
            "route": route,
        }
        if _accepts_parameter(runner.chat_reply_if_conversational, "self_mode"):
            triage_kwargs["self_mode"] = mode
        if root_task_id is not None and _accepts_parameter(
            runner.chat_reply_if_conversational,
            "root_task_id",
        ):
            triage_kwargs["root_task_id"] = root_task_id
        if runner.chat_reply_if_conversational(**triage_kwargs):
            if mode == "inspect":
                chat_state["last_thread_id"] = getattr(runner, "last_thread_id", None)
            return captured[0] if captured else _empty_reply_for_outcome()
    except TypeError:
        # Older runner without phase_cb / route support — retry without them
        # (fail-soft; the older runner will classify route internally).
        try:
            if runner.chat_reply_if_conversational(
                objective=body, sink=_Capture(progress_phases=True),
                seed_thread_id=chat_state.get("last_thread_id"),
            ):
                chat_state["last_thread_id"] = getattr(runner, "last_thread_id", None)
                return captured[0] if captured else _empty_reply_for_outcome()
        except BackendLoginRequired:
            raise
        except Exception as exc:  # noqa: BLE001 — triage failure
            if is_pre_provider_refusal_error(exc):
                return _pre_provider_refusal_reply(exc, body)
            return None
    except BackendLoginRequired:
        raise
    except Exception as exc:  # noqa: BLE001 — triage failure: bias to task
        if is_pre_provider_refusal_error(exc):
            return _pre_provider_refusal_reply(exc, body)
        return None
    return None

def _extract_chat_reply_text(msg: str) -> str:
    """Pull the human reply out of a chat result (plain text, or JSON-wrapped)."""
    msg = (msg or "").strip()
    if "📢" in msg:
        msg = msg.rsplit("📢", 1)[1].strip()
    if msg.startswith("{") and msg.endswith("}"):
        try:
            data = json.loads(msg)
            # Pending-question resolution consumes this structured Manager
            # decision downstream. Do not collapse it to its operator-facing
            # ``reply`` field before that parser sees it.
            if (
                isinstance(data.get("is_answer"), bool)
                and isinstance(data.get("resolved"), bool)
            ):
                return msg
            for key in ("reply", "message", "text", "answer", "response"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except Exception:  # noqa: BLE001
            pass
    return msg

__all__ = [
    "_accepts_parameter",
    "_MANAGER_RUNNER_UNAVAILABLE",
    "ManagerHandoffError",
    "ManagerModelCapabilityMismatchError",
    "ManagerHandoffSupersededError",
    "PreparedManagerHandoff",
    "_derive_session_name",
    "_emit_manager_event",
    "_ensure_manager_runner",
    "_extract_chat_reply_text",
    "_life_dir_for",
    "_manager_divide_user_task",
    "_maybe_name_session",
    "manager_execution_task",
    "manager_bounded_handoff",
    "manager_continuous_handoff",
    "manager_triage",
    "mission_is_running",
    "prepare_manager_execution_task",
    "require_manager_execution_task",
]
