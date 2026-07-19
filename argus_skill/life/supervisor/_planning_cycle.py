"""One continuous-planner cycle: decide vertical, plan, dedupe, enqueue."""

from __future__ import annotations

import logging
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ...core.pricing import price_for, usd_for_tokens
from ..memory import BacklogItem
from ._constants import (
    MANAGER_RECONCILE_AFTER_IDLE_CYCLES,
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


def _revision_context_refs(revision_request: dict[str, Any]) -> list[dict[str, str]]:
    report = revision_request.get("planner_report")
    report = report if isinstance(report, dict) else {}
    raw_refs = report.get("evidence_files")
    if not isinstance(raw_refs, list):
        return []
    refs: list[dict[str, str]] = []
    for raw in raw_refs[:8]:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        if not path:
            continue
        refs.append({
            "kind": "artifact",
            "ref": path[:400],
            "why": str(raw.get("why") or "").strip()[:600],
            "content_hash": str(raw.get("content_hash") or "").strip()[:128],
        })
    return refs


def _render_revision_request(
    revision_request: dict[str, Any],
    active_items: list[BacklogItem],
) -> str:
    report = revision_request.get("planner_report")
    report = report if isinstance(report, dict) else {}
    lines = [
        "DYNAMIC PLAN REVISION REQUEST (Reviewer-authored, L4 decides):",
        f"- reason: {str(report.get('plan_signal_reason') or '').strip()}",
        "- remaining active nodes:",
    ]
    lines.extend(
        f"  - {item.node_key or item.id}: [{item.status}] {item.title}"
        for item in active_items
    )
    refs = _revision_context_refs(revision_request)
    if refs:
        lines.append("- evidence files to open before replanning:")
        lines.extend(f"  - {ref['ref']}: {ref['why']}" for ref in refs)
    lines.append(
        "Return a complete replacement batch for the remaining active nodes. "
        "Completed nodes are immutable. Do not return project_done. Exception: if "
        "current_stage itself makes the prerequisite repair illegal, return "
        "waiting=true with a waiting_contract whose "
        "stage_reconciliation_required=true; emit no replacement tasks and let the "
        "Manager decide HOLD versus ROLLBACK. Never use this exception for polling "
        "or an ordinary implementation blocker."
    )
    return "\n".join(lines)


def _research_project_done_issue(
    project_root: object,
    journal_entries: list[Any],
) -> str:
    """Require a current-target reviewer certification before Planner success."""
    from ...core.research_contract import (
        adapt_legacy_research_result_payload,
        research_completion_issue,
        resolve_research_target_contract,
        resolve_research_target_set_at,
    )

    target_contract = resolve_research_target_contract(project_root)
    target_level = target_contract.selected_level
    if target_contract.required and target_level is None:
        return "missing_research_target_level"
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
        research_result = adapt_legacy_research_result_payload(extra)
        if not research_completion_issue(
            research_result,
            research_target_level=target_level,
            scope=str(extra.get("scope") or "") if isinstance(extra, dict) else "",
        ):
            return ""
    return f"missing_{target_level}_reviewer_certification"


class PlanningCycleMixin:
    def _independent_overlap_task(self, verdict: Any) -> Any | None:
        """Turn a live-job wait into one useful, non-conflicting mission."""
        if not bool(getattr(verdict, "waiting", False)):
            return None
        if bool(getattr(verdict, "project_done", False)):
            return None
        if list(getattr(verdict, "new_tasks", []) or []):
            return None
        contract = getattr(verdict, "waiting_contract", None)
        if bool(getattr(contract, "operator_action_required", False)):
            return None
        root = self._artifact_root()
        try:
            from ...engineer.background_subagents import scan_inflight_subagents

            watched = [job for job in scan_inflight_subagents(root) if job.self_watched]
        except Exception:  # noqa: BLE001 - overlap is a throughput optimization
            return None
        if not watched:
            return None
        title = "Advance independent work while background job runs"
        try:
            if any(
                item.status in {"pending", "running"} and item.title == title
                for item in self.memory.backlog.all()
            ):
                return None
        except Exception:  # noqa: BLE001
            return None
        from ...planner import TaskSpec
        from ...skills.stage_checklists import current_stage

        stage = current_stage(root)
        job_ids = ", ".join(job.task_id for job in watched[:4])
        objective = (
            f"Bounded overlap mission while current_stage remains `{stage}` and "
            f"healthy self-watched background job(s) `{job_ids}` continue. Do not "
            "poll, restart, stop, or duplicate those jobs. Produce one concrete "
            "current-stage deliverable that does not depend on their terminal "
            "result: platform/evaluator repair, data or provenance preparation, "
            "analysis code/scaffolding, claim-evidence organization, or manuscript "
            "prose with explicit placeholders as applicable. Inspect the current "
            "stage and existing artifacts first; preserve result-dependent claims "
            "as placeholders. Do not edit Manager-owned stage state."
        )
        return TaskSpec(
            title=title,
            objective=objective,
            impact_score=5,
            impact_area="throughput",
            evidence=f"live self-watched jobs: {job_ids}",
            scope="bounded",
            stage_closing=False,
        )

    def _emit_planner_verdict(
        self,
        *,
        status: PlannerVerdictStatus,
        reason: str,
        completion_kind: str,
        resume_outcome: bool | str,
        terminal_signature: str = "",
        **details: Any,
    ) -> bool:
        raise NotImplementedError

    def _retry_pending_planner_verdict(self) -> tuple[bool, bool | str | None]:
        raise NotImplementedError

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

    def _reconcile_open_ended_planner_waiting(self, verdict: Any) -> str:
        """Let the Manager repair a stage/Planner mutual wait.

        Planner explicitly requests reconciliation when ``current_stage`` blocks
        prerequisite work. A missed request still gets one liveness review after
        repeated idle cycles. The Manager alone decides HOLD versus ROLLBACK.
        """
        if not getattr(self.config, "open_ended", False):
            return ""
        if not bool(getattr(verdict, "waiting", False)):
            return ""
        if bool(getattr(verdict, "project_done", False)):
            return ""
        if list(getattr(verdict, "new_tasks", []) or []):
            return ""

        contract = getattr(verdict, "waiting_contract", None)
        blocker_fingerprint, recheck_token = self._waiting_contract_key(contract)
        if not blocker_fingerprint or not recheck_token:
            return ""

        # Manager is the sole stage authority, but it is not the operator and
        # cannot expand the operator's scope.  Never invoke wait reconciliation
        # when fresh operator input is the declared (or parser-inferred) gate.
        if bool(getattr(contract, "operator_action_required", False)):
            self._planner_waits_since_reconciliation = 0
            return ""

        explicitly_requested = bool(
            getattr(contract, "stage_reconciliation_required", False)
        )

        root = self._artifact_root()
        from ...skills.stage_checklists import current_stage

        stage = current_stage(root)
        key = (
            stage,
            blocker_fingerprint,
            recheck_token,
            explicitly_requested,
        )
        last_key = getattr(
            self,
            "_last_planner_wait_reconciliation_key",
            None,
        )
        key_changed = key != last_key
        waits_since_reconciliation = (
            1
            if key_changed
            else int(
                getattr(self, "_planner_waits_since_reconciliation", 0) or 0
            ) + 1
        )
        self._last_planner_wait_reconciliation_key = key
        self._planner_waits_since_reconciliation = waits_since_reconciliation
        if not (
            (explicitly_requested and key_changed)
            or waits_since_reconciliation >= MANAGER_RECONCILE_AFTER_IDLE_CYCLES
        ):
            return ""

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
        self._emit({
            "type": EventType.LIFE_MANAGER_STAGE_DECISION,
            "action": decision.action,
            "target_stage": decision.target_stage,
            "reason": decision.reason,
            "current_stage": decision.current_stage,
            "source": decision.source,
            "diagnostic": decision.diagnostic,
            "trigger": "planner_waiting_reconciliation",
            "resolves_wait": bool(getattr(decision, "resolves_wait", False)),
        })

        if decision.source == "manager_llm" or decision.action == "rollback":
            self._planner_waits_since_reconciliation = 0
        else:
            # Backend/failsafe HOLDs are not authoritative. Retry next wait.
            self._planner_waits_since_reconciliation = (
                MANAGER_RECONCILE_AFTER_IDLE_CYCLES
            )

        if decision.diagnostic == "planner_wait_advance_rejected":
            persisted = self._persist_manager_planner_feedback(
                stage=stage,
                reason=decision.reason,
                diagnostic=decision.diagnostic,
            )
            if not persisted:
                self._emit_status(
                    "failed to persist Manager feedback for Planner; retry later"
                )
                return False
            self._deactivate_planner_waiting_contract()
            self._last_planner_wait_reconciliation_key = None
            self._planner_waits_since_reconciliation = 0
            self._last_open_ended_project_done_signature = ""
            self._reset_idle_backoff()
            self._emit({
                "type": "life.manager.feedback.persisted",
                "stage": stage,
                "reason": decision.reason,
                "diagnostic": decision.diagnostic,
            })
            self._emit_status(
                f"Manager rejection returned to Planner for {stage} replanning"
            )
            return "hold"

        if (
            decision.action == "hold"
            and decision.source == "manager_llm"
            and bool(getattr(decision, "resolves_wait", False))
        ):
            self._resolve_planner_waiting_contract(
                manager_reason=decision.reason,
                target_stage=decision.target_stage,
            )
            self._last_planner_wait_reconciliation_key = None
            self._planner_waits_since_reconciliation = 0
            self._reset_idle_backoff()
            self._emit_status(
                "manager resolved planner wait while holding "
                f"current stage {decision.target_stage}"
            )
            return "hold"

        if decision.action != "rollback":
            return ""

        self._deactivate_planner_waiting_contract()
        self._clear_planner_wait_resolution()
        self._last_planner_wait_reconciliation_key = None
        self._planner_waits_since_reconciliation = 0
        self._last_open_ended_project_done_signature = ""
        self._reset_idle_backoff()
        self._emit_status(
            "manager resolved planner wait by reopening "
            f"{decision.target_stage}"
        )
        return "rollback"

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

    def _plan_next_work(
        self,
        *,
        revision_request: dict[str, Any] | None = None,
    ) -> bool | None | str:
        """Call the planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and
        ``"daemon_handoff"`` if the planner asked the host to restart,
        and ``None`` when the planner fails and should be retried later.
        """
        revision_request = (
            dict(revision_request) if isinstance(revision_request, dict) else None
        )
        operator_messages = (
            self._drain_user_inbox() if revision_request is None else []
        )
        if operator_messages:
            self._deactivate_planner_waiting_contract()
            self._reset_idle_backoff()
        revision_active_items: list[BacklogItem] = []
        expected_plan_id = ""
        expected_plan_version = 0
        if revision_request is not None:
            expected_plan_id = str(
                revision_request.get("expected_plan_id") or ""
            )
            expected_plan_version = int(
                revision_request.get("expected_plan_version") or 0
            )
            if not expected_plan_id:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "unversioned backlog items cannot be revised",
                    "expected_plan_id": "",
                    "expected_plan_version": expected_plan_version,
                })
                return PLAN_ERROR
            try:
                revision_active_items = [
                    item
                    for item in self.memory.backlog.all()
                    if item.plan_id == expected_plan_id
                    and item.plan_version == expected_plan_version
                    and item.status not in {"done", "failed", "skipped", "superseded"}
                ]
            except Exception as exc:  # noqa: BLE001
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": f"cannot inspect active plan: {type(exc).__name__}: {exc}",
                })
                return PLAN_ERROR
            requested_item_id = str(revision_request.get("item_id") or "")
            if not revision_active_items or requested_item_id not in {
                item.id for item in revision_active_items
            }:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "plan revision conflict: active revision changed",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                })
                return PLAN_ERROR
            self._emit({
                "type": EventType.LIFE_PLAN_REVISION_PROPOSED,
                "expected_plan_id": expected_plan_id,
                "expected_plan_version": expected_plan_version,
                "active_item_ids": [item.id for item in revision_active_items],
                "reason": str(
                    revision_request.get("planner_report", {}).get(
                        "plan_signal_reason", ""
                    )
                    if isinstance(revision_request.get("planner_report"), dict)
                    else ""
                ),
            })

        if revision_request is None:
            retried, retry_outcome = self._retry_pending_planner_verdict()
            if retried:
                return retry_outcome
        terminal_idle = (
            None
            if revision_request is not None
            else self._maybe_idle_after_unchanged_open_ended_done()
        )
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

        wiki_collect_task = (
            None
            if revision_request is not None
            else self._wiki_collect_task_if_due_under_blocker()
        )
        if wiki_collect_task is not None:
            return self._enqueue_wiki_collect_task(wiki_collect_task)

        if self.planner_runner is None:
            if revision_request is not None:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "no planner runner wired",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                })
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
        if (
            revision_request is None
            and self._effective_full_paper_gate(self._artifact_root())
        ):
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
            revision_request is None
            and
            not getattr(self.config, "open_ended", False)
            and not self._effective_full_paper_gate(artifact_root)
            and vertical_reached_own_terminal_stage(artifact_root, vertical)
            and not _research_project_done_issue(
                artifact_root,
                self.memory.journal.all(),
            )
        ):
            reason = f"bounded {vertical} vertical reached terminal stage"
            delivered = self._emit_planner_verdict(
                status=PlannerVerdictStatus.COMPLETED,
                completion_kind="project_completed",
                resume_outcome=False,
                terminal_signature=self._open_ended_terminal_idle_signature(),
                cycle=self._planning_cycles,
                project_done=True,
                reason=reason,
                task_count=0,
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                enqueued_titles=[],
                skipped_duplicate_titles=[],
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                restart_daemon=False,
                restart_reason="",
            )
            if not delivered:
                return PLAN_RETRY
            self._emit_status(f"planner: project done — {reason}")
            return False

        journal_tail = self._render_journal_for_planner()

        runtime_note = self._planner_runtime_with_idle_note()
        operator_note = (
            "LIVE OPERATOR GUIDANCE (supersedes stale blocker state):\n"
            + "\n".join(f"- {message}" for message in operator_messages)
            if operator_messages
            else ""
        )
        revision_note = (
            _render_revision_request(revision_request, revision_active_items)
            if revision_request is not None
            else ""
        )

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
                            operator_note,
                            stuck_families_note,
                            runtime_note,
                            revision_note,
                        ) if part
                    ),
                    config=self._planner_config(),
                )
            finally:
                if stream_ctx:
                    stream_ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: planner raised; retrying later")
            if revision_request is not None:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": f"planner raised: {type(exc).__name__}: {exc}",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                })
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
            if revision_request is not None:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": verdict.error,
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                })
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

        overlap_task = self._independent_overlap_task(verdict)
        if overlap_task is not None:
            from dataclasses import replace

            verdict = replace(
                verdict,
                waiting=False,
                waiting_reason="",
                waiting_contract=None,
                reason=(
                    "healthy background job continues; scheduling independent "
                    "overlap work instead of idling"
                ),
                new_tasks=[overlap_task],
            )
            self._emit({
                "type": "life.planner.wait_overridden",
                "cycle": self._planning_cycles,
                "task_title": overlap_task.title,
                "reason": verdict.reason,
            })

        # First-class await-external: the planner intentionally idled because
        # the project is blocked on a live, nonterminal external job and there
        # is no new high-impact work. NOT an error, NOT make-work — record a
        # lightweight waiting entry and back off (escalating) before re-checking.
        if verdict.waiting:
            if self._load_manager_planner_feedback() is not None:
                self._emit({
                    "type": "life.manager.feedback.unresolved",
                    "reason": "planner returned waiting instead of revision tasks",
                })
                self._emit_status(
                    "planner ignored unresolved Manager feedback; retry later"
                )
                self._enter_idle_backoff()
                return PLAN_ERROR
            if revision_request is not None:
                reconciliation_result = (
                    self._reconcile_open_ended_planner_waiting(verdict)
                )
                if reconciliation_result == "rollback":
                    superseding_plan_id = (
                        f"manager-rollback-{BacklogItem.new_id()}"
                    )
                    try:
                        result = self.memory.backlog.supersede_active_plan(
                            expected_plan_id=expected_plan_id,
                            expected_version=expected_plan_version,
                            supersede_item_ids=[
                                item.id for item in revision_active_items
                            ],
                            superseded_by_plan_id=superseding_plan_id,
                            reason=verdict.waiting_reason or verdict.reason,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._emit({
                            "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                            "reason": (
                                "Manager rolled back stage but active plan could "
                                f"not be retired: {type(exc).__name__}: {exc}"
                            ),
                            "expected_plan_id": expected_plan_id,
                            "expected_plan_version": expected_plan_version,
                        })
                        return PLAN_ERROR
                    for item_id in result.superseded_ids:
                        self._emit({
                            "type": EventType.LIFE_PLAN_NODE_SUPERSEDED,
                            "item_id": item_id,
                            "plan_id": expected_plan_id,
                            "plan_version": expected_plan_version,
                            "superseded_by_plan_id": superseding_plan_id,
                            "reason": verdict.waiting_reason or verdict.reason,
                        })
                    self._emit({
                        "type": "life.plan.revision.rolled_back",
                        "old_plan_id": expected_plan_id,
                        "old_plan_version": expected_plan_version,
                        "superseded_item_ids": list(result.superseded_ids),
                        "reason": verdict.waiting_reason or verdict.reason,
                    })
                    return PLAN_RETRY
                if reconciliation_result:
                    return PLAN_RETRY
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "replacement planner returned waiting",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                })
            if (
                revision_request is None
                and self._reconcile_open_ended_planner_waiting(verdict)
            ):
                return PLAN_RETRY
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

        # The Planner has explicitly moved on from waiting. Preserve the
        # historical probed-token set for deduplication, but stop injecting the
        # old blocker into subsequent planning context.
        self._deactivate_planner_waiting_contract()
        self._last_planner_wait_reconciliation_key = None
        self._planner_waits_since_reconciliation = 0

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
                        stage_closing=True,
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

        if revision_request is not None and verdict.project_done:
            self._emit({
                "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                "reason": "replacement planner cannot declare project_done",
                "expected_plan_id": expected_plan_id,
                "expected_plan_version": expected_plan_version,
            })
            return PLAN_ERROR

        if verdict.project_done and self.config.open_ended:
            self._enter_idle_backoff()
            terminal_signature = self._open_ended_terminal_idle_signature()
            delivered = self._emit_planner_verdict(
                status=PlannerVerdictStatus.COMPLETED,
                completion_kind="project_completed",
                resume_outcome=PLAN_RETRY,
                terminal_signature=terminal_signature,
                cycle=self._planning_cycles,
                project_done=verdict.project_done,
                reason=verdict.reason,
                task_count=len(verdict.new_tasks),
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                enqueued_titles=[],
                skipped_duplicate_titles=[],
                input_tokens=verdict.input_tokens,
                cached_input_tokens=verdict.cached_input_tokens,
                output_tokens=verdict.output_tokens,
                cost_usd=planner_cost_usd,
                restart_daemon=verdict.restart_daemon,
                restart_reason=verdict.restart_reason,
                open_ended_objective=True,
            )
            if not delivered:
                return PLAN_RETRY
            self._emit_status(
                "planner: project done — continuing later for open-ended objective"
            )
            return PLAN_RETRY

        if verdict.project_done:
            delivered = self._emit_planner_verdict(
                status=PlannerVerdictStatus.COMPLETED,
                completion_kind="project_completed",
                resume_outcome=False,
                terminal_signature=self._open_ended_terminal_idle_signature(),
                cycle=self._planning_cycles,
                project_done=verdict.project_done,
                reason=verdict.reason,
                task_count=len(verdict.new_tasks),
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                enqueued_titles=[],
                skipped_duplicate_titles=[],
                input_tokens=verdict.input_tokens,
                cached_input_tokens=verdict.cached_input_tokens,
                output_tokens=verdict.output_tokens,
                cost_usd=planner_cost_usd,
                restart_daemon=verdict.restart_daemon,
                restart_reason=verdict.restart_reason,
            )
            if not delivered:
                return PLAN_RETRY
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
            if revision_request is not None:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "replacement planner requested daemon restart",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                })
            delivered = self._emit_planner_verdict(
                status=PlannerVerdictStatus.INFRA_BLOCKED,
                completion_kind="daemon_handoff",
                resume_outcome=PLAN_HANDOFF,
                cycle=self._planning_cycles,
                project_done=verdict.project_done,
                reason=verdict.reason,
                task_count=0,
                enqueued_tasks=0,
                skipped_duplicate_tasks=0,
                enqueued_titles=[],
                skipped_duplicate_titles=[],
                input_tokens=verdict.input_tokens,
                cached_input_tokens=verdict.cached_input_tokens,
                output_tokens=verdict.output_tokens,
                cost_usd=planner_cost_usd,
                restart_daemon=True,
                restart_reason=restart_reason,
            )
            if not delivered:
                return PLAN_RETRY
            if self._handle_planner_restart(restart_reason):
                self._emit_status("daemon_handoff")
                return PLAN_HANDOFF
            self._emit_status("planner requested daemon restart but host did not restart")
            self._enter_idle_backoff()
            return PLAN_ERROR

        if not verdict.new_tasks:
            if revision_request is not None:
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": "planner produced no replacement tasks",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                })
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
        revision_active_ids = {item.id for item in revision_active_items}
        for existing in existing_items:
            if existing.id in revision_active_ids:
                continue
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
        new_plan_id = f"plan-{BacklogItem.new_id()}"
        new_plan_version = (
            expected_plan_version + 1 if revision_request is not None else 1
        )
        revision_context_refs = (
            _revision_context_refs(revision_request)
            if revision_request is not None
            else []
        )

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
            if (
                duplicate_item is not None
                and bool(getattr(task, "stage_closing", False))
                and not self._item_requires_independent_review(duplicate_item)
            ):
                # Review semantics are part of task identity. A prior ordinary
                # or self-reviewed task cannot satisfy a later stage-closing
                # certification request, even when its prose is identical.
                duplicate_item = None
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
            item_id = BacklogItem.new_id()
            item = BacklogItem.new(
                item_id=item_id,
                title=task.title,
                objective=task.objective,
                priority=100,
                tags=self._planner_task_tags(task),
                iterate=True,
                iteration_max_cycles=self._item_iteration_cycles(),
                plan_id=new_plan_id,
                plan_version=new_plan_version,
                node_key=str(getattr(task, "key", "") or item_id),
                context_refs=(
                    list(getattr(task, "context_refs", []) or [])
                    or revision_context_refs
                ),
                acceptance_check=str(
                    getattr(task, "acceptance_check", "")
                    or getattr(task, "evidence", "")
                ),
                non_goals=list(getattr(task, "non_goals", []) or []),
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
            if revision_request is None:
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
                    "plan_id": item.plan_id,
                    "plan_version": item.plan_version,
                    "node_key": item.node_key,
                })

        if revision_request is not None and pending_items:
            replacement_items = [item for _task, item in pending_items]
            try:
                revision_result = self.memory.backlog.apply_plan_revision(
                    expected_plan_id=expected_plan_id,
                    expected_version=expected_plan_version,
                    new_plan_id=new_plan_id,
                    new_version=new_plan_version,
                    supersede_item_ids=[
                        item.id for item in revision_active_items
                    ],
                    new_items=replacement_items,
                    reason=str(
                        revision_request.get("planner_report", {}).get(
                            "plan_signal_reason", ""
                        )
                        if isinstance(revision_request.get("planner_report"), dict)
                        else ""
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._emit({
                    "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "expected_plan_id": expected_plan_id,
                    "expected_plan_version": expected_plan_version,
                })
                return PLAN_ERROR
            for item_id in revision_result.superseded_ids:
                self._emit({
                    "type": EventType.LIFE_PLAN_NODE_SUPERSEDED,
                    "item_id": item_id,
                    "plan_id": expected_plan_id,
                    "plan_version": expected_plan_version,
                    "superseded_by_plan_id": new_plan_id,
                    "reason": str(
                        revision_request.get("planner_report", {}).get(
                            "plan_signal_reason", ""
                        )
                        if isinstance(revision_request.get("planner_report"), dict)
                        else ""
                    ),
                })
            for task, item in pending_items:
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
                    "plan_id": item.plan_id,
                    "plan_version": item.plan_version,
                    "node_key": item.node_key,
                })
            self._emit({
                "type": EventType.LIFE_PLAN_REVISION_COMMITTED,
                "old_plan_id": expected_plan_id,
                "old_plan_version": expected_plan_version,
                "new_plan_id": new_plan_id,
                "new_plan_version": new_plan_version,
                "superseded_item_ids": list(revision_result.superseded_ids),
                "added_item_ids": list(revision_result.added_ids),
            })

        if revision_request is not None and not pending_items:
            self._emit({
                "type": EventType.LIFE_PLAN_REVISION_REJECTED,
                "reason": "all replacement tasks were filtered",
                "expected_plan_id": expected_plan_id,
                "expected_plan_version": expected_plan_version,
            })

        delivered = self._emit_planner_verdict(
            status=PlannerVerdictStatus.PLANNED,
            completion_kind="tasks_scheduled",
            resume_outcome=True if added_titles else PLAN_RETRY,
            cycle=self._planning_cycles,
            project_done=verdict.project_done,
            reason=verdict.reason,
            task_count=len(verdict.new_tasks),
            enqueued_tasks=len(added_titles),
            skipped_duplicate_tasks=len(skipped_duplicate_titles),
            skipped_recent_failure_tasks=len(skipped_recent_failure_titles),
            skipped_subagent_family_failure_tasks=len(skipped_subagent_family_failure_titles),
            enqueued_titles=added_titles,
            enqueued_impact_scores=added_impact_scores,
            skipped_duplicate_titles=skipped_duplicate_titles,
            skipped_recent_failure_titles=skipped_recent_failure_titles,
            skipped_subagent_family_failure_titles=skipped_subagent_family_failure_titles,
            stuck_subagent_families={
                family: failure.streak
                for family, failure in subagent_family_failures.items()
            },
            input_tokens=verdict.input_tokens,
            cached_input_tokens=verdict.cached_input_tokens,
            output_tokens=verdict.output_tokens,
            cost_usd=planner_cost_usd,
            restart_daemon=verdict.restart_daemon,
            restart_reason=verdict.restart_reason,
            manager_intent=manager_intent,
        )
        if not delivered:
            return PLAN_RETRY
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
        self._clear_manager_planner_feedback()
        # Real new work was queued: clear the no-work backoff so the next cycle
        # runs promptly.
        self._reset_idle_backoff()
        return True


__all__ = ["PlanningCycleMixin"]
