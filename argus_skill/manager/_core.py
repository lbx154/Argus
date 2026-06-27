"""argus.manager — the user-facing Manager that DIVIDES a Task.

When the user hands over a Task, the Manager first decides whether it is a
"regular" task — one that maps to a preset vertical pipeline (a research paper,
or a lean optimize/speedrun loop) — then splits it into that vertical's Stages
and commits the choice. The existing engine (LifeSupervisor → Planner → SkillLoop
→ Engineer ↔ Reviewer) then advances stage-by-stage on its own.

This is a thin ORCHESTRATION layer — it reuses the real machinery, adding only
the user-facing *division* step:

  * classify   → ``skills.vertical_select.classify_vertical`` (LLM if a runner is
                 given, else a keyword heuristic; optimize verticals routed by
                 ``_route_optimize_vertical``)
  * stage list → ``verticals/<v>/stages.py`` ``STAGE_ORDER`` via ``load_vertical``
  * commit     → ``skills.vertical_select.persist_vertical`` — the supervisor then
                 TRUSTS the persisted vertical and does NOT re-classify
                 (see life/supervisor/_core.py:2460).

The Manager never judges the win and never plans loops itself — it only divides
the task and hands the current Stage to the existing Planner.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # POSIX advisory file locking; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from ..skills import vertical_select
from ..skills.vertical_select import (
    classify_vertical,
    normalize_vertical,
    persist_vertical,
    resolve_vertical,
)

# Verticals that run a lean optimize/speedrun loop rather than the paper pipeline.
_OPTIMIZE_VERTICALS = frozenset(
    {"speedrun", "nanochat", "nanogpt_speedrun", "kernelbench"}
)

log = logging.getLogger(__name__)

# Where the Manager's one persistent codex session lives (under project_root).
_SESSION_FILE = ".manager_session.json"
_SESSION_LOCK = ".manager_session.lock"

# Fixed role skill the Manager always injects into its decision prompts (mirrors
# the planner's ``_PLANNER_ROLE_SKILL`` / the reviewer's role-context block).
_MANAGER_ROLE_SKILL = "argus-manager-role.md"
_MANAGER_ROLE_FALLBACK = """# Argus Manager Role

The Manager is argus-skill's task-divider and pipeline authority. It classifies
the task into a vertical, splits it into that vertical's stages, owns the
advance/hold/rollback stage transition (the SOLE post-bootstrap writer of
`current_stage`), approves which reviewer-proposed skills enter the library, and
routes free text as conversation-vs-task. It never writes code or judges the win
itself — it divides the work and hands the current stage to the existing engine.
"""


def _session_lock_timeout_s() -> float:
    """Bounded wait for the shared Manager session lock (default 120s). Manager
    turns are short LLM calls (classify / stage / skill-review), so 120s easily
    covers a normal turn while capping starvation if a peer turn hangs."""
    raw = os.environ.get("ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S", "")
    try:
        return max(0.0, float(raw)) if raw.strip() else 120.0
    except ValueError:
        return 120.0


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


class _ManagerSession:
    """A flock-serialized, persistent codex session shared by every Manager LLM
    call. The thread_id lives at ``<project_root>/.manager_session.json``; a
    sibling ``.manager_session.lock`` serializes cross-process use so the REPL
    front-end and the daemon never interleave a turn. Fail-open: any lock/IO
    error degrades to a plain no-session call — the Manager's decision must never
    be blocked by this.

    This is a "runner-like" wrapper: it exposes ``run_exec(prompt=, options=,
    run_label=)`` so it can be passed anywhere a runner is expected
    (``classify_vertical``, ``approve_skill``). It IGNORES any incoming
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
            tid = data.get("thread_id")
            return str(tid) if tid else None
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
        resume_thread_id: str | None = None,  # IGNORED: persistent session wins.
    ) -> Any:
        """Run one turn on the shared persistent session, serialized by flock.

        The session lock is acquired NON-blocking with a bounded wait
        (``ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S``, default 120s), so a long/hung turn
        in the peer process (REPL vs daemon share one lock per cwd) can't freeze
        this one indefinitely — if it can't be acquired in time we fall open to a
        plain no-session call.

        Fail-open recovery: if anything in the session-mode path fails (lock setup,
        a corrupt resume tid, a runner that does not accept ``resume_thread_id``),
        we fall back to ONE plain no-session call — a deliberate recovery + runner
        compatibility shim. The fallback runs AFTER the lock is released, never
        nested under it.
        """
        def _no_session() -> Any:
            return self.runner.run_exec(
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
                result = self.runner.run_exec(
                    prompt=prompt,
                    options=options,
                    run_label=run_label,
                    resume_thread_id=tid,
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


@dataclass
class Division:
    """The Manager's verdict on how to divide a Task."""
    task: str
    vertical: str            # research | speedrun | … | a Manager-authored data domain
    kind: str                # "research" | "optimize" | "custom"
    regular: bool            # True = maps to a preset pipeline; False = free-form
    stages: list[str]        # the vertical's Stage template (engine advances current_stage)
    # Set when the Manager AUTHORED a new data domain for a task that fit no
    # preset vertical. ``pending_confirmation`` means the proposal has NOT been
    # written yet — the caller (an interactive REPL) must confirm and then call
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

    ``action`` is ``advance`` | ``hold`` | ``rollback``. A ``hold`` writes
    nothing; ``advance``/``rollback`` are applied (the Manager is the SOLE
    post-bootstrap writer of ``current_stage``). ``source`` records WHY this was
    the verdict — useful for journaling and to distinguish a model decision from
    a fail-safe HOLD.
    """

    action: str            # "advance" | "hold" | "rollback"
    target_stage: str
    reason: str
    current_stage: str = ""
    # manager_llm | no_review_hold | no_runner_hold | failsafe_hold | illegal_target_hold
    source: str = "manager_llm"
    # Non-secret parser/runtime code for log triage (never raw model output).
    diagnostic: str = ""

    def is_write(self) -> bool:
        return self.action in ("advance", "rollback")


class Manager:
    """User-facing entry: divide a Task, then hand it to the existing engine.

    ``project_root`` is the life project dir (where PIPELINE_STATE.json lives).
    ``runner`` is an optional LLM backend for classification; without it the
    classifier degrades to the deterministic keyword heuristic.
    """

    def __init__(
        self,
        project_root: Path | str = ".",
        runner: Any = None,
        *,
        skill_store: Any = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.runner = runner
        # One persistent, flock-serialized codex session shared by every Manager
        # LLM call (front-end REPL + daemon). ``None`` when there is no runner —
        # the classifier then falls back to the keyword heuristic as before.
        self._session = (
            _ManagerSession(runner, self.project_root) if runner is not None else None
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

    # ---- skill injection (fixed role skill + matched adaptive block) ----
    def _role_skill_block(self, objective: str) -> str:
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
        """
        if self.skill_store is None:
            return ""
        from ..skills.role_context import format_role_context

        block = format_role_context(
            "Argus manager role skill",
            _MANAGER_ROLE_SKILL,
            _MANAGER_ROLE_FALLBACK,
        )
        # Adaptive matched manager skill(s). Fail-soft: a matcher hiccup must
        # never break a stage decision, so any error degrades to role skill only.
        if (objective or "").strip():
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

    # ---- triage: is this a regular task, and which vertical/kind? ----
    def triage(self, task: str) -> tuple[str, str, bool]:
        """Return (vertical, kind, regular). Reuses vertical_select — no new classifier."""
        vertical = normalize_vertical(
            classify_vertical(task, runner=(self._session or self.runner))
        )
        kind = "optimize" if vertical in _OPTIMIZE_VERTICALS else "research"
        return vertical, kind, self._is_regular(task)

    @staticmethod
    def _is_regular(task: str) -> bool:
        """Regular = the task actually reads as a project (carries at least one
        research/optimize signal), not an empty or throwaway line. The classifier
        always maps to *some* vertical, so we additionally require a real signal."""
        t = (task or "").lower()
        if not t.strip():
            return False
        hits = sum(1 for s in vertical_select._SPEEDRUN_SIGNALS if s in t)
        hits += sum(1 for s in vertical_select._RESEARCH_SIGNALS if s in t)
        return hits >= 1

    # ---- split into the vertical's Stage template ----
    def plan_stages(self, vertical: str) -> list[str]:
        """The vertical's Stage list (research → the 8-stage paper pipeline).
        Reuses verticals/<v>/stages.py; falls back to the canonical 8 stages."""
        try:
            from ..verticals._base import load_vertical

            order = getattr(load_vertical(vertical), "STAGE_ORDER", None)
            if order:
                return list(order)
        except Exception:  # noqa: BLE001 — fall back, never crash division
            pass
        from ..skills.stage_checklists import CANONICAL_STAGE_ORDER

        return list(CANONICAL_STAGE_ORDER)

    # ---- the user-facing division step ----
    def divide(self, task: str, *, ask_on_new_domain: bool = False) -> Division:
        """Classify → stages → COMMIT the vertical so the existing supervisor trusts
        it (no re-classify). Returns the Division for display/confirmation.

        When the Task carries NO preset-vertical signal (research / optimize /
        quant), the Manager AUTHORS a new data domain instead of forcing the
        research default. ``ask_on_new_domain`` controls the commit:

        * ``False`` (autonomous): write the data domain + persist it immediately.
        * ``True`` (ask): return a ``Division`` carrying the proposal with
          ``pending_confirmation=True`` and write NOTHING — the caller confirms
          with the operator and then calls :meth:`commit_domain`.

        If authoring fails (no backend / ambiguous proposal) it falls through to
        today's preset path (the research default), so behavior is never worse
        than before.
        """
        if task and task.strip() and not self._matches_preset(task):
            proposal = self._author_domain(task)
            if proposal is not None:
                if ask_on_new_domain:
                    return Division(
                        task=task, vertical=proposal.name, kind="custom",
                        regular=True, stages=list(proposal.stages),
                        proposed_domain=proposal, pending_confirmation=True,
                    )
                return self.commit_domain(task, proposal)
            # authoring failed → fall through to the preset path (research default)
        vertical, kind, regular = self.triage(task)
        stages = self.plan_stages(vertical)
        persist_vertical(self.project_root, vertical)   # supervisor reads & trusts this
        return Division(task=task, vertical=vertical, kind=kind,
                        regular=regular, stages=stages)

    @staticmethod
    def _matches_preset(task: str) -> bool:
        """Whether the Task carries any preset-vertical signal (research / optimize
        / quant). When it does NOT, the Manager authors a new data domain."""
        t = (task or "").lower()
        if not t.strip():
            return False
        for sigs in (
            vertical_select._SPEEDRUN_SIGNALS,
            vertical_select._RESEARCH_SIGNALS,
            vertical_select._QUANT_SIGNALS,
        ):
            if any(s in t for s in sigs):
                return True
        return False

    def _author_domain(self, task: str) -> Any:
        """Author a new domain (name + stages) via the Manager LLM, or ``None``.

        Returns a :class:`~argus_skill.manager.domain_author.DomainProposal` on a
        clean proposal; ``None`` when there is no backend or the proposal is
        ambiguous (fail-closed → caller uses the research default)."""
        backend = self._session or self.runner
        if backend is None:
            return None
        from ..core.models import RunnerOptions
        from ..verticals._data_domain import list_data_domains
        from .domain_author import build_domain_author_prompt, parse_domain_proposal
        from .stage_decider import extract_answer

        existing = list_data_domains(self.project_root)
        known = list(vertical_select.VERTICALS)
        prompt = build_domain_author_prompt(
            task, known_verticals=known, existing_data_domains=existing
        )
        try:
            result = backend.run_exec(
                prompt=prompt,
                options=RunnerOptions(reasoning_effort="high", skip_git_repo_check=True),
                run_label="manager-domain-author",
            )
            return parse_domain_proposal(
                extract_answer(result),
                known_verticals=known,
                existing_data_domains=existing,
            )
        except Exception:  # noqa: BLE001 — authoring must never crash division
            log.debug("manager domain authoring failed", exc_info=True)
            return None

    def commit_domain(self, task: str, proposal: Any) -> Division:
        """Write the authored data domain to disk and persist it as the active
        vertical (so the supervisor trusts it). On any write error, fall back to
        the research default. Called autonomously by :meth:`divide` or by the REPL
        after operator confirmation."""
        from ..verticals._data_domain import write_data_domain

        try:
            write_data_domain(
                self.project_root,
                proposal.name,
                stages=list(proposal.stages),
                created_by="manager",
            )
        except Exception:  # noqa: BLE001 — write failed → safe research fallback
            log.warning("commit_domain(%r) write failed; using research", proposal.name, exc_info=True)
            persist_vertical(self.project_root, "research")
            stages = self.plan_stages("research")
            return Division(task=task, vertical="research", kind="research",
                            regular=False, stages=stages)
        persist_vertical(self.project_root, proposal.name)
        return Division(
            task=task, vertical=proposal.name, kind="custom", regular=True,
            stages=list(proposal.stages), proposed_domain=proposal,
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
                return _backend.run_exec(
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort="high", skip_git_repo_check=True
                    ),
                    run_label="manager-converse",
                )

        return classify_is_conversational(
            text, run_exec=run_exec, role_skill_block=self._role_skill_block(text)
        )

    # ---- stage-transition authority (the Manager OWNS the pipeline stage) ----
    def decide_stage_transition(
        self,
        *,
        review: Any = None,
        planner_verdict: Any = None,
        project_root: Path | str | None = None,
        run_exec: Any = None,
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
            parse_stage_decision,
        )

        root = Path(project_root) if project_root is not None else self.project_root
        cur = _current_stage(root)

        # No reviewer feedback → never advance.
        if review is None:
            return StageTransition(
                "hold", cur, "no reviewer feedback", current_stage=cur,
                source="no_review_hold",
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
                return _backend.run_exec(
                    prompt=prompt,
                    options=RunnerOptions(
                        reasoning_effort="high", skip_git_repo_check=True
                    ),
                    run_label="manager-stage",
                )

        try:
            raw_order, _items = _vertical_defs(root)
            order = [str(s).strip().lower() for s in raw_order]
            cur_idx = order.index(cur) if cur in order else -1
            next_stage = order[cur_idx + 1] if 0 <= cur_idx < len(order) - 1 else ""
            earlier = order[:cur_idx] if cur_idx > 0 else []
            checklist_md = _format_checklist(cur, role="planner", project_root=root)
            prompt = build_stage_decision_prompt(
                current_stage=cur,
                next_stage=next_stage,
                earlier_stages=earlier,
                checklist_md=checklist_md,
                review=review,
                planner_verdict=planner_verdict,
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
            prompt = self._role_skill_block(_match_objective) + prompt
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
                decision = parse_stage_decision(
                    raw, current_stage=cur, stage_order=order
                )
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

    # ---- skill-library approval (the Manager is the top-level authority) ----
    def approve_skill(
        self,
        *,
        content: str,
        task: str,
        op: str = "create",
        reasoning_effort: str = "high",
    ) -> Any:
        """Judge whether a reviewer-proposed skill may enter the library.

        The Manager owns the generality + correctness gate (it sees the most
        context). Reuses ``skill_review.approve_skill`` but runs it on THIS
        Manager instance's ``runner`` — so "Manager approval" actually uses the
        Manager's backend, not the reviewer's. Returns an ``ApprovalVerdict``.
        """
        from .skill_review import approve_skill as _approve

        return _approve(
            content=content,
            task=task,
            op=op,
            runner=(self._session or self.runner),
            reasoning_effort=reasoning_effort,
            role_skill_block=self._role_skill_block(task),
        )

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
