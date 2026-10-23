"""Manager front-door routing shared by the Ink TUI and Web API."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from ..core.knobs import resolve_role_model
from ..core.progress_step import REPLY_KINDS

# Minimum spacing between live reply snapshots sent over SSE.
_LIVE_DELTA_INTERVAL_S = 0.06

# (progress-event key, SSE phase field) pairs carried on tool phases.
_PHASE_META_FIELDS = (
    ("tool_name", "tool"),
    ("call_id", "call_id"),
    ("tool_kind", "tool_kind"),
    ("status", "status"),
    ("exit_code", "exit_code"),
    ("output_excerpt", "output"),
)
from ..core.runner_errors import is_pre_provider_refusal_error
from ..core.secret_guard import known_secret_values, redact_secrets_text
from ..core.session import topic_name

if TYPE_CHECKING:
    from ..core.handoff import HandOff

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
        from ..core.operator_messages import publish_operator_message

        publish_operator_message(
            _life_dir_for(mem),
            text=text,
            message_id=f"manager-model-capability-mismatch:{model_id}:{clause}:{consecutive_count}",
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
    hand the Manager runner a workspace rooted at the filesystem root. The gate
    brain (``~/.argus-skill``), the package source and the active venv are
    equally off-limits, for the reasons ``core.sandbox.forbidden_write_roots``
    sets out.
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
    # Containment by real path, applied to the workspace we are about to hand
    # the Manager runner.
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
        # A memory opened by session id takes the server's cwd for its
        # worktree; the Manager's own recall reads the project's Wiki and
        # Skills from the workspace the Manager works in.
        mem.project_worktree = operator_workspace
        runner_backend = backend or "codex"
        ns = argparse.Namespace(
            backend=runner_backend,
            engineer_model=resolve_role_model(
                "engineer",
                role_env="ARGUS_SKILL_ENGINEER_MODEL",
                backend=runner_backend,
            ),
            reviewer_model=resolve_role_model(
                "reviewer",
                role_env="ARGUS_SKILL_REVIEWER_MODEL",
                backend=runner_backend,
            ),
            engineer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh"
            ),
            reviewer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high"
            ),
            plan_mode="auto",
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
    """Normalize one proposed label; this does not summarize a user request."""
    for raw in (text or "").splitlines():
        line = " ".join(raw.split()).strip().strip("`\"'“”‘’")
        line = line.rstrip("。.!！?？;；:：")
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


# ``name_source`` values owned by the Agent. ``agent`` titles came from a turn
# that started no task (greeting, chit-chat); the first real task may replace
# them. ``agent_task`` titles came from a task and change only when the
# classifier judges the operator moved to a new topic.
TASK_NAME_SOURCE = "agent_task"
AGENT_NAME_SOURCES = frozenset({"agent", TASK_NAME_SOURCE})


def _maybe_name_session(
    chat_state: dict[str, Any],
    task_text: str,
    *,
    suggested_name: str = "",
    replacing: bool = False,
    promote_task_name: bool = False,
) -> str:
    """Persist an Agent's topic summary, preserving manual names atomically.

    Missing cosmetic output leaves naming open for the next Agent turn; raw
    user instructions are never promoted to a permanent title. The classifier
    decides when a topic changed; later execution handoffs only fill blanks.
    """
    _ = task_text  # Retained for callers; never use a truncated task as a title.
    name = topic_name(_derive_session_name(suggested_name, limit=32))
    if not name:
        return ""
    sid = chat_state.get("session_id")
    gr = chat_state.get("global_root")
    if not sid or gr is None:
        return ""
    try:
        from ..core.session import (
            PROVISIONAL_NAME_SOURCE,
            normalize_session_name,
            update_session_meta,
        )

        changed = False
        # The front door already titled this turn's topic in the operator's
        # language; the task handoff then only marks that title as task-owned.
        fresh_turn_title = bool(chat_state.pop("_fresh_agent_name", False))

        def _rename(meta: Any) -> None:
            nonlocal changed
            source = meta.name_source
            named = bool(meta.display_name.strip()) and source != PROVISIONAL_NAME_SOURCE
            if named and source not in AGENT_NAME_SOURCES:
                return  # operator-owned title
            if named and not replacing:
                # A task may replace a title given to earlier chit-chat once;
                # after that only a classifier-judged topic change renames.
                if not promote_task_name or source != "agent":
                    return
                if fresh_turn_title:
                    meta.name_source = TASK_NAME_SOURCE
                    return
            normalized = normalize_session_name(name)
            target_source = TASK_NAME_SOURCE if (
                promote_task_name or source == TASK_NAME_SOURCE
            ) else "agent"
            if meta.display_name == normalized and source == target_source:
                return
            meta.display_name = normalized
            meta.name_source = target_source
            changed = True

        update_session_meta(gr, sid, _rename, create=True)
        return name if changed else ""
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
        chat_state.get("_team_lifetime", "bounded") or "bounded"
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
            AUTHORITY_MANAGER,
            AUTHORITY_OPERATOR,
            CLAUSE_PRECISE,
            CLAUSE_SEMANTIC,
            ContractConfirmation,
            confirmation_changes,
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
        venue = str(getattr(decision, "target_venue", "") or "").strip()
        if target:
            # The Manager always picks a level; it is the operator's only when
            # the operator named a venue or chose the level in launch settings.
            from ..core.launch_settings import launch_target_level_stated

            stated = bool(venue) or launch_target_level_stated(body)
            clauses.append(make_clause(
                CLAUSE_SEMANTIC, f"research target level: {target}",
                AUTHORITY_OPERATOR if stated else AUTHORITY_MANAGER,
            ))
        if venue:
            clauses.append(make_clause(CLAUSE_SEMANTIC, f"target venue: {venue}", AUTHORITY_OPERATOR))
        exclusions = tuple(getattr(decision, "exclusions", ()) or ())
        assumptions = tuple(
            f"{row['chose']} (rather than {row['instead_of']})"
            for row in getattr(decision, "assumptions", ())
        )
        current = load_contract(state_dir)
        if current is None:
            save_contract(
                state_dir,
                contract=new_contract(
                    objective=body,
                    clauses=clauses,
                    exclusions=exclusions,
                    assumptions=assumptions,
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
        proposed_assumptions = (
            assumptions
            if objective_changed or assumptions
            else current.assumptions
        )
        if (
            new_objective == current.objective
            and proposed_clauses == current.clauses
            and proposed_exclusions == current.exclusions
            and proposed_assumptions == current.assumptions
        ):
            return

        confirmation: ContractConfirmation | None = None
        changed = confirmation_changes(
            current, objective=new_objective,
            clauses=proposed_clauses, exclusions=proposed_exclusions,
        )
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
            assumptions=proposed_assumptions,
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
    cancelled: Callable[[], bool] | None = None
    on_wait: Callable[[], None] | None = None
    # The model may need bounded conversation/attachment context in body. That
    # input is not the public operator objective carried by lifecycle events.
    public_objective: str | None = None

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
        _maybe_name_session(
            {"session_id": _life_dir_for(self.mem).name,
             "global_root": getattr(self.mem, "global_root", None)},
            self.body,
            suggested_name=str(getattr(self.decision, "session_title", "") or ""),
            replacing=force_stage_reset,
        )
        return division

    def completed(
        self,
        division: Any,
        *,
        continuous_generation: int | None = None,
    ) -> None:
        execution_task = require_manager_execution_task(division)
        workflow_mode = str(
            getattr(division, "workflow_mode", "staged") or "staged"
        ).strip().lower()
        lifetime = str(self.lifetime or "bounded").strip().lower()
        # Every TEAM task runs inside a Planner-run campaign.
        continuous = True if self.continuous is None else self.continuous
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
            "objective": execution_task,
            "execution_task": execution_task,
            "vertical": getattr(division, "vertical", ""),
            "domain": getattr(division, "domain", ""),
            "route": "team",
            "workflow_mode": workflow_mode,
            "workflow_profile": getattr(division, "workflow_profile", ""),
            "workflow_summary": getattr(division, "workflow_summary", ""),
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
                + (
                    f"\n{division.workflow_summary}"
                    if getattr(division, "workflow_summary", "") else ""
                )
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
            "objective": self.body if self.public_objective is None else self.public_objective,
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


def _handoff_wait_seconds() -> float:
    from ..core.knobs import normalize_cockpit_knob_value, resolve_knob

    name = "ARGUS_SKILL_MANAGER_HANDOFF_WAIT_SECONDS"
    return float(normalize_cockpit_knob_value(name, resolve_knob(name, "900").value))


@contextmanager
def _operator_pipeline_lock(
    prepared: PreparedManagerHandoff, *, cancelled: Callable[[], bool] | None = None,
    request_yield: bool = True,
) -> Iterator[None]:
    """Ask for a mission boundary BEFORE waiting on the foreground commit lock.

    This never terminates a provider call. Native Manager locks honor timeout
    and cancellation; the optional kwargs keep injected test/custom lock
    factories compatible with the existing no-argument protocol.
    """
    from ._session_ops import (
        ManagerLockCancelled,
        clear_manager_pipeline_yield,
        manager_pipeline_boundary,
        request_manager_pipeline_yield,
    )

    check = cancelled if cancelled is not None else getattr(prepared, "cancelled", None)
    if callable(check) and check():
        raise ManagerLockCancelled("Manager request cancelled before safe handoff")
    timeout = _handoff_wait_seconds()
    on_wait = getattr(prepared, "on_wait", None)
    if callable(on_wait):
        on_wait()
    life_dir = _life_dir_for(prepared.mem)
    owner = request_manager_pipeline_yield(life_dir, cancelled=check) if request_yield else ""
    try:
        with manager_pipeline_boundary(prepared.manager, cancelled=check, timeout=timeout):
            yield
    finally:
        if owner:
            clear_manager_pipeline_yield(life_dir, owner)


def prepare_manager_execution_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
    ensure_runner: Callable[[dict[str, Any], Any], Any] | None = None,
    public_objective: str | None = None,
) -> PreparedManagerHandoff:
    public_objective = body if public_objective is None else public_objective.strip()
    intent_id = f"intent-{time.time_ns()}"
    lifetime = str(
        chat_state.get("_team_lifetime", "bounded") or "bounded"
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
        "objective": public_objective,
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
        approved = chat_state.pop("_approved_domain_decision", None)
        decision = (approved if approved is not None and approved.execution_task == body
                    else manager.decide_vertical(body, **decision_kwargs))
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
            public_objective=public_objective,
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
            public_objective=public_objective,
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
            from ..core.mission_view import say, uses_cjk

            # The exception keeps the developer's account; the operator reads
            # what happened and what to change.
            _publish_manager_model_capability_mismatch(
                mem,
                text=say("manager_model_mismatch", uses_cjk(public_objective or body), model=exc.model_id),
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
        with _operator_pipeline_lock(prepared):
            division = prepared.commit(acquire_lock=False)
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
    prepared_handoff: PreparedManagerHandoff | None = None,
    cancelled: Callable[[], bool] | None = None,
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
    from ._session_ops import clear_manager_pipeline_yield, request_manager_pipeline_yield

    life_dir = _life_dir_for(mem)
    yield_token = ""
    try:
        yield_token = request_manager_pipeline_yield(life_dir, cancelled=cancelled)
        with _operator_pipeline_lock(prepared, cancelled=cancelled, request_yield=False):
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
    finally:
        if yield_token:
            clear_manager_pipeline_yield(life_dir, yield_token)


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
        if persist is not None:
            committed["persisted"] = persist(
                prepared.execution_task,
                committed["division"],
            )

    from ._session_ops import clear_manager_pipeline_yield, request_manager_pipeline_yield

    yield_token = ""
    try:
        yield_token = request_manager_pipeline_yield(life_dir, cancelled=cancelled)
        with _operator_pipeline_lock(prepared, cancelled=cancelled, request_yield=False):
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
        if yield_token:
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


def _pre_provider_refusal_reply(exc: Exception, body: str) -> str:
    compact = " ".join(str(body or "").split())
    return (
        "[not dispatched] The provider call was refused before start: "
        f"{exc}. Request: {compact if len(compact) <= 160 else compact[:157] + '...'}"
    )


def manager_judgment(mem: Any, prompt: str, chat_state: dict[str, Any], *,
                     run_label: str, root_task_id: str | None = None) -> str:
    """One stateless, read-only Manager turn on a self-contained prompt.

    Unlike :func:`manager_turn` it neither resumes nor advances the
    Manager's conversation. ``""`` when no Manager is available or it said nothing.
    """
    manager = getattr(_ensure_manager_runner(chat_state, mem), "manager", None)
    if manager is None:
        return ""
    return manager.run_turn(prompt, run_label=run_label, root_task_id=root_task_id)


def manager_turn(mem: Any, body: str, chat_state: dict[str, Any], *,
                 on_fragment: Any = None, root_task_id: str | None = None,
                 team_allowed: bool = True) -> "tuple[str | None, HandOff | None]":
    """Hand one operator message to the Manager's session: its reply, and what it handed off.

    The reply is ``None`` only when no Manager is available. A failed turn
    replies with the failure and sets ``chat_state["_self_failure"]``.

    ``on_fragment(kind, payload)`` streams the turn to a live front end:
    ``("delta", {"text", "message_id"})`` per assistant message as it arrives,
    and ``("phase", {"role", "label", ...})`` per visible step.
    """
    from ..core.mission_view import say, uses_cjk
    from ..core.operator_messages import humanize_runtime_reason
    from ..provider_integrations.authorization_retry import BackendLoginRequired
    from .form import manager_actions

    chat_state.pop("_self_delivery", None)
    chat_state.pop("_self_failure", None)
    runner = _ensure_manager_runner(chat_state, mem)
    if runner is None:
        return None, None
    chinese = uses_cjk(body)
    captured: list[str] = []
    round_failure: str | None = None

    def _redact(text: Any) -> str:
        return redact_secrets_text(str(text or ""), known_values=known_secret_values())

    def _failure_reply(detail: Any) -> str:
        safe = _redact(detail).strip()[:1200] or say("reply_incomplete", chinese)
        chat_state["_self_failure"] = {"detail": safe}
        chat_state.pop("last_thread_id", None)
        return say("reply_failed", chinese, said=humanize_runtime_reason(safe, language_hint=body, interactive=True))

    def _fragment(kind: str, payload: dict[str, Any]) -> None:
        if not callable(on_fragment):
            return
        payload = {key: _redact(value) if key in {"text", "label", "detail"} else value
                   for key, value in payload.items()}
        try:
            on_fragment(kind, payload)
        except Exception:  # noqa: BLE001 — a UI callback must never break the turn
            pass

    def _phase(label: str, *, role: str = "manager", kind: str = "", detail: str = "",
               meta: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"role": role, "label": label}
        if kind:
            payload["kind"] = kind
        if detail:
            payload["detail"] = detail
        # Structured tool facts ride along so the cockpit can pair a call with
        # its result and show how it ended, instead of parsing the label.
        for key, field in _PHASE_META_FIELDS:
            value = (meta or {}).get(key)
            if value not in (None, ""):
                payload[field] = _redact(value) if isinstance(value, str) else value
        _fragment("phase", payload)

    class _Capture:
        def __init__(self) -> None:
            self._message_id = ""
            self._live_at = 0.0
            self._live_message_id = ""

        def handle_event(self, event: dict[str, Any]) -> None:
            nonlocal round_failure
            etype = str(event.get("type") or "")
            if etype == "skill.library.available":
                _emit_manager_event(mem, event)
            # Each assistant message streams live as a snapshot of that
            # message; a later one replaces it, and round.main.completed
            # carries the final text.
            if etype == "engineer.progress" and str(event.get("kind") or "") in REPLY_KINDS:
                self._message_id = str(event.get("message_id") or "")
                live = _redact(_extract_chat_reply_text(str(event.get("text") or "")))
                if event.get("transient") or not live or live.startswith("{"):
                    return
                # Pacing snapshots of one message keeps a long reply from
                # re-sending itself per token; the final text always follows.
                now = time.monotonic()
                if self._message_id == self._live_message_id and now - self._live_at < _LIVE_DELTA_INTERVAL_S:
                    return
                self._live_at, self._live_message_id = now, self._message_id
                _fragment("delta", {"text": live, "message_id": self._message_id,
                                    "fragment_mode": "snapshot", "live": True})
                return
            if etype != "round.main.completed":
                return
            if event.get("exit_code") not in (None, 0) or event.get("fatal_error") or event.get("turn_completed") is False:
                round_failure = _redact(event.get("fatal_error") or "The Manager turn did not complete.").strip()
                return
            text = _redact(_extract_chat_reply_text(str(event.get("last_message") or "")))
            if text:
                captured.append(text)
                _fragment("delta", {"text": text, "message_id": self._message_id, "fragment_mode": "snapshot"})

    actions = manager_actions(body, active_mission=mission_is_running(mem), team_allowed=team_allowed)
    try:
        outcome = runner.manager_turn(
            message=body, sink=_Capture(), actions=actions, seed_thread_id=chat_state.get("last_thread_id"),
            phase_cb=_phase, root_task_id=root_task_id,
        )
    except BackendLoginRequired:
        raise
    except Exception as exc:  # noqa: BLE001 — preserve failure, never dispatch or replay
        if is_pre_provider_refusal_error(exc):
            return _redact(_pre_provider_refusal_reply(exc, body)), None
        return _failure_reply(f"{type(exc).__name__}: {exc}"), None
    if not outcome.success or round_failure:
        return _failure_reply(outcome.stop_reason or round_failure), None
    if isinstance(outcome.delivery, dict):
        chat_state["_self_delivery"] = outcome.delivery
    chat_state["last_thread_id"] = runner.last_thread_id
    handoff = actions.handoff
    return (captured[-1] if captured else handoff.summary if handoff else say("reply_empty", chinese)), handoff


def _extract_chat_reply_text(msg: str) -> str:
    """Pull the human reply out of a chat result (plain text, or JSON-wrapped)."""
    msg = (msg or "").strip()
    if "📢" in msg:
        msg = msg.rsplit("📢", 1)[1].strip()
    if msg.startswith("{") and msg.endswith("}"):
        try:
            data = json.loads(msg)
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
    "manager_judgment",
    "manager_turn",
    "mission_is_running",
    "prepare_manager_execution_task",
    "require_manager_execution_task",
]
