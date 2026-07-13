"""argus.manager — the user-facing Manager that DIVIDES a Task.

When the user hands over a Task, the Manager first decides whether it is a
"regular" task — one that maps to a preset vertical pipeline (a research paper,
or a lean optimize/speedrun loop) — then splits it into that vertical's Stages
and commits the choice. The existing engine (LifeSupervisor → Planner → SkillLoop
→ Engineer ↔ Reviewer) then advances stage-by-stage on its own.

This is a thin ORCHESTRATION layer — it reuses the real machinery, adding only
the user-facing *division* step:

  * decide     → ``Manager.decide_vertical`` — an explicit built-in env choice is
                 reused directly; otherwise ONE grounded agent call picks an
                 existing vertical/data-domain or authors a new data domain (no
                 keyword classifier; see ``manager/domain_author.py``)
  * stage list → ``verticals/<v>/stages.py`` ``STAGE_ORDER`` via ``load_vertical``
  * commit     → ``skills.vertical_select.persist_vertical`` — the supervisor then
                 TRUSTS the persisted vertical and does NOT re-classify.

The Manager never judges the win and never plans loops itself — it only divides
the task and hands the current Stage to the existing Planner.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # POSIX advisory file locking; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.runner_errors import result_has_missing_resume_target
from ..skills import vertical_select
from ..skills.vertical_select import (
    persist_vertical,
    resolve_vertical,
)
from .domain_author import VerticalDecision, VerticalDecisionError

# Verticals that run a lean optimize/speedrun loop rather than the paper pipeline.
_OPTIMIZE_VERTICALS = frozenset(
    {"speedrun", "nanochat", "nanogpt_speedrun", "kernelbench"}
)

log = logging.getLogger(__name__)
_DEFAULT_MANAGER_REASONING_EFFORT = "xhigh"

# Where the Manager's one persistent codex session lives (under project_root).
_SESSION_FILE = ".manager_session.json"
_SESSION_LOCK = ".manager_session.lock"
_PIPELINE_LOCK = ".manager_pipeline.lock"

def _manager_reasoning_effort() -> str:
    for key in (
        "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    ):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return _DEFAULT_MANAGER_REASONING_EFFORT


def _manager_safe_mode() -> bool:
    raw = os.environ.get("ARGUS_SKILL_SAFE_MODE")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _session_lock_timeout_s() -> float:
    """Bounded wait for the shared Manager session lock (default 120s). Manager
    turns are short LLM calls (classify / stage / skill-review), so 120s easily
    covers a normal turn while capping starvation if a peer turn hangs."""
    raw = os.environ.get("ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S", "")
    try:
        return max(0.0, float(raw)) if raw.strip() else 120.0
    except ValueError:
        return 120.0


def _pipeline_lock_timeout_s() -> float:
    raw = os.environ.get("ARGUS_SKILL_MANAGER_PIPELINE_LOCK_TIMEOUT_S", "")
    try:
        return max(0.0, float(raw)) if raw.strip() else 1800.0
    except ValueError:
        return 1800.0


def _acquire_session_lock(fh: Any, *, timeout: float) -> bool:
    """Acquire ``LOCK_EX`` non-blocking, retrying up to ``timeout`` seconds.

    Returns True if acquired, False if the peer held it past the budget (a
    long/hung turn) — so the caller can fail-open instead of blocking forever.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)


@contextmanager
def manager_pipeline_lock(root: Path | str):
    """Serialize Manager pipeline commits with daemon mission execution."""
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    with (path / _PIPELINE_LOCK).open("a+b") as handle:
        if fcntl is not None and not _acquire_session_lock(
            handle,
            timeout=_pipeline_lock_timeout_s(),
        ):
            raise TimeoutError("timed out waiting for the current mission boundary")
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _restore_files_on_error(paths: list[Path]):
    snapshots: dict[Path, bytes | None] = {}
    for path in paths:
        try:
            snapshots[path] = path.read_bytes()
        except FileNotFoundError:
            snapshots[path] = None
    try:
        yield
    except Exception:
        for path, content in snapshots.items():
            try:
                if content is None:
                    path.unlink(missing_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f".{path.name}.rollback.{os.getpid()}")
                tmp.write_bytes(content)
                os.replace(tmp, path)
            except OSError:
                log.exception("failed to restore Manager pipeline artifact %s", path)
        raise


class _ManagerSession:
    """A flock-serialized, persistent codex session shared by every Manager LLM
    call. The thread_id lives at ``<project_root>/.manager_session.json``; a
    sibling ``.manager_session.lock`` serializes cross-process use so the cockpit
    front-end and the daemon never interleave a turn. Fail-open: any lock/IO
    error degrades to a plain no-session call — the Manager's decision must never
    be blocked by this.

    This is a "runner-like" wrapper: it exposes ``run_exec(prompt=, options=,
    run_label=)`` so it can be passed anywhere a runner is expected
    (``classify_vertical`` and other Manager calls). It IGNORES any incoming
    ``resume_thread_id`` and always continues the persistent session instead.
    """

    def __init__(self, runner: Any, project_root: Path | str) -> None:
        self.runner = runner
        self.project_root = Path(project_root)
        self._session_path = self.project_root / _SESSION_FILE
        self._lock_path = self.project_root / _SESSION_LOCK

    # --- persistent thread_id IO (corrupt/missing → None, never raises) ---
    def _read_tid(self) -> str | None:
        try:
            data = json.loads(self._session_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            tid = data.get("thread_id")
            if not isinstance(tid, str):
                return None
            tid = tid.strip()
            return tid or None
        except Exception:  # noqa: BLE001 — missing/corrupt/unreadable → no session
            return None

    def _write_tid(self, tid: str) -> None:
        # Atomic replace so a concurrent reader never sees a half-written file.
        self.project_root.mkdir(parents=True, exist_ok=True)
        tmp = self._session_path.with_suffix(
            self._session_path.suffix + f".tmp.{os.getpid()}"
        )
        tmp.write_text(json.dumps({"thread_id": tid}), encoding="utf-8")
        os.replace(tmp, self._session_path)

    @property
    def thread_id(self) -> str | None:
        """The current persistent session thread_id (for tests / future
        chat-reply wiring); ``None`` when no session has been established."""
        return self._read_tid()

    # --- the runner-like surface ---
    def run_exec(
        self,
        *,
        prompt: str,
        options: Any,
        run_label: str,
        resume_thread_id: str | None = None,  # noqa: ARG002 — runner Protocol parity; ignored
    ) -> Any:
        """Run one turn on the shared persistent session, serialized by flock.

        The session lock is acquired NON-blocking with a bounded wait
        (``ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S``, default 120s), so a long/hung turn
        in the peer process (cockpit vs daemon share one lock per cwd) can't freeze
        this one indefinitely — if it can't be acquired in time we fall open to a
        plain no-session call.

        Fail-open recovery: if anything in the session-mode path fails (lock setup,
        a corrupt resume tid, a runner that does not accept ``resume_thread_id``),
        we fall back to ONE plain no-session call — a deliberate recovery + runner
        compatibility shim. The fallback runs AFTER the lock is released, never
        nested under it.
        """
        def _no_session() -> Any:
            return gateway_run_exec(
                self.runner,
                prompt=prompt, options=options, run_label=run_label
            )

        try:
            self.project_root.mkdir(parents=True, exist_ok=True)
            fh = self._lock_path.open("a+b")
        except Exception:  # noqa: BLE001 — lock setup failed → no-session fail-open
            return _no_session()

        try:
            if fcntl is not None and not _acquire_session_lock(
                fh, timeout=_session_lock_timeout_s()
            ):
                # Peer holds a long/hung turn past the budget → don't block forever;
                # a no-session call uses a fresh thread, so it can't corrupt the
                # shared session.
                return _no_session()
            try:
                tid = self._read_tid()
                result = gateway_run_exec(
                    self.runner,
                    prompt=prompt,
                    options=options,
                    run_label=run_label,
                    resume_thread_id=tid,
                )
                if tid and result_has_missing_resume_target(result):
                    try:
                        self._session_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    result = gateway_run_exec(
                        self.runner,
                        prompt=prompt,
                        options=options,
                        run_label=run_label,
                    )
                new = getattr(result, "thread_id", None)
                if new:
                    try:
                        self._write_tid(str(new))
                    except Exception:  # noqa: BLE001 — persist is best-effort
                        pass
                return result
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001 — session-mode failed (lock released) → no-session
            return _no_session()
        finally:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass


def reset_manager_session(project_root: Path | str) -> bool:
    """Drop the Manager's persistent codex session pointer at ``project_root``.

    EN: A new daemon is a fresh isolation generation — it must NOT resume the
    prior daemon's Manager conversation, which otherwise grows unbounded across
    generations until codex auto-compaction. Stage truth lives in
    ``research/PIPELINE_STATE.json``, so dropping the thread_id pointer loses
    nothing load-bearing; the on-disk codex transcript stays auditable.
    中文：新 daemon 是全新的隔离代际，绝不能 resume 上一个 daemon 的 Manager
    会话（它会跨代际无界增长，直到 codex 有损压缩）。stage 真相在
    ``research/PIPELINE_STATE.json`` 里，清掉 thread_id 指针不丢任何承重信息；
    盘上的 codex transcript 不动，仍可审计。

    Best-effort, never raises (boot must not be blocked). Returns True if a
    session pointer existed. / 尽力而为、绝不抛异常（不能阻塞 daemon 启动）；
    原本存在会话指针时返回 True。
    """
    session_path = Path(project_root) / _SESSION_FILE
    try:
        existed = session_path.exists()
        session_path.unlink(missing_ok=True)
        return existed
    except Exception:  # noqa: BLE001 — best-effort; never block boot / 尽力而为，不阻塞启动
        return False


@dataclass
class Division:
    """The Manager's verdict on how to divide a Task."""
    task: str
    vertical: str            # research | speedrun | … | a Manager-authored data domain
    kind: str                # "research" | "optimize" | "custom"
    regular: bool            # True = maps to a preset pipeline; False = free-form
    stages: list[str]        # the vertical's Stage template (engine advances current_stage)
    execution_task: str = ""
    # Set when the Manager AUTHORED a new data domain for a task that fit no
    # preset vertical. ``pending_confirmation`` means the proposal has NOT been
    # written yet — the interactive caller must confirm and then call
    # :meth:`Manager.commit_domain`. Autonomous callers receive an already-
    # committed Division with ``pending_confirmation=False``.
    proposed_domain: Any = None
    pending_confirmation: bool = False

    def headline(self) -> str:
        if self.proposed_domain is not None and self.pending_confirmation:
            return (f"[manager] no preset vertical fit → PROPOSED new domain "
                    f"`{self.vertical}` ({len(self.stages)} stage(s): "
                    f"{' → '.join(self.stages)}) — awaiting confirmation")
        tag = "regular" if self.regular else "free-form"
        if self.kind == "custom":
            tag = "new domain"
        return (f"[manager] {self.kind} task ({tag}) → vertical={self.vertical}, "
                f"{len(self.stages)} stage(s): {' → '.join(self.stages)}")


@dataclass
class StageTransition:
    """The Manager's verdict on whether/how to move the pipeline stage.

    ``action`` is ``advance`` | ``hold`` | ``rollback`` | ``complete``. A
    ``hold`` writes nothing; ``advance``/``rollback`` are applied to
    ``current_stage`` and ``complete`` marks the final stage done while leaving
    ``current_stage`` coherent. ``source`` records WHY this was the verdict —
    useful for journaling and to distinguish a model decision from a fail-safe
    HOLD.
    """

    action: str            # "advance" | "hold" | "rollback" | "complete"
    target_stage: str
    reason: str
    current_stage: str = ""
    # manager_llm | no_review_hold | no_runner_hold | failsafe_hold | illegal_target_hold
    source: str = "manager_llm"
    # Non-secret parser/runtime code for log triage (never raw model output).
    diagnostic: str = ""


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _manager_blocked_rollback_artifact(
    root: Path,
    *,
    current_stage: str,
    stage_order: list[str],
) -> dict[str, Any] | None:
    payload = _read_json_object(root / "research" / "STAGE_CHECK_MANAGER_BLOCKED.json")
    if payload is None:
        return None
    if payload.get("outcome") != "MANAGER_BLOCKED":
        return None
    if payload.get("status") != "rollback-accepted":
        return None
    if payload.get("current_stage") != current_stage:
        return None
    if payload.get("requested_stage") != current_stage:
        return None
    target = payload.get("rollback_target")
    if not isinstance(target, str) or not target:
        return None
    if payload.get("earliest_broken_stage") != target:
        return None
    if payload.get("manager_action_required") != f"rollback_stage_to_{target}":
        return None
    if payload.get("pipeline_stage_fields_clean") is not True:
        return None
    try:
        current_idx = stage_order.index(current_stage)
        target_idx = stage_order.index(target)
    except ValueError:
        return None
    if target_idx >= current_idx:
        return None
    evidence_files = payload.get("evidence_files")
    if not isinstance(evidence_files, dict) or not evidence_files:
        return None
    for rel in evidence_files.values():
        if not isinstance(rel, str) or not rel:
            return None
        if not (root / rel).exists():
            return None
    return payload


class Manager:
    """User-facing entry: divide a Task, then hand it to the existing engine.

    ``project_root`` is the mission's real project WORKDIR — where
    ``research/PIPELINE_STATE.json``, ``research/DOMAINS/*.json``, and every
    other stage/vertical artifact live, matching what
    ``skills.stage_checklists`` / ``skills.vertical_select`` / the reviewer's
    stage-gated checklist all read and write. It must NEVER be the daemon's
    internal life_dir (a distinct, life-of-the-daemon scoped directory) — the
    two are easy to conflate but reads/writes against life_dir are invisible
    to everything else that tracks pipeline stage. ``manager_session_root``
    is the separate, orthogonal concern: where the Manager's OWN persistent
    codex session/lock files live (safe to keep daemon/life_dir-scoped).
    ``runner`` is an optional LLM backend for classification; without it the
    classifier degrades to the deterministic keyword heuristic.
    """

    def __init__(
        self,
        project_root: Path | str = ".",
        runner: Any = None,
        *,
        skill_store: Any = None,
        manager_session_root: Path | str | None = None,
        usage_context: Any = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.runner = runner
        self._usage_context_factory = usage_context
        self.manager_session_root = (
            Path(manager_session_root)
            if manager_session_root is not None
            else self.project_root
        )
        # One persistent, flock-serialized codex session shared by every Manager
        # LLM call within THIS Argus session. ``None`` when there is no runner —
        # the classifier then falls back to the keyword heuristic as before.
        self._session = (
            _ManagerSession(runner, self.manager_session_root)
            if runner is not None
            else None
        )
        # Optional role-mission skill matcher (the same scaffold engineer,
        # reviewer, and planner use). ``None`` skill_store ⇒ an empty match and
        # NO injected skill block, so the Manager's existing classify / stage /
        # approve behaviour is byte-for-byte unchanged for every current caller
        # that does not pass a store (full backward compatibility). When a store
        # IS wired, the Manager injects its fixed role skill plus any matched
        # adaptive manager skill into its stage-decision prompt.
        self.skill_store = skill_store
        from ..skills.missions import ManagerMission

        self.mission = ManagerMission(skill_store)

    def _task_usage_scope(self, root_task_id: str | None):
        if not root_task_id or self._usage_context_factory is None:
            return nullcontext()
        return self._usage_context_factory(root_task_id)

    def pipeline_lock(self):
        return manager_pipeline_lock(self.manager_session_root)

    # ---- skill injection (fixed role skill + matched adaptive block) ----
    def _role_skill_block(self, objective: str, *, match: bool = True) -> str:
        """Build the Manager's injected skill block for a decision prompt.

        Returns ``""`` when no ``skill_store`` is wired (the default) — so the
        Manager's decision prompt is then byte-for-byte identical to before this
        feature existed, preserving full backward compatibility for every caller
        that does not pass a store. When a store IS wired the block has two parts,
        mirroring how the planner/reviewer compose their prompts:

        * a FIXED role skill (``argus-manager-role.md`` from builtin_skills,
          with an inline fallback) that states the Manager's identity and duties;
        * a MATCHED adaptive block — the role-scoped matcher's high-fit manager
          skills for ``objective`` (empty today; populated once self-evolution
          adds OWN manager skills, and may already surface cross-role references).

        The caller PREPENDS it to the decision prompt; it never alters the
        decision's output contract/schema.

        ``match=False`` injects ONLY the fixed role identity and SKIPS the matcher
        LLM call (F6) — for pure-classification callers (route / is_conversational
        / decide_stage_transition) that need the fixed manager role context but do
        NOT consume matched skill bodies, so a matcher call each time is pure burn.
        Skill placement keeps ``match=True`` — it judges from the matched bodies.
        """
        if self.skill_store is None:
            return ""
        from ..skills.role_context import format_role_context

        block = format_role_context(
            "Argus manager role skill",
            "argus-manager-role.md",
        )
        # Adaptive matched manager skill(s). Fail-soft: a matcher hiccup must
        # never break a stage decision, so any error degrades to role skill only.
        if match and (objective or "").strip():
            try:
                match = self.mission.match(objective)
                if match.block:
                    block += (
                        "Matched manager skill(s) for this objective "
                        "(read first; apply the relevant one(s)):\n"
                        f"{match.block}\n\n"
                    )
            except Exception:  # noqa: BLE001 — matcher is advisory, never fatal
                log.debug("manager skill match failed", exc_info=True)
        return block

    # ---- the Manager's grounded vertical decision (agent, not keywords) ----
    def decide_vertical(
        self,
        task: str,
        *,
        root_task_id: str | None = None,
    ) -> VerticalDecision:
        """Choose the vertical for ``task``.

        A valid built-in named by ``ARGUS_SKILL_VERTICAL`` is an explicit operator
        decision, so it is reused directly without giving the domain author a
        chance to replace it with a task-specific DATA domain. Otherwise the agent
        picks an existing built-in vertical (preferred — built-ins ship expert
        reviewer checklists) or an existing project data domain, else AUTHORS a
        new data domain. It has shell/read access pinned to ``project_root`` and is
        told to investigate the repo before deciding (same
        ``dangerous_yolo``/``safe_mode`` convention as every other real-work call).

        FAIL-HARD when agent judgment is needed: no backend, or a model reply that
        is missing / not a valid choice, RAISES ``VerticalDecisionError``. There is
        NO keyword classifier and NO silent fallback to the research default.
        """
        explicit_builtin = vertical_select.explicit_builtin_vertical()
        if explicit_builtin is not None:
            return VerticalDecision(
                choice="existing",
                vertical=explicit_builtin,
                execution_task=task.strip(),
            )

        backend = self._session or self.runner
        if backend is None:
            raise VerticalDecisionError(
                "cannot decide the vertical: the Manager has no backend/runner"
            )
        from ..core.models import RunnerOptions
        from ..verticals._data_domain import list_data_domains
        from .domain_author import (
            build_vertical_decision_prompt,
            parse_vertical_decision,
        )
        from .stage_decider import extract_answer

        existing = list_data_domains(self.project_root)
        prompt = build_vertical_decision_prompt(
            task,
            verticals_with_purpose=vertical_select.VERTICAL_PURPOSES,
            existing_data_domains=existing,
        )
        with self._task_usage_scope(root_task_id):
            result = gateway_run_exec(
                backend,
                prompt=prompt,
                options=RunnerOptions(
                    reasoning_effort=_manager_reasoning_effort(),
                    working_dir=str(self.project_root),
                    sandbox_mode="read-only",
                    skip_git_repo_check=True,
                ),
                run_label="manager-vertical-decide",
            )
        detail = str(getattr(result, "fatal_error", "") or "").strip()
        if int(getattr(result, "exit_code", 0) or 0) != 0 or detail:
            if not detail:
                detail = "\n".join(
                    map(str, getattr(result, "stderr_lines", None) or [])
                ).strip()
            raise VerticalDecisionError(
                "Manager vertical decision backend failed"
                + (f": {detail}" if detail else "")
            )
        answer = extract_answer(result)
        decision = parse_vertical_decision(
            answer,
            known_verticals=list(vertical_select.VERTICALS),
            existing_data_domains=existing,
        )
        if decision is None:
            raise VerticalDecisionError(
                f"Manager could not decide a vertical for task {task!r}: the "
                "model reply was missing or not a valid existing/new choice"
            )
        decision.rendering_response = answer
        return decision

    def _apply_vertical_decision_rendering(
        self,
        decision: VerticalDecision,
    ) -> None:
        """Apply Manager-owned presentation only after its decision commits."""
        try:
            from .live_view import apply_manager_rendering_response

            apply_manager_rendering_response(
                self.project_root,
                decision.rendering_response,
            )
        except Exception:  # noqa: BLE001
            log.debug("manager live-view persistence failed", exc_info=True)

    @staticmethod
    def _kind_for(vertical: str) -> str:
        """Coarse kind for a resolved vertical: optimize | research | custom."""
        if vertical in _OPTIMIZE_VERTICALS:
            return "optimize"
        if vertical in ("research", "quant"):
            return "research"
        return "custom"  # a project-local (Manager-authored) data domain

    # ---- triage: which vertical/kind, and is this a real task? ----
    def triage(
        self,
        task: str,
        *,
        root_task_id: str | None = None,
    ) -> tuple[str, str, bool]:
        """Return (vertical, kind, regular) from the Manager's agent decision.

        No keyword classifier: the vertical is whatever :meth:`decide_vertical`
        returns. ``regular`` is simply whether the task is non-blank — the
        Manager already judged it a real task by choosing/authoring a vertical.
        """
        decision = self.decide_vertical(task, root_task_id=root_task_id)
        return (
            decision.vertical,
            self._kind_for(decision.vertical),
            bool((task or "").strip()),
        )

    # ---- split into the vertical's Stage template ----
    def plan_stages(self, vertical: str) -> list[str]:
        """The vertical's Stage list (research → the 8-stage paper pipeline).

        Reuses ``verticals/<v>/stages.py``. A vertical whose module loads fine
        but does not define ``STAGE_ORDER`` gets the canonical 8-stage
        template (that vertical simply opted out of a custom stage list — not
        a failure). A vertical that fails to resolve/import PROPAGATES the
        error: this matches :meth:`divide`'s documented FAIL-HARD contract
        ("no silent fallback to the research default") and
        ``LifeSupervisor._resolve_vertical_once``'s own FAIL-HARD contract —
        silently substituting the canonical/paper stage list for a broken or
        unresolvable vertical would turn e.g. a kernelbench mission into the
        paper pipeline with no visible error.
        """
        from ..verticals._base import load_vertical

        order = getattr(
            load_vertical(vertical, project_root=self.project_root),
            "STAGE_ORDER", None,
        )
        if order:
            return list(order)
        from ..skills.stage_checklists import CANONICAL_STAGE_ORDER

        return list(CANONICAL_STAGE_ORDER)

    # ---- the user-facing division step ----
    def divide(
        self,
        task: str,
        *,
        ask_on_new_domain: bool = False,
        root_task_id: str | None = None,
    ) -> Division:
        """Decide the vertical (Manager agent) → stages → COMMIT so the existing
        supervisor trusts it (no re-classify). Returns the Division for
        display/confirmation.

        * existing built-in vertical or existing data domain → persist it.
        * new data domain → ``ask_on_new_domain`` controls the commit:
          * ``False`` (autonomous): write the data domain + persist immediately.
          * ``True`` (ask): return a ``Division`` carrying the proposal with
            ``pending_confirmation=True`` and write NOTHING — the caller confirms
            with the operator and then calls :meth:`commit_domain`.

        FAIL-HARD: a blank task or an undecidable vertical RAISES. There is no
        silent fallback to the research default.

        This is also the layer where a genuinely NEW, operator-issued intent is
        dispatched, so — right after persisting the decided vertical — it
        checks whether the PREVIOUSLY-persisted vertical had already reached
        ITS OWN terminal stage with ``status="done"``. If so, and the newly
        decided vertical differs, the old project is finished and this call is
        superseding it with unrelated new work: ``current_stage`` is reset to
        the new vertical's first stage (via
        ``vertical_select.reset_stage_for_new_intent`` /
        ``stage_checklists.rollback_stage``) instead of silently inheriting a
        stale stage whose name happens to collide with one of the new
        vertical's own stages. This does NOT touch ``persist_vertical``'s
        seed-only, never-reset contract for the (common) in-project
        reclassification case, where the prior vertical was not yet finished.
        """
        if not (task and task.strip()):
            raise ValueError("Manager.divide requires a non-empty task")
        decision = self.decide_vertical(task, root_task_id=root_task_id)
        return self.commit_vertical_decision(
            task,
            decision,
            ask_on_new_domain=ask_on_new_domain,
        )

    def commit_vertical_decision(
        self,
        task: str,
        decision: VerticalDecision,
        *,
        ask_on_new_domain: bool = False,
        _lock_held: bool = False,
    ) -> Division:
        """Commit a previously computed decision without another model call."""
        lock = nullcontext() if _lock_held else self.pipeline_lock()
        with lock:
            return self._commit_vertical_decision_locked(
                task,
                decision,
                ask_on_new_domain=ask_on_new_domain,
            )

    def _commit_vertical_decision_locked(
        self,
        task: str,
        decision: VerticalDecision,
        *,
        ask_on_new_domain: bool,
    ) -> Division:
        old_vertical = vertical_select._persisted_vertical(self.project_root)
        if decision.choice == "new":
            proposal = decision.proposal
            if ask_on_new_domain:
                division = Division(
                    task=task, vertical=proposal.name, kind="custom",
                    regular=True, stages=list(proposal.stages),
                    execution_task=decision.execution_task,
                    proposed_domain=proposal, pending_confirmation=True,
                )
                self._apply_vertical_decision_rendering(decision)
                return division
            division = self._commit_domain_locked(
                task,
                proposal,
                _old_vertical=old_vertical,
                execution_task=decision.execution_task,
            )
            self._apply_vertical_decision_rendering(decision)
            return division
        vertical = decision.vertical
        stages = self.plan_stages(vertical)
        pipeline_state = self.project_root / "research" / "PIPELINE_STATE.json"
        with _restore_files_on_error([pipeline_state]):
            persist_vertical(self.project_root, vertical)
            vertical_select.reset_stage_for_new_intent(
                self.project_root,
                old_vertical=old_vertical,
                new_vertical=vertical,
            )
        division = Division(
            task=task,
            vertical=vertical,
            kind=self._kind_for(vertical),
            regular=True,
            stages=stages,
            execution_task=decision.execution_task,
        )
        self._apply_vertical_decision_rendering(decision)
        return division

    def commit_domain(
        self,
        task: str,
        proposal: Any,
        *,
        _old_vertical: str | None = None,
        execution_task: str = "",
        _lock_held: bool = False,
    ) -> Division:
        """Write the authored data domain to disk and persist it as the active
        vertical (so the supervisor trusts it). FAIL-HARD: a write error
        PROPAGATES — no silent research fallback. Called autonomously by
        :meth:`divide` or by the cockpit after operator confirmation.

        ``_old_vertical`` (private, optional) lets :meth:`divide` pass along the
        vertical it read BEFORE deciding — so the new-intent-supersedes-a-
        finished-vertical stage reset (see :meth:`divide`'s docstring) still
        applies on the new-data-domain path. When called directly (e.g. by the
        cockpit after an operator confirms a pending proposal) it is re-read here.
        """
        lock = nullcontext() if _lock_held else self.pipeline_lock()
        with lock:
            return self._commit_domain_locked(
                task,
                proposal,
                _old_vertical=_old_vertical,
                execution_task=execution_task,
            )

    def _commit_domain_locked(
        self,
        task: str,
        proposal: Any,
        *,
        _old_vertical: str | None,
        execution_task: str,
    ) -> Division:
        from ..verticals._data_domain import write_data_domain

        if _old_vertical is None:
            _old_vertical = vertical_select._persisted_vertical(self.project_root)

        pipeline_state = self.project_root / "research" / "PIPELINE_STATE.json"
        domain_path = (
            self.project_root
            / "research"
            / "DOMAINS"
            / f"{proposal.name}.json"
        )
        with _restore_files_on_error([pipeline_state, domain_path]):
            write_data_domain(
                self.project_root,
                proposal.name,
                stages=list(proposal.stages),
                created_by="manager",
            )
            persist_vertical(self.project_root, proposal.name)
            vertical_select.reset_stage_for_new_intent(
                self.project_root,
                old_vertical=_old_vertical,
                new_vertical=proposal.name,
            )
        return Division(
            task=task, vertical=proposal.name, kind="custom", regular=True,
            stages=list(proposal.stages), proposed_domain=proposal,
            execution_task=(
                execution_task
                or str(getattr(proposal, "execution_task", "") or "")
            ),
            pending_confirmation=False,
        )

    # ---- conversational-intent decision (the Manager owns this) ----
    def is_conversational(self, text: str, *, run_exec: Any = None) -> bool:
        """The Manager's top-level dialogue call: is this free text a conversation
        (greeting / capability question / ack) rather than a real task?

        The Manager — not the runner — owns this decision. Reuses
        ``life/router.classify_is_conversational`` (conservative: biases hard
        toward TASK, so work is never silently skipped). ``run_exec`` is the LLM
        caller; when omitted one is built from ``self.runner``. With no backend at
        all, treat as a task (safe default — never drop work to a bad classify).
        """
        from ..life.router import classify_is_conversational

        if run_exec is None:
            if self.runner is None:
                return False
            from ..core.models import RunnerOptions

            # Route the internal classify call through the shared persistent
            # session when available, so this turn continues the one Manager
            # conversation; otherwise fall back to a plain runner call.
            _backend = self._session or self.runner

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_manager_reasoning_effort(),
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-converse",
                )

        return classify_is_conversational(text, run_exec=run_exec)

    def classify_config_intent(
        self,
        text: str,
        *,
        run_exec: Any = None,
        root_task_id: str | None = None,
    ) -> Any:
        """Does this free text ask to change one of Argus's OWN runtime knobs
        (a role's backend/model/effort, a budget cap, or a safe_mode/
        show_reasoning/telegram toggle)? Returns a ``life.router.ConfigIntent``
        or ``None``.

        Intent recognition via one low-reasoning LLM call — never keyword/regex
        matching. The Manager owns this decision; ``run_exec`` is the LLM caller,
        built from ``self.runner`` when omitted. Biases hard toward ``None`` so a
        real task that merely mentions a model/backend is never swallowed.
        """
        from ..life.router import classify_config_intent

        if run_exec is None:
            if self.runner is None:
                return None
            from ..core.models import RunnerOptions

            # Config-intent is a STATELESS yes/no check on the CURRENT message
            # ("does this ask to change a knob?") — it needs no prior turns. Run it
            # FRESH on the raw backend (``self.runner``), NOT through ``self._session``:
            # the persistent Manager session reloads its FULL history on every
            # resume (tens of seconds on a long-lived copilot session — it was
            # adding ~30s to EVERY operator message at the cockpit front door), and
            # continuing it here would also pollute that conversation with throwaway
            # classify prompts. ``route`` is already run fresh at the front door for
            # exactly this reason (see ``apps/_runtime.py``'s ``_classify_run_exec``);
            # this makes config-intent match instead of resuming the big session.
            _backend = self.runner

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_manager_reasoning_effort(),
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-config-intent",
                    resume_thread_id=None,
                )

        with self._task_usage_scope(root_task_id):
            return classify_config_intent(text, run_exec=run_exec)

    def classify_front_door(
        self,
        text: str,
        *,
        run_exec: Any = None,
        root_task_id: str | None = None,
    ) -> Any:
        """One fresh model call classifying config, control, and routing.

        Same discipline as ``classify_config_intent``: built FRESH on the raw
        backend (``self.runner``, NEVER ``self._session`` — no giant-session
        resume, no pollution), ``resume_thread_id=None``. Effort comes from
        ``ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT`` (default ``low``): a three-axis
        label classification needs no heavy reasoning, and ``low`` is what makes
        this cheap. Biases each axis to its own safe default on any error."""
        from ..life.router import classify_front_door

        if run_exec is None:
            if self.runner is None:
                return None, None, "complex"
            import os

            from ..core.models import RunnerOptions

            _backend = self.runner
            _effort = os.environ.get(
                "ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", "low"
            ).strip() or "low"

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_effort,
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-frontdoor-classify",
                    resume_thread_id=None,
                )

        with self._task_usage_scope(root_task_id):
            return classify_front_door(text, run_exec=run_exec)

    def route(
        self,
        text: str,
        *,
        run_exec: Any = None,
        root_task_id: str | None = None,
    ) -> str:
        """The Manager's lego-block router: pick the SMALLEST block that fits the
        operator's input — ``"chat"`` (one codex reply), ``"simple"`` (one bounded
        codex turn, no reviewer/planner), or ``"complex"`` (the full mission
        pipeline). The Manager owns this call. Reuses
        ``life/router.classify_route`` (biases hard to ``"complex"`` so real work
        never silently skips the reviewer). With no backend, returns ``"complex"``
        — the safe default that never drops work to a bad classify."""
        from ..life.router import classify_route

        if run_exec is None:
            if self.runner is None:
                return "complex"
            from ..core.models import RunnerOptions

            _backend = self._session or self.runner

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_manager_reasoning_effort(),
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-route",
                )

        with self._task_usage_scope(root_task_id):
            return classify_route(text, run_exec=run_exec)

    def needs_persistence(
        self,
        text: str,
        *,
        run_exec: Any = None,
        root_task_id: str | None = None,
    ) -> bool:
        """Should this task be armed as a STANDING (continuous) campaign, or is
        it BOUNDED (one mission, drains once)? The Manager owns this decision so
        the operator never has to manually pass ``--continuous --objective`` for
        open-ended work typed straight into chat (e.g. "optimize as many kernels
        as possible"). Reuses ``life/router.classify_needs_persistence`` (TEAM
        work defaults to STANDING unless the Manager explicitly chooses
        BOUNDED). With no backend, returns False because no Manager exists to
        author the continuous objective."""
        from ..life.router import classify_needs_persistence as _classify

        if run_exec is None:
            if self.runner is None:
                return False
            from ..core.models import RunnerOptions

            _backend = self._session or self.runner

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_manager_reasoning_effort(),
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-persistence",
                )

        with self._task_usage_scope(root_task_id):
            return _classify(text, run_exec=run_exec)

    # ---- stage-transition authority (the Manager OWNS the pipeline stage) ----
    def decide_stage_transition(
        self,
        *,
        review: Any = None,
        planner_verdict: Any = None,
        project_root: Path | str | None = None,
        run_exec: Any = None,
        on_event: Any = None,
        root_task_id: str | None = None,
        open_ended: bool = False,
        continuous_objective: str = "",
    ) -> StageTransition:
        """Independently decide advance / hold / rollback for the pipeline stage,
        then WRITE it. The Manager is the SOLE post-bootstrap writer of
        ``current_stage`` — the reviewer/planner only ADVISE (via ``review`` /
        ``planner_verdict``); the engineer never edits stage state.

        THICK: the Manager makes its own LLM judgment from the reviewer's
        structured feedback + the current-stage checklist, parses a strict JSON
        verdict, and on advance/rollback calls
        :func:`stage_checklists.advance_stage` / ``rollback_stage``.

        Fail-safe — writes NOTHING and returns a HOLD when: ``review is None``
        (no feedback → never advance), there is no backend, the LLM/parse errors,
        or the model picks an illegal target. A HOLD simply leaves the stage put;
        the mission/planner loop continues, so the daemon never deadlocks.
        """
        from ..skills.stage_checklists import (
            _active_vertical_checklist_defs as _vertical_defs,
        )
        from ..skills.stage_checklists import (
            advance_stage as _advance,
        )
        from ..skills.stage_checklists import (
            complete_final_stage as _complete,
        )
        from ..skills.stage_checklists import (
            current_stage as _current_stage,
        )
        from ..skills.stage_checklists import (
            format_stage_checklist as _format_checklist,
        )
        from ..skills.stage_checklists import (
            rollback_stage as _rollback,
        )
        from .stage_decider import (
            build_stage_decision_prompt,
            extract_answer,
            fallback_empty_stage_decision,
            final_stage_completion_decision,
            parse_stage_decision,
        )

        root = Path(project_root) if project_root is not None else self.project_root
        cur = _current_stage(root)

        try:
            raw_order, _items = _vertical_defs(root)
            order = [str(s).strip().lower() for s in raw_order]
        except Exception:  # noqa: BLE001
            log.debug("manager stage-order lookup failed", exc_info=True)
            order = []

        artifact = _manager_blocked_rollback_artifact(
            root, current_stage=cur, stage_order=order
        )
        if artifact is not None:
            target = str(artifact["rollback_target"])
            try:
                _rollback(
                    root,
                    target_stage=target,
                    reason=(
                        "stage_check accepted positive evidence rollback packet: "
                        f"earliest_broken_stage={artifact['earliest_broken_stage']}"
                    ),
                    rolled_back_by="manager",
                )
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal rollback artifact target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="manager_blocked_artifact_illegal_target",
                )
            return StageTransition(
                "rollback",
                target,
                "stage_check accepted positive evidence rollback packet",
                cur,
                "manager_blocked_rollback_artifact",
                "accepted_manager_blocked_artifact",
            )

        # An open-ended final-stage checkpoint may need a new solve cycle after
        # the Planner confirms the operator's objective is still unresolved.
        # The Manager remains the sole rollback authority; the Planner only
        # supplies the advisory reason.
        open_ended_reconciliation = bool(
            open_ended
            and planner_verdict is not None
            and order
            and cur == order[-1]
        )

        # No reviewer feedback normally means no stage transition. The sole
        # exception is the structured open-ended terminal reconciliation above.
        if review is None:
            if not open_ended_reconciliation:
                return StageTransition(
                    "hold", cur, "no reviewer feedback", current_stage=cur,
                    source="no_review_hold",
                )
            from types import SimpleNamespace

            planner_reason = str(
                getattr(planner_verdict, "reason", "") or planner_verdict
            )
            review = SimpleNamespace(
                status="done",
                reason=(
                    "The final-stage checkpoint is reviewer-certified, but the "
                    "open-ended campaign objective remains unresolved. "
                    f"Planner advisory: {planner_reason}"
                ),
                planner_report={
                    "forward_progress": False,
                    "blocker": planner_reason,
                    "recommended_next": (
                        "Manager decides whether to roll back for another "
                        "evidence-led cycle or hold."
                    ),
                },
                checklist=[],
            )

        # Build the LLM caller (mirrors is_conversational): no backend → safe HOLD.
        if run_exec is None:
            if self.runner is None and self._session is None:
                return StageTransition(
                    "hold", cur, "no manager backend", current_stage=cur,
                    source="no_runner_hold",
                )
            from ..core.models import RunnerOptions

            _backend = self._session or self.runner

            def run_exec(prompt: str) -> Any:  # noqa: ANN401
                return gateway_run_exec(
                    _backend,
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort=_manager_reasoning_effort(),
                        working_dir=str(root),
                        sandbox_mode="read-only",
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-stage",
                )

            # F3: meter each manager-stage codex turn (incl. the empty-output
            # retries below) so its tokens fold into the per-mission cost sink +
            # the daily cap — they were previously invisible. Fail-soft.
            from ..core.cost_events import metered_run_exec
            try:
                from ..core.knobs import resolve_role_model
                _mmodel = resolve_role_model(
                    "engineer",
                    role_env="ARGUS_SKILL_ENGINEER_MODEL",
                ) or ""
            except Exception:  # noqa: BLE001
                _mmodel = ""
            run_exec = metered_run_exec(
                run_exec, on_event, layer="manager", model=_mmodel,
                run_label="manager-stage",
            )

        try:
            cur_idx = order.index(cur) if cur in order else -1
            next_stage = order[cur_idx + 1] if 0 <= cur_idx < len(order) - 1 else ""
            earlier = order[:cur_idx] if cur_idx > 0 else []
            checklist_md = _format_checklist(cur, role="planner", project_root=root)
            from .live_view import manager_rendering_prompt

            prompt = build_stage_decision_prompt(
                current_stage=cur,
                next_stage=next_stage,
                earlier_stages=earlier,
                checklist_md=checklist_md,
                review=review,
                planner_verdict=planner_verdict,
                rendering_block=manager_rendering_prompt(root, review=review),
                open_ended=open_ended,
                continuous_objective=continuous_objective,
            )
            # Inject the Manager's fixed role skill (+ any matched adaptive
            # manager skill) ahead of the decision prompt. No-op when no
            # skill_store is wired — the prompt is then byte-for-byte identical to
            # before, preserving the stage-decision output contract. The matcher
            # objective is the current stage + the reviewer's reason so the
            # role-scoped matcher has a concrete task descriptor.
            _match_objective = " ".join(
                p for p in (cur, str(getattr(review, "reason", "") or "")) if p
            )
            prompt = self._role_skill_block(_match_objective, match=False) + prompt
            with self._task_usage_scope(root_task_id):
                raw = extract_answer(run_exec(prompt))
                # gpt-5.5/fnyweg (and other backends) occasionally return an EMPTY
                # turn. An empty raw makes parse_stage_decision fall back to a silent
                # "manager held (default)" — which, after a DONE reviewer verdict,
                # wedges current_stage FOREVER (research completes but never advances
                # to plan, because no later mission re-triggers a stage decision).
                # Retry a couple of times on an empty response before accepting a
                # hold, mirroring the planner's empty-output retry. A genuine,
                # non-empty hold verdict is never retried.
                _empty_retries = 0
                while not str(raw or "").strip() and _empty_retries < 2:
                    _empty_retries += 1
                    time.sleep(1.0)
                    raw = extract_answer(run_exec(prompt))
            if not str(raw or "").strip():
                decision = fallback_empty_stage_decision(
                    review, current_stage=cur, stage_order=order
                )
            else:
                try:
                    from .live_view import (
                        apply_manager_rendering_response,
                        parse_live_view_response,
                    )

                    live_decided, _live_view = parse_live_view_response(raw)
                    live_view = apply_manager_rendering_response(root, raw)
                    if live_decided and on_event is not None:
                        on_event({
                            "type": "manager.live_view.updated",
                            "title": live_view.title if live_view else "",
                            "paths": list(live_view.paths) if live_view else [],
                            "text": (
                                f"Manager refreshed right sidebar: {live_view.title}"
                                if live_view
                                else "Manager cleared right sidebar"
                            ),
                        })
                except Exception:  # noqa: BLE001 — rendering never blocks stage
                    log.debug("manager live-view refresh failed", exc_info=True)
                decision = parse_stage_decision(
                    raw, current_stage=cur, stage_order=order
                )
            if not open_ended:
                final_decision = final_stage_completion_decision(
                    review,
                    current_stage=cur,
                    stage_order=order,
                    vertical=resolve_vertical(root),
                    trigger_diagnostic=decision.diagnostic,
                    trigger_reason=decision.reason,
                )
                if final_decision is not None:
                    decision = final_decision
        except Exception:  # noqa: BLE001 — any failure → safe HOLD, write nothing
            log.debug("manager stage decision failed", exc_info=True)
            return StageTransition(
                "hold", cur, "manager decision error", current_stage=cur,
                source="failsafe_hold", diagnostic="exception",
            )

        if decision.action == "advance":
            try:
                _advance(root, target_stage=decision.target_stage,
                         reason=decision.reason, advanced_by="manager")
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal advance target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("advance", decision.target_stage, decision.reason,
                                   cur, "manager_llm", decision.diagnostic)

        if decision.action == "complete":
            try:
                _complete(root, reason=decision.reason, completed_by="manager")
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal final-stage completion", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("complete", decision.target_stage, decision.reason,
                                   cur, "manager_llm", decision.diagnostic)

        if decision.action == "rollback":
            try:
                _rollback(root, target_stage=decision.target_stage,
                          reason=decision.reason, rolled_back_by="manager")
            except ValueError:
                return StageTransition(
                    "hold", cur, "illegal rollback target", current_stage=cur,
                    source="illegal_target_hold",
                    diagnostic="stage_write_illegal_target",
                )
            return StageTransition("rollback", decision.target_stage, decision.reason,
                                   cur, "manager_llm", decision.diagnostic)

        return StageTransition("hold", cur, decision.reason or "manager held",
                               cur, "manager_llm", decision.diagnostic)

    # ---- skill-library tidy-up (the Manager is the "janitor") ----
    def classify_skill_placement(self, *, content: str, task: str) -> Any:
        """Decide where a project-distilled skill belongs: global / a vertical /
        stay. Runs the placement judge on THIS Manager's runner with the known
        verticals as candidates. Returns a ``PlacementVerdict``."""
        from .skill_review import classify_skill_placement as _classify

        return _classify(
            content=content,
            task=task,
            candidate_verticals=list(vertical_select.VERTICALS),
            runner=(self._session or self.runner),
        )

    def classify_skill_placements(self, skills: list[dict[str, str]]) -> Any:
        """Batch variant used by source promotion to avoid one call per skill."""
        from .skill_review import classify_skill_placements as _classify_batch

        return _classify_batch(
            skills=skills,
            candidate_verticals=list(vertical_select.VERTICALS),
            runner=(self._session or self.runner),
        )

    # ---- progress view ----
    def current_stage(self) -> str:
        """Which Stage the engine is on now (read from PIPELINE_STATE.json)."""
        import json

        try:
            state = json.loads(
                (self.project_root / "research" / "PIPELINE_STATE.json")
                .read_text(encoding="utf-8")
            )
            return str(state.get("current_stage") or "") or self.plan_stages(
                resolve_vertical(self.project_root)
            )[0]
        except Exception:  # noqa: BLE001
            return ""
