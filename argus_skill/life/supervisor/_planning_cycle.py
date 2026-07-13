"""One continuous-planner cycle: decide vertical, plan, dedupe, enqueue."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from ...core.event_catalog import EventType
from ...core.pricing import price_for, usd_for_tokens
from ..memory import BacklogItem
from ._constants import (
    PLAN_ERROR,
    PLAN_HANDOFF,
    PLAN_RETRY,
    PLANNER_DEDUP_STATUSES,
    PLANNER_SCOPE_FINAL_SUBMISSION,
)
from ._cost import copilot_usd_for_premium_requests
from ._helpers import (
    _entry_task_signature,
    _planner_task_signature,
    _resolve_task_dep_ids,
    _sanitize_planner_task_text,
)

log = logging.getLogger(__name__)


def _research_project_done_issue(
    project_root: object,
    journal_entries: list[Any],
) -> str:
    """Require a current-target reviewer certification before Planner success."""
    from ...core.research_contract import (
        research_completion_issue,
        resolve_research_target_level,
        resolve_research_target_set_at,
    )

    target_level = resolve_research_target_level(project_root)
    if target_level is None:
        return ""
    target_set_at = resolve_research_target_set_at(project_root) or 0.0
    for entry in reversed(journal_entries):
        if str(getattr(entry, "kind", "") or "") != "mission_complete":
            continue
        try:
            entry_ts = float(getattr(entry, "ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if entry_ts < target_set_at:
            break
        extra = getattr(entry, "extra", None)
        if (
            isinstance(extra, dict)
            and str(extra.get("scope") or "").strip().lower() == "bounded"
        ):
            continue
        research_result = (
            extra.get("research_result") or extra.get("math_result")
            if isinstance(extra, dict)
            else None
        )
        if not research_completion_issue(
            research_result,
            research_target_level=target_level,
            scope=str(extra.get("scope") or "") if isinstance(extra, dict) else "",
        ):
            return ""
    return f"missing_{target_level}_reviewer_certification"


class PlanningCycleMixin:
    def _reconcile_open_ended_terminal_stage(self, verdict: Any) -> bool:
        """Ask the Manager to reopen a completed final stage when work remains.

        A Planner at a certified final stage cannot legally enqueue earlier-stage
        work and cannot write ``PIPELINE_STATE.json``. When it structurally
        returns ``project_done=False`` with no tasks in an open-ended campaign,
        give its advisory verdict to the Manager, which may roll back or hold.
        """
        if not getattr(self.config, "open_ended", False):
            return False
        if bool(getattr(verdict, "project_done", False)):
            return False
        if list(getattr(verdict, "new_tasks", []) or []):
            return False

        root = self._artifact_root()
        from ...skills.vertical_select import (
            resolve_vertical,
            vertical_reached_own_terminal_stage,
        )

        vertical = resolve_vertical(root)
        if not vertical_reached_own_terminal_stage(root, vertical):
            return False

        from ...manager import Manager

        manager = Manager(
            project_root=root,
            runner=self.planner_runner,
            skill_store=self.skill_store,
        )
        on_event = getattr(self.sink, "handle_event", None)
        decision = manager.decide_stage_transition(
            review=None,
            planner_verdict=verdict,
            project_root=root,
            on_event=on_event,
            open_ended=True,
            continuous_objective=self.config.continuous_objective,
        )
        if decision.action != "rollback":
            return False

        self._emit({
            "type": EventType.LIFE_MANAGER_STAGE_DECISION,
            "action": decision.action,
            "target_stage": decision.target_stage,
            "reason": decision.reason,
            "current_stage": decision.current_stage,
            "source": decision.source,
            "diagnostic": decision.diagnostic,
        })
        self._emit_status(
            "manager reopened open-ended campaign at "
            f"{decision.target_stage}"
        )
        self._last_open_ended_project_done_signature = ""
        self._reset_idle_backoff()
        return True

    def _resolve_vertical_once(self) -> None:
        """DECIDE + persist the active vertical exactly once per mission, BEFORE
        any gate/stage read (``resolve_vertical``) runs.

        Precedence:
        * An already-persisted vertical is TRUSTED as-is (sticky across daemon
          restarts; a chosen per-task vertical stays chosen).
        * Otherwise, when a continuous objective is set, the MANAGER AGENT
          decides it (``Manager.divide`` — one grounded call, no keyword
          classifier) and persists it (autonomously authoring a new data domain
          when no built-in fits).

        FAIL-HARD: an undecidable vertical, a missing backend, or a corrupt
        ``PIPELINE_STATE.json`` PROPAGATES — a mission that cannot determine its
        vertical must fail loudly, not silently run the research pipeline.
        """
        if getattr(self, "_vertical_resolved", False):
            return
        # Flip the guard immediately so this decision runs exactly once.
        self._vertical_resolved = True

        from ...skills import vertical_select as _vsel

        artifact_root = self._artifact_root()
        persisted = _vsel._persisted_vertical(artifact_root)
        if persisted is not None:
            # A read is sufficient: rewriting the whole pipeline state here can
            # race a Manager target/stage commit and restore stale fields.
            self._emit({
                "type": "life.vertical.resolved",
                "vertical": persisted,
                "profile_hint": "persisted",
                "agent_layer": "planner",
            })
            return

        if not self.config.continuous_objective:
            return

        from ...manager import Manager

        mgr = Manager(project_root=artifact_root, runner=self.planner_runner)
        division = mgr.divide(self.config.continuous_objective)
        self._emit({
            "type": "life.vertical.resolved",
            "vertical": division.vertical,
            "profile_hint": "manager",
            "agent_layer": "planner",
        })
        log.info(
            "life supervisor: resolved vertical = %s (manager-decided)",
            division.vertical,
        )

    def _plan_next_work(self) -> bool | None | str:
        """Call the planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and
        ``"daemon_handoff"`` if the planner asked the host to restart,
        and ``None`` when the planner fails and should be retried later.
        """
        terminal_idle = self._maybe_idle_after_unchanged_open_ended_done()
        if terminal_idle is not None:
            return terminal_idle

        self._planning_cycles += 1
        manager_intent = self._manager_intent_context()
        self._emit({
            "type": EventType.LIFE_PLANNER_START,
            "cycle": self._planning_cycles,
            "objective": self.config.continuous_objective[:200],
            "manager_intent": manager_intent,
        })

        wiki_collect_task = self._wiki_collect_task_if_due_under_blocker()
        if wiki_collect_task is not None:
            return self._enqueue_wiki_collect_task(wiki_collect_task)

        if self.planner_runner is None:
            self._emit_status("planner error: no planner runner wired; retry later")
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": "no planner runner wired",
            })
            return None

        # Only skip the planner on an operator-only external blocker when the
        # full EMNLP gate is active. A ``--bounded`` mission
        # (``full_paper_gate=False``) does not require the external benchmark
        # targets, so it must fall through to the planner and reach its own
        # ``project_done`` instead of waiting forever on artifacts it never
        # needs. Mirrors the gating in
        # ``_defer_project_done_for_operator_external_blocker``.
        short_circuit = None
        if self._effective_full_paper_gate(self._artifact_root()):
            short_circuit = self._operator_external_blocker_short_circuit_decision(
                project_root=self._project_workdir(),
            )
        if short_circuit is not None:
            return self._record_planner_waiting(
                short_circuit,
                planner_cost_usd=0.0,
            )

        # The mission is now committing to real planning work — every idle /
        # blocked / no-runner / done short-circuit above has returned. Decide +
        # persist the vertical here (once, guarded), so the planner and its
        # downstream gate reads see a stable vertical. Placing it AFTER the
        # short-circuits means a blocked/idle cycle never triggers a Manager
        # decision (nor a wasted planner-runner call).
        self._resolve_vertical_once()

        artifact_root = self._artifact_root()
        from ...skills.vertical_select import (
            resolve_vertical,
            vertical_reached_own_terminal_stage,
        )

        vertical = resolve_vertical(artifact_root)
        if (
            not getattr(self.config, "open_ended", False)
            and not self._effective_full_paper_gate(artifact_root)
            and vertical_reached_own_terminal_stage(artifact_root, vertical)
            and not _research_project_done_issue(
                artifact_root,
                self.memory.journal.all(),
            )
        ):
            reason = f"bounded {vertical} vertical reached terminal stage"
            self._emit({
                "type": EventType.LIFE_PLANNER_VERDICT,
                "cycle": self._planning_cycles,
                "project_done": True,
                "reason": reason,
                "task_count": 0,
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "restart_daemon": False,
                "restart_reason": "",
            })
            self._emit_status(f"planner: project done — {reason}")
            return False

        journal_tail = self._render_journal_for_planner()

        runtime_note = self._planner_runtime_with_idle_note()

        subagent_family_failures = self._recent_subagent_family_failures()
        stuck_families_note = self._stuck_subagent_families_note(subagent_family_failures)

        try:
            from ...planner import Planner

            planner = Planner(self.planner_runner, skill_store=self.skill_store)
            # Enable streaming so planner output flows through the event sink
            ctx = getattr(self.runner, "stream_to", None)
            stream_ctx = ctx(self.sink) if ctx else None
            if stream_ctx:
                stream_ctx.__enter__()
            try:
                verdict = planner.plan_next(
                    continuous_objective=self.config.continuous_objective,
                    journal_tail=journal_tail,

                    planning_cycle=self._planning_cycles - 1,
                    runtime_change_summary="\n\n".join(
                        part for part in (
                            self._manager_intent_prompt_block(manager_intent),
                            stuck_families_note,
                            runtime_note,
                        ) if part
                    ),
                    config=self._planner_config(),
                )
            finally:
                if stream_ctx:
                    stream_ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: planner raised; retrying later")
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": f"{type(exc).__name__}: {exc}",
            })
            return None

        planner_cost_usd = usd_for_tokens(
            self.reviewer_model,
            verdict.input_tokens,
            verdict.cached_input_tokens,
            verdict.output_tokens,
            reasoning_output_tokens=verdict.reasoning_output_tokens,
            price_lookup=price_for,
        ) + copilot_usd_for_premium_requests(verdict.premium_requests)

        if verdict.error:
            if self._reconcile_open_ended_terminal_stage(verdict):
                return PLAN_RETRY
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": verdict.error,
                "raw_text": verdict.raw_text,
            })
            self._emit_status(f"planner error: {verdict.error}; retry later")
            # A planner error is a no-work outcome: back off before retrying so
            # a persistently-failing planner cannot spin every poll interval.
            self._enter_idle_backoff()
            return PLAN_ERROR

        verdict = self._defer_project_done_for_operator_external_blocker(verdict)

        # First-class await-external: the planner intentionally idled because
        # the project is blocked on a live, nonterminal external job and there
        # is no new high-impact work. NOT an error, NOT make-work — record a
        # lightweight waiting entry and back off (escalating) before re-checking.
        if verdict.waiting:
            record = self._record_planner_waiting(
                verdict,
                planner_cost_usd=planner_cost_usd,
            )
            # Stall-breaker: if the planner has idled K+ cycles on the same
            # blocker, force a verification probe so reality (not a memory of
            # the blocker) drives the next decision. Running it next tick resets
            # the idle backoff via _reset_idle_backoff().
            if self._maybe_dispatch_verification_probe(verdict):
                return True
            return record

        # The planner's tasks are trusted. Deterministic gate-repair is only
        # used as a fallback when the planner itself fails (verdict.error above).

        if (
            verdict.project_done
            and self._effective_full_paper_gate(self._artifact_root())
            and not self._journal_has_full_paper_gate_success()
        ):
            from ...planner import TaskSpec

            verdict = replace(
                verdict,
                project_done=False,
                reason=(
                    "full-pipeline final-submission readiness is required before "
                    "project_done; queueing final submission proof"
                ),
                new_tasks=[
                    TaskSpec(
                        title="Prove final submission readiness",
                        objective=(
                            "Project-final task. Scope: final_submission. "
                            "Complete and verify every item on the full research "
                            "pipeline checklist (research → run → analysis → draft → "
                            "submission). The reviewer will certify completion only "
                            "when EVERY checklist item is satisfied with concrete "
                            "evidence (command output, file contents, query rows). "
                            "Do not declare done based on a single stage, a pilot run, "
                            "or an underlength draft; inspect any unmet checklist item "
                            "and repair experiments, baselines, ablations, paper "
                            "contract, figures, citations, manifest, or submission "
                            "state as needed until the reviewer certifies the project."
                        ),
                        impact_score=5,
                        impact_area="requirement_gap",
                        evidence=(
                            "Planner attempted project_done without an event "
                            "certifying full-pipeline final-submission readiness."
                        ),
                        scope=PLANNER_SCOPE_FINAL_SUBMISSION,
                    )
                ],
            )

        if verdict.project_done:
            research_done_issue = _research_project_done_issue(
                self._artifact_root(),
                self.memory.journal.all(),
            )
            if research_done_issue:
                verdict = replace(
                    verdict,
                    project_done=False,
                    reason=(
                        "Research project completion gate held: "
                        f"{research_done_issue}. A completed report or bounded cycle "
                        "does not satisfy the persisted research target."
                    ),
                    new_tasks=[],
                )

        if verdict.project_done and self.config.open_ended:
            self._last_open_ended_project_done_signature = (
                self._open_ended_terminal_idle_signature()
            )
            self._enter_idle_backoff()
            self._emit({
                "type": EventType.LIFE_PLANNER_VERDICT,
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": len(verdict.new_tasks),
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
                "open_ended_objective": True,
            })
            self._emit_status(
                "planner: project done — continuing later for open-ended objective"
            )
            return PLAN_RETRY

        if verdict.project_done:
            self._emit({
                "type": EventType.LIFE_PLANNER_VERDICT,
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": len(verdict.new_tasks),
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
            })
            self._emit_status(
                f"planner: project done — {verdict.reason}"
            )
            if verdict.restart_daemon and self._handle_planner_restart(
                verdict.restart_reason
            ):
                self._emit_status("daemon_handoff")
                return PLAN_HANDOFF
            return False

        if verdict.restart_daemon and not verdict.new_tasks:
            restart_reason = verdict.restart_reason or verdict.reason
            self._emit({
                "type": EventType.LIFE_PLANNER_VERDICT,
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": 0,
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": True,
                "restart_reason": restart_reason,
            })
            if self._handle_planner_restart(restart_reason):
                self._emit_status("daemon_handoff")
                return PLAN_HANDOFF
            self._emit_status("planner requested daemon restart but host did not restart")
            self._enter_idle_backoff()
            return PLAN_ERROR

        if not verdict.new_tasks:
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": "planner produced no tasks",
                "raw_text": verdict.raw_text,
            })
            self._emit_status("planner error: produced no tasks; retry later")
            # No tasks, no waiting flag, not done: a degenerate no-work cycle.
            # Back off so repeated empty plans cannot spin the daemon.
            self._enter_idle_backoff()
            return PLAN_ERROR

        try:
            existing_items = self.memory.backlog.all()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog before planning")
            existing_items = []

        seen_signatures: dict[tuple[str, str], BacklogItem] = {}
        for existing in existing_items:
            if existing.status not in PLANNER_DEDUP_STATUSES:
                continue
            signature = _planner_task_signature(existing.title, existing.objective)
            if existing.status in {"pending", "running"}:
                seen_signatures[signature] = existing
            elif signature not in seen_signatures:
                seen_signatures[signature] = existing

        recent_failures = self._recent_no_progress_failures()
        added_titles: list[str] = []
        skipped_duplicate_titles: list[str] = []
        skipped_recent_failure_titles: list[str] = []
        skipped_subagent_family_failure_titles: list[str] = []
        added_impact_scores: list[int] = []

        # Add new tasks to the backlog.
        #
        # Two passes so a planner-emitted DAG can be wired up before anything is
        # enqueued. Pass 1 builds the surviving items (after dedup / recent-
        # failure skips, exactly as before) WITHOUT adding them yet, and records
        # each task's local ``key`` → real ``item.id`` in ``key_map``. Pass 2
        # resolves each task's ``deps`` (local keys → real ids) onto the item and
        # only then adds it. A flat task (no key/deps) flows through with an empty
        # dep list, so its enqueue is byte-for-byte identical to the old path.
        key_map: dict[str, str] = {}
        pending_items: list[tuple[Any, Any]] = []  # (task, item)
        for task in verdict.new_tasks:
            sanitized_title = _sanitize_planner_task_text(task.title)
            sanitized_objective = _sanitize_planner_task_text(task.objective)
            sanitized_evidence = _sanitize_planner_task_text(task.evidence)
            if (
                sanitized_title != task.title
                or sanitized_objective != task.objective
                or sanitized_evidence != task.evidence
            ):
                task = replace(
                    task,
                    title=sanitized_title,
                    objective=sanitized_objective,
                    evidence=sanitized_evidence,
                )
            signature = _planner_task_signature(task.title, task.objective)
            duplicate_item = seen_signatures.get(signature)
            if duplicate_item is not None:
                skipped_duplicate_titles.append(task.title)
                duplicate_reason = (
                    "duplicate completed task"
                    if duplicate_item.status == "done"
                    else "duplicate pending/running task"
                )
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_item_id": duplicate_item.id,
                    "matched_status": duplicate_item.status,
                    "reason": duplicate_reason,
                })
                continue
            recent_failure = recent_failures.get(signature)
            if recent_failure is not None:
                skipped_recent_failure_titles.append(task.title)
                failure_extra = getattr(recent_failure, "extra", {}) or {}
                failure_signature = _entry_task_signature(recent_failure)
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_item_id": failure_extra.get("item_id"),
                    "matched_title": recent_failure.title,
                    "matched_status": failure_extra.get("terminal_status") or failure_extra.get("status"),
                    "matched_stop_reason": failure_extra.get("stop_reason") or failure_extra.get("failure_reason"),
                    "matched_signature": (
                        {
                            "title": failure_signature[0],
                            "objective": failure_signature[1],
                        }
                        if failure_signature is not None
                        else None
                    ),
                    "skip_category": "recent_no_progress_failure",
                    "reason": "recent no_progress failure",
                })
                continue
            family_failure = next(
                (
                    ff for ff in subagent_family_failures.values()
                    if self._task_mentions_family(task, ff.family)
                ),
                None,
            )
            if family_failure is not None:
                skipped_subagent_family_failure_titles.append(task.title)
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_family": family_failure.family,
                    "matched_streak": family_failure.streak,
                    "matched_last_task_id": family_failure.last_task_id,
                    "matched_last_state": family_failure.last_state,
                    "matched_last_reason": family_failure.last_reason,
                    "skip_category": "recent_subagent_family_failure",
                    "reason": (
                        f"subagent family {family_failure.family!r} has failed "
                        f"{family_failure.streak} times in a row unresolved"
                    ),
                })
                continue
            item_budget = self._item_iteration_budget()
            item = BacklogItem.new(
                title=task.title,
                objective=task.objective,
                priority=100,
                max_cost_usd=item_budget,
                tags=self._planner_task_tags(task),
                iterate=True,
                iteration_max_cycles=self._item_iteration_cycles(),
                iteration_budget_usd=item_budget,
            )
            # Reserve the signature now so a later sibling in the SAME batch
            # with an identical title/objective still de-dupes against this
            # one (matches the old single-pass behaviour). The item is not
            # added to the backlog until pass 2.
            seen_signatures[signature] = item
            if getattr(task, "key", ""):
                key_map[task.key] = item.id
            pending_items.append((task, item))

        # Pass 2: resolve local dep keys to real item ids, then enqueue. Only
        # intra-batch deps are supported — a key the planner referenced but did
        # not define in THIS batch (typo, or an unsupported cross-cycle ref) is
        # dropped with a warning so a stray key cannot wedge the item forever.
        for task, item in pending_items:
            task_deps = list(getattr(task, "deps", []) or [])
            if task_deps:
                resolved_ids, unresolved_keys = _resolve_task_dep_ids(
                    task_deps, key_map
                )
                item.deps = resolved_ids
                if unresolved_keys:
                    log.warning(
                        "life supervisor: dropping unresolved planner dep "
                        "key(s) %s for task %r (only same-batch new_tasks deps "
                        "are supported)",
                        unresolved_keys,
                        item.title,
                    )
            self.memory.backlog.add(item)
            added_titles.append(item.title)
            added_impact_scores.append(task.impact_score)
            self._emit({
                "type": EventType.LIFE_PLANNER_TASK_ADDED,
                "item_id": item.id,
                "title": item.title,
                "objective": item.objective,
                "deps": list(item.deps),
                "priority": item.priority,
                "branch_id": item.id,
                "parent_branch_id": item.deps[0] if item.deps else None,
                "impact_score": task.impact_score,
                "impact_area": task.impact_area,
                "manager_intent": manager_intent,
            })

        self._emit({
            "type": EventType.LIFE_PLANNER_VERDICT,
            "cycle": self._planning_cycles,
            "project_done": verdict.project_done,
            "reason": verdict.reason,
            "task_count": len(verdict.new_tasks),
            "enqueued_tasks": len(added_titles),
            "skipped_duplicate_tasks": len(skipped_duplicate_titles),
            "skipped_recent_failure_tasks": len(skipped_recent_failure_titles),
            "skipped_subagent_family_failure_tasks": len(skipped_subagent_family_failure_titles),
            "enqueued_titles": added_titles,
            "enqueued_impact_scores": added_impact_scores,
            "skipped_duplicate_titles": skipped_duplicate_titles,
            "skipped_recent_failure_titles": skipped_recent_failure_titles,
            "skipped_subagent_family_failure_titles": skipped_subagent_family_failure_titles,
            "stuck_subagent_families": {
                family: failure.streak
                for family, failure in subagent_family_failures.items()
            },
            "input_tokens": verdict.input_tokens,
            "cached_input_tokens": verdict.cached_input_tokens,
            "output_tokens": verdict.output_tokens,
            "cost_usd": planner_cost_usd,
            "restart_daemon": verdict.restart_daemon,
            "restart_reason": verdict.restart_reason,
            "manager_intent": manager_intent,
        })
        if verdict.restart_daemon and self._handle_planner_restart(
            verdict.restart_reason
        ):
            self._emit_status("daemon_handoff")
            return PLAN_HANDOFF
        if not added_titles:
            self._enter_idle_backoff()
            self._emit_status(
                "planner: all proposed tasks were filtered; retrying after backoff"
            )
            return PLAN_RETRY
        # Real new work was queued: clear the no-work backoff so the next cycle
        # runs promptly.
        self._reset_idle_backoff()
        return True


__all__ = ["PlanningCycleMixin"]
