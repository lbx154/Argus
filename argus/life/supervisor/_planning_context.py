"""Planner context, continuous reload, blockers, and escalation policy."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.operator_context import operator_context_state_root
from ...core.planner_verdict import PlannerVerdictStatus
from ...core.workspace_facts import fact_text
from ..memory import _TERMINAL_STATUSES, BacklogItem, manager_direct_in_hand
from ._constants import (
    PLAN_AWAITING,
    PLAN_RETRY,
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
    PLANNER_TASKS_FILTERED_DIAGNOSTIC,
)
from ._helpers import _operator_only_external_blocker_wait_reason_for_project
from ._planner_failure_backoff import planner_failure_kind

log = logging.getLogger(__name__)


class PlanningContextMixin:
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

    def _planner_task_tags(self, task: Any) -> list[str]:
        scope = self._normalize_planner_scope(getattr(task, "scope", ""))
        if scope == PLANNER_SCOPE_FINAL_SUBMISSION and not self._final_submission_scope_applies(
            self._artifact_root()
        ):
            # ``final_submission`` is a terminal-gate transport scope. A Planner
            # may still choose it for a vertical that has no terminal gate at
            # all, and persisting that tag makes ``tick()`` retire the task as
            # stale and re-plan it forever. Normalize at the enqueue boundary;
            # the old skip path remains as migration support for persisted rows.
            scope = PLANNER_SCOPE_BOUNDED
        tags = ["planner", f"scope:{scope}"]
        if scope == PLANNER_SCOPE_BOUNDED:
            tags.append("bounded_dag_node")
        if bool(getattr(task, "stage_closing", False)):
            tags.append("stage_closing")
        if bool(getattr(task, "stage_closing", False)) or bool(
            getattr(task, "require_independent_review", True)
        ):
            tags.append("review:required")
        else:
            tags.append("review:waived")
        if bool(getattr(task, "skip_stage_transition", False)):
            tags.append("stage_transition:skip")
        if bool(getattr(task, "allow_skill_changes", False)):
            tags.append("skill_changes:allowed")
        if bool(getattr(task, "parallel_safe", False)):
            tags.append("parallel_safe")
        if bool(getattr(task, "stage_package", False)):
            tags.append("stage_package")
        # Bind Planner work to the stage in which it was proposed.  This is
        # host-owned routing metadata, not a model judgement.  It lets the
        # enqueue boundary distinguish "re-run the same certification" from
        # "certify a later stage" even when the Planner rewords the title.
        stage_reader = getattr(self, "_current_pipeline_stage", None)
        stage = ""
        if callable(stage_reader):
            try:
                stage = str(stage_reader() or "").strip().lower()
            except Exception:  # noqa: BLE001 - a missing stage tag is legacy-safe
                stage = ""
        if stage:
            tags.append(f"stage:{stage}")
        return tags

    @staticmethod
    def _item_requires_independent_review(item: BacklogItem) -> bool:
        normalized_tags = {
            str(tag).strip().lower().replace("-", "_")
            for tag in item.tags
        }
        if normalized_tags & {"review:waived", "independent_review:waived"}:
            return False
        return True

    @staticmethod
    def _item_is_stage_closing(item: BacklogItem) -> bool:
        return any(
            str(tag).strip().lower().replace("-", "_") == "stage_closing"
            for tag in item.tags
        )

    @staticmethod
    def _item_skips_stage_transition(item: BacklogItem) -> bool:
        return any(
            str(tag).strip().lower().replace("-", "_")
            == "stage_transition:skip"
            for tag in item.tags
        )

    def _planner_authorization_prompt_block(self) -> str:
        try:
            from ...manager.control_state import CampaignControlStore

            store = CampaignControlStore(
                Path(self.memory.root),
                project_root=self._project_workdir(),
            )
            rows = [store.public_authorization(row) for row in store.current_authorizations()]
        except (OSError, TypeError, ValueError):
            log.warning("failed to load current Manager authorizations", exc_info=True)
            return ""
        if not rows:
            return ""
        lines = [
            "## Current Manager authorizations",
            "These are verified, non-secret references. Use one only for a task "
            "whose exact action and writable paths match. Set authorization_id "
            "and authorization_action on that task; never invent or reuse an id.",
        ]
        for row in rows:
            lines.append("- " + json.dumps(row, ensure_ascii=False, sort_keys=True))
        return "\n".join(lines)

    def _validated_task_authorization(self, task: Any) -> tuple[str, str]:
        authorization_id = str(getattr(task, "authorization_id", "") or "").strip()
        action = str(getattr(task, "authorization_action", "") or "").strip().lower()
        if not authorization_id and not action:
            return "", ""
        if not authorization_id or not action:
            raise ValueError("planner task authorization reference is incomplete")
        if action != "validator_repair":
            raise ValueError("only validator_repair has an enforced mission capability")
        from ...manager.control_state import CampaignControlStore

        store = CampaignControlStore(
            Path(self.memory.root),
            project_root=self._project_workdir(),
        )
        rows = {
            str(row.get("authorization_id") or ""): row for row in store.current_authorizations()
        }
        row = rows.get(authorization_id)
        if row is None:
            raise ValueError("planner task references a stale authorization")
        if action not in set(row.get("allowed_actions") or []):
            raise ValueError("planner task action is outside Manager authorization")
        return authorization_id, action

    @staticmethod
    def _normalize_planner_scope(scope: object) -> str:
        normalized = str(scope or PLANNER_SCOPE_BOUNDED).strip().lower().replace("-", "_")
        if normalized == PLANNER_SCOPE_FINAL_SUBMISSION:
            return PLANNER_SCOPE_FINAL_SUBMISSION
        return PLANNER_SCOPE_BOUNDED

    @staticmethod
    def _planner_scope_from_item(item: BacklogItem) -> str:
        for tag in item.tags:
            normalized = str(tag).strip().lower().replace("-", "_")
            if normalized in {
                f"scope:{PLANNER_SCOPE_FINAL_SUBMISSION}",
                f"planner_scope:{PLANNER_SCOPE_FINAL_SUBMISSION}",
            }:
                return PLANNER_SCOPE_FINAL_SUBMISSION
            if normalized in {
                f"scope:{PLANNER_SCOPE_BOUNDED}",
                f"planner_scope:{PLANNER_SCOPE_BOUNDED}",
            }:
                return PLANNER_SCOPE_BOUNDED
        return ""

    def _render_backlog_item_metadata(self, item: BacklogItem) -> str:
        scope = self._planner_scope_from_item(item)
        context_refs = [ref for ref in getattr(item, "context_refs", []) if isinstance(ref, dict)]
        acceptance_check = str(getattr(item, "acceptance_check", "") or "").strip()
        plan_hypothesis = str(getattr(item, "plan_hypothesis", "") or "").strip()
        goal_contribution = str(getattr(item, "goal_contribution", "") or "").strip()
        expected_regressions = str(
            getattr(item, "expected_regressions", "") or ""
        ).strip()
        decision_rule = str(getattr(item, "decision_rule", "") or "").strip()
        execution_workdir = str(
            getattr(item, "execution_workdir", "") or ""
        ).strip()
        owns_paths = [
            str(path).strip()
            for path in getattr(item, "owns_paths", [])
            if str(path).strip()
        ]
        non_goals = [
            str(value).strip() for value in getattr(item, "non_goals", []) if str(value).strip()
        ]
        if (
            not scope
            and not item.tags
            and not getattr(item, "plan_id", "")
            and not context_refs
            and not acceptance_check
            and not plan_hypothesis
            and not goal_contribution
            and not expected_regressions
            and not decision_rule
            and not execution_workdir
            and not owns_paths
            and not non_goals
        ):
            return ""
        is_paper_long_horizon = self.config.paper_mission
        lines = ["## Task context"]
        if item.plan_id:
            lines.append(f"- dynamic_plan: {item.plan_id} v{item.plan_version}")
        if item.node_key:
            lines.append(f"- node_key: {item.node_key}")
        if scope:
            lines.append(f"- planner_scope: {scope}")
        if execution_workdir:
            lines.append(
                "- execution_repository_request: " + execution_workdir
            )
        if owns_paths:
            lines.append("- writable_paths: " + ", ".join(owns_paths))
            lines.append(
                "  Do not write outside these paths; sibling missions may be "
                "working concurrently."
            )
        if self._item_requires_independent_review(item):
            lines.append(
                "- independent_review: REQUIRED; this mission must close through "
                "the normal independent Reviewer path."
            )
        if self._item_skips_stage_transition(item):
            lines.append(
                "- stage_transition: DISABLED; the review verdict must not invoke "
                "the Manager's formal stage writer."
            )
        if item.tags:
            lines.append("- tags: " + ", ".join(item.tags))
        if any(str(tag).strip().lower() == "operator_priority" for tag in item.tags):
            lines.append(
                "- authority: this is the latest explicit operator task. Execute its "
                "requested actions before autonomously derived cleanup or hardening; "
                "do not replace its outcome with project housekeeping."
            )
        if plan_hypothesis:
            lines.append("- planner_working_hypothesis: " + plan_hypothesis)
            lines.append(
                "  This is revisable technical strategy, not an operator-owned constraint."
            )
        if goal_contribution:
            lines.append("- goal_frontier_contribution: " + goal_contribution)
        if expected_regressions:
            lines.append("- allowed_temporary_regressions: " + expected_regressions)
        if decision_rule:
            lines.append("- revise_split_or_abandon_when: " + decision_rule)
        if acceptance_check:
            lines.append("- what_good_looks_like: " + acceptance_check)
        if non_goals:
            lines.append("- non_goals:")
            lines.extend(f"  - {value}" for value in non_goals)
        if scope == PLANNER_SCOPE_FINAL_SUBMISSION:
            lines.append(
                "- final_paper_review: improve the current paper and sources, then "
                "yield to the independent Reviewer. Do not create certification "
                "packets unless they are scientifically useful."
            )
        elif scope == PLANNER_SCOPE_BOUNDED:
            if is_paper_long_horizon:
                lines.append(
                    "- paper_optimization_task: this is a single task, but it is "
                    "part of a long-horizon paper objective. Complete the requested "
                    "scientific or writing increment without expanding it into "
                    "paperwork for unrelated stages."
                )
            else:
                lines.append(
                    "- bounded_task: judge this item against its own acceptance criteria; "
                    "do not hold it to the project-final publication standard unless "
                    "the objective explicitly asks for that."
                )
        if context_refs:
            lines.append("")
            lines.append("### Context references — Open only as needed; contents are not preloaded")
            for ref in context_refs:
                kind = str(ref.get("kind") or "artifact")
                target = str(ref.get("ref") or "").strip()
                if not target:
                    continue
                why = str(ref.get("why") or "").strip()
                suffix = f" — {why}" if why else ""
                lines.append(f"- [{kind}] {target}{suffix}")
                attachment_fields = (
                    ("attachment_id", str(ref.get("attachment_id") or "").strip()),
                    ("original_name", str(ref.get("original_name") or "").strip()),
                    ("mime", str(ref.get("mime") or "").strip()),
                    ("size_bytes", str(ref.get("size_bytes") or "").strip()),
                    ("integrity", str(ref.get("integrity") or "").strip()),
                )
                for label, value in attachment_fields:
                    if value:
                        lines.append(f"  {label}: {value}")
        return "\n".join(lines)

    def _journal_has_final_certification(self) -> bool:
        """Decide whether the project-final completion gate has passed.

        Source of truth (post-validator-retirement): the event timeline. A
        ``final_submission`` mission is certified complete only when the
        reviewer returns a full-pipeline completion verdict, which the
        supervisor records as a ``life.mission.completed`` event carrying
        ``final_submission_certified = True``. We no longer call the
        retired hardcoded paper-readiness validator — the reviewer's
        checklist verdict is the single source of truth.

        Fail-closed: only an explicit certified entry bound to the current
        project-state signature counts.
        """
        try:
            entries = self.memory.journal.all()
        except Exception:  # noqa: BLE001
            return False
        current_signature = self._final_submission_signature()
        for entry in reversed(entries):
            if getattr(entry, "kind", "") != "mission_complete":
                continue
            extra = getattr(entry, "extra", {}) or {}
            if isinstance(extra, dict) and bool(extra.get("final_submission_certified")):
                from ...skills.vertical_select import resolve_vertical_if_decided

                if (
                    resolve_vertical_if_decided(self._artifact_root()) == "research"
                    and (Path(self._project_workdir()) / "paper/main.tex").is_file()
                ):
                    from types import SimpleNamespace

                    from ...core.venue_review import current_venue_acceptance_issue

                    if current_venue_acceptance_issue(
                        SimpleNamespace(
                            venue_review=extra.get("venue_review"),
                            venue_review_snapshot=extra.get("venue_review_snapshot"),
                            review_source="reviewer",
                        ),
                        state_root=self._artifact_root(), artifact_root=self._project_workdir(),
                    ):
                        continue
                manuscript_binding = extra.get("manuscript_snapshot")
                if (
                    (Path(self._project_workdir()) / "paper/main.tex").is_file()
                    and not isinstance(manuscript_binding, dict)
                ):
                    continue
                if isinstance(manuscript_binding, dict):
                    try:
                        from ...core.manuscript_snapshot import (
                            manuscript_review_status,
                        )

                        if manuscript_review_status(
                            extra, self._project_workdir()
                        ).get("status") != "current":
                            continue
                    except Exception:  # noqa: BLE001 - unreadable binding fails closed
                        continue
                certified_signature = str(extra.get("final_submission_signature") or "")
                if certified_signature:
                    if bool(current_signature) and certified_signature == current_signature:
                        return True
                    continue
                from ..terminal_state import project_unchanged_since

                if self._legacy_final_submission_cert_matches(
                    entry=entry,
                    current_signature=current_signature,
                    unchanged_since=project_unchanged_since(
                        project_root=self._project_workdir(),
                        cutoff=float(getattr(entry, "ts", 0.0) or 0.0),
                        state_root=operator_context_state_root(self.memory),
                    ),
                ):
                    return True
        return False

    def _effective_final_certification_gate(self, workdir: object) -> bool:
        """Whether the full-pipeline final-submission gate applies here.

        Returns ``self.config.final_certification_gate`` AND the active vertical's
        completion gate requiring independent final certification. The
        final-submission completion gate only makes sense for a *research*
        vertical: an optimize mission (``math_synth``, or the community
        ``speedrun``) runs just the optimize+measure stages and has no
        submission package to certify, so requiring the gate would wedge it
        forever. AND-ing with the vertical's own completion gate keeps research
        behavior identical (gate stays on) while letting optimize missions
        accept ``project_done`` straight from the run loop (gate off).
        The read side is deterministic and exception-free, so this never spends
        a token.
        """
        if not self.config.final_certification_gate:
            return False
        from ...skills.vertical_select import resolve_vertical_if_decided
        from ...verticals._base import load_vertical_contract

        vertical = resolve_vertical_if_decided(workdir)
        if vertical is None:
            # The Manager has not decided + persisted a vertical on this root.
            # An undecided mission is definitionally not at its final-submission
            # gate, so the gate does not apply and the project keeps running.
            #
            # This asks ``resolve_vertical_if_decided`` rather than
            # ``resolve_vertical`` because the latter does not raise for an
            # undecided project — it logs and answers ``research``, whose
            # completion gate is ``certified``. Reading that fallback here would
            # turn "nobody has decided yet" into "the paper gate applies",
            # which is exactly backwards, and would do it silently. Today every
            # production caller passes a state root that does carry the
            # decision, and ``config.final_certification_gate`` is itself
            # computed from a persisted certified vertical, so the fallback was
            # masked twice over rather than being safe.
            return False
        return load_vertical_contract(
            vertical, project_root=workdir
        ).completion_gate == "certified"

    def _final_submission_scope_applies(self, workdir: object) -> bool:
        """Whether ``scope:final_submission`` can ever be satisfied here.

        Two gates consume that scope, and each is keyed on a different part of
        the vertical contract:

        * ``_journal_has_final_certification`` guards the full-paper pipeline
          and reads ``completion_gate == "certified"``.
        * ``_research_project_done_issue`` guards a persisted research target
          and reads a non-empty ``research_target_levels``.

        Both are cleared by exactly one artifact — the journal entry
        ``_mission_execution_settlement`` writes for a succeeded mission whose
        ``item_scope`` is ``final_submission``. Keying the enqueue-time
        downgrade on the *first* gate alone therefore stranded every vertical
        that declares research targets without a certified completion gate:
        ``math`` (and the community ``materials``) demand a scope the enqueue boundary refuses
        to persist, so no project in either could reach ``project_done``.
        Testbed runs 8, 9 and 10 all died here — once fixes #45 and #46 landed
        the Planner did emit ``TASK_SCOPE=final_submission``, and the item was
        still enqueued as ``scope:bounded``.

        The bounded verticals this downgrade was written to protect
        (``software``, a ``perf_tuning`` data domain, and friends) declare
        neither, so they still normalize to ``bounded`` exactly as before.

        The research-target arm reads the Manager-*decided* vertical only, with
        no compatibility fallback. ``resolve_vertical`` answers ``research`` for
        an undecided project, and a stale default ``research`` state inferring
        its way into a paper-final task is the precise accident this downgrade
        exists to prevent. An undecided project is therefore already covered by
        the certification-gate arm above, on the same fallback, and does not
        need a second inferred route in.
        """
        if self._effective_final_certification_gate(workdir):
            return True
        from ...core.research_contract import research_target_contract
        from ...skills.vertical_select import resolve_vertical_if_decided
        from ...verticals._base import load_vertical_contract

        vertical = resolve_vertical_if_decided(workdir)
        if vertical is None:
            return False
        return research_target_contract(
            supported_levels=load_vertical_contract(
                vertical, project_root=workdir
            ).research_target_levels,
            selected_level=None,
        ).required

    def _final_submission_signature(self) -> str:
        from ..terminal_state import build_project_state_signature

        return build_project_state_signature(
            project_root=self._project_workdir(),
            state_root=operator_context_state_root(self.memory),
        )

    def _legacy_final_submission_cert_matches(
        self,
        *,
        entry: Any,
        current_signature: str,
        unchanged_since: bool,
    ) -> bool:
        """Bind one legacy certification to a state hash on first safe read."""
        path = Path(self.memory.root) / "legacy_final_submission_signatures.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        key = (
            f"{float(getattr(entry, 'ts', 0.0) or 0.0):.9f}:"
            f"{str(getattr(entry, 'title', '') or '')}"
        )
        bound = str(payload.get(key) or "")
        if bound:
            return bool(current_signature) and bound == current_signature
        if not unchanged_since or not current_signature:
            return False
        payload[key] = current_signature
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return True

    def _operator_only_external_blocker_wait_reason(self) -> str:
        """Return the reason only the operator can remove an external blocker.

        Generic: scans for operator-only external blocker artifacts,
        validates that local engineering is exhausted, and returns a human
        reason string. Empty string when nothing matches or when local action
        is still required.
        """
        return _operator_only_external_blocker_wait_reason_for_project(self._project_workdir())

    @staticmethod
    def _operator_external_blocker_short_circuit_decision(*, project_root: Path) -> Any | None:
        """Hand the slot to the operator before the planner runs when
        operator-only external artifacts are still absent.
        """
        reason = _operator_only_external_blocker_wait_reason_for_project(project_root)
        if not reason:
            return None
        from ...planner.planner import PlannerVerdict

        reason = f"{reason}; skipping planner cycle to avoid impossible repair-task loop"
        return PlannerVerdict(
            project_done=False,
            reason=reason,
            handoff="operator",
            operator_question=reason,
        )

    def _defer_project_done_for_operator_external_blocker(self, verdict: Any) -> Any:
        if not (
            getattr(verdict, "project_done", False)
            and self._effective_final_certification_gate(self._artifact_root())
            and not self._journal_has_final_certification()
        ):
            return verdict
        wait_reason = self._operator_only_external_blocker_wait_reason()
        if not wait_reason:
            return verdict
        # Only the operator can supply these artifacts; while they cannot be
        # asked the Manager decides in their place.
        return replace(
            verdict,
            project_done=False,
            reason=wait_reason,
            handoff="operator",
            operator_question=wait_reason,
            new_tasks=[],
            settled_instructions=(),
        )

    def _manager_intent_context(self) -> dict[str, Any]:
        """Latest user-intent interpretation from the canonical events timeline."""
        try:
            project = getattr(self.memory, "project", None)
            root = getattr(project, "root", None)
            if root is None:
                root = getattr(self.config, "project_state_dir", None)
            if root is None:
                root = getattr(self.memory, "root", None)
            if root is None:
                return {}
            try:
                continuous = json.loads(
                    (Path(root) / "continuous.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                return {}
            if not isinstance(continuous, dict) or not continuous.get("enabled"):
                return {}
            target_generation = int(continuous.get("generation", 0) or 0)
            target_objective = str(continuous.get("objective") or "").strip()
            data: dict[str, Any] | None = None
            for name in ("events.jsonl", "events.jsonl.1"):
                path = Path(root) / name
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for raw in reversed(lines):
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if str(event.get("type") or "") == "life.manager.intent.completed":
                        execution_task = event.get("execution_task")
                        if not isinstance(execution_task, str) or not execution_task.strip():
                            continue
                        event_generation = int(
                            event.get("continuous_generation", 0) or 0
                        )
                        if event_generation > target_generation:
                            continue
                        if str(execution_task).strip() != target_objective:
                            continue
                        data = event
                        break
                if data is not None:
                    break
            if data is None:
                return {}
            keep = (
                "intent_id",
                "source",
                "execution_task",
                "vertical",
                "kind",
                "stage",
                "current_stage",
                "workflow_mode",
                "require_independent_review",
                "research_target_level",
                "learned_vertical_status",
                "continuous_generation",
                "stages",
                "reason",
                "text",
                "error",
            )
            intent = {k: data.get(k) for k in keep if k in data}
            stage_reader = getattr(self, "_current_pipeline_stage", None)
            live_stage = (
                str(stage_reader() or "").strip()
                if callable(stage_reader)
                else ""
            )
            if live_stage:
                intent["stage"] = live_stage
                intent["current_stage"] = live_stage
            return intent
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _manager_intent_prompt_block(
        intent: dict[str, Any],
        _execution_objective: str = "",
    ) -> str:
        if not intent:
            return ""
        parts = [
            "## Manager routing boundary (authoritative)",
            f"VERTICAL={intent.get('vertical') or ''}",
            f"WORKFLOW={intent.get('workflow_mode') or ''}",
            "AUTHORITY=technical",
        ]
        strategic_context = str(
            intent.get("reason") or intent.get("text") or ""
        ).strip()
        if strategic_context:
            parts.extend([
                "",
                "## Manager strategic context",
                strategic_context,
            ])
        parts.extend([
            "",
            "Plan only work consistent with this Manager boundary. If it appears "
            "wrong, surface a Manager/Planner mismatch instead of silently "
            "switching scope.",
        ])
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Hot-reload continuous config
    # ------------------------------------------------------------------

    def _reload_continuous_config(self) -> None:
        """Update ``self.config.continuous`` from the config provider.

        Called at the top of every ``run()`` iteration so that changes
        from the cockpit (written to disk) take effect within seconds even
        when the supervisor is in a long continuous run.
        """
        provider = self.config.continuous_config_provider
        if provider is None:
            return
        try:
            supplied = provider()
            if not isinstance(supplied, tuple) or len(supplied) != 3:
                raise TypeError(
                    "continuous config provider must return "
                    "(enabled, objective, open_ended)"
                )
            enabled, objective, open_ended = supplied
            if not isinstance(enabled, bool) or not isinstance(open_ended, bool):
                raise TypeError(
                    "continuous config provider enabled/open_ended values "
                    "must be bool"
                )
            if not isinstance(objective, str):
                raise TypeError(
                    "continuous config provider objective must be str"
                )
            self.config.continuous = enabled
            self.config.open_ended = open_ended
            self.config.final_certification_gate = bool(
                self.config.paper_mission and enabled and open_ended
            )
            if objective:
                self.config.continuous_objective = objective
            runner_args = getattr(self.runner, "_args", None)
            if runner_args is not None:
                runner_args.open_ended = enabled and open_ended
                runner_args.continuous_objective = objective if enabled else ""
        except Exception:  # noqa: BLE001
            log.warning(
                "continuous config provider failed; keeping current values",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Planner — continuous improvement mode
    # ------------------------------------------------------------------

    def _record_planner_handoff(self, verdict: Any, state: Any = None) -> str:
        """The Planner adds no work now and names who acts next.

        Nothing is watched on its behalf: it is called again when something
        new is handed to it -- a mission ends, the operator writes, the Manager
        directs -- because its inputs then differ from the turn it answered.
        A question for the operator goes on the planning slot's card, which the
        operator answers, or the Manager while the operator cannot be asked.
        """
        reason = str(verdict.reason or verdict.operator_question or "").strip()
        if verdict.handoff == "operator":
            self._park_planner_question(
                state, str(verdict.operator_question or reason), reason,
            )
        self._enter_pause_backoff()
        self._emit(
            {
                "type": EventType.LIFE_PLANNER_IDLE,
                "cycle": self._planning_cycles,
                "reason": reason,
                "handoff": str(verdict.handoff or ""),
            }
        )
        self._emit_status(f"planner: nothing new to start — {reason}")
        return PLAN_AWAITING

    def _planner_objective_fingerprint(self) -> str:
        """The campaign a durable record belongs to: its objective, compared as text."""
        return str(getattr(self.config, "continuous_objective", "") or "")

    def _manager_planner_feedback_path(self) -> Path:
        root = Path(
            getattr(self.config, "project_state_dir", None)
            or getattr(self.memory, "root", None)
            or "."
        )
        return root / "manager-planner-feedback.json"

    def _manager_feedback_evidence_signature(self) -> str:
        """Fingerprint project evidence without backlog/process churn."""
        try:
            from ..terminal_state import build_terminal_idle_signature

            return build_terminal_idle_signature(
                objective=str(self.config.continuous_objective or ""),
                stage=str(self._current_pipeline_stage() or ""),
                backlog=(),
                artifact_root=self._artifact_root(),
                project_root=self._planner_workdir(),
                state_root=operator_context_state_root(self.memory),
                completion_contract=None,
            )
        except Exception:  # noqa: BLE001 - circuit remains conservative
            return ""

    def _load_manager_planner_feedback(self) -> dict[str, Any] | None:
        path = self._manager_planner_feedback_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, TypeError, ValueError):
            log.warning("Manager feedback is unreadable: %s", path, exc_info=True)
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        if (
            str(payload.get("objective_fingerprint") or "")
            != self._planner_objective_fingerprint()
        ):
            return None
        if not bool(payload.get("active")):
            return None
        if not str(payload.get("reason") or "").strip():
            return None
        return payload

    def _write_manager_planner_feedback(self, payload: dict[str, Any]) -> bool:
        path = self._manager_planner_feedback_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        try:
            tmp.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, path)
            return True
        except OSError:
            log.exception("failed to persist Manager feedback: %s", path)
            return False
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _manager_directive_signature(self) -> list[str]:
        """The newest Manager directive handed to the Planner, not one for the team."""
        from ...manager.directive import load_active_manager_directive

        record = load_active_manager_directive(operator_context_state_root(self.memory))
        return [record.planner_revision] if record is not None and record.planner_revision else []

    def _planner_visible_input_signature(
        self,
        *,
        operator_context_revision: int = 0,
    ) -> str:
        """The decision inputs a fresh Planner call would read now, as comparable text.

        Two cycles with equal inputs would hand the Planner the same
        objective, backlog, journal window, research plan, standing operator
        context, Manager feedback, and background-job states —
        so a second model call can only repeat the first answer. Wall-clock
        facts (operator presence, log churn from healthy live jobs, git status
        noise) are deliberately left out: live jobs rewrite their own files
        continuously. Unchanged inputs keep the Planner silent until they change.

        Fail-soft: an empty string disables the unchanged-input skip for this
        cycle rather than guessing.
        """
        try:
            entries = self._planner_journal_window()
            if entries is None:
                return ""
            feedback = self._load_manager_planner_feedback()
            payload = {
                "objective": str(self.config.continuous_objective or ""),
                "stage": str(self._current_pipeline_stage() or ""),
                "backlog": self._backlog_planning_signature(),
                "journal": sorted(
                    self._planner_journal_entry_key(entry) for entry in entries
                ),
                "research_plan": self._render_research_plan_for_planner(),
                "operator_context_revision": int(operator_context_revision),
                # The Manager's advisory direction reaches the Planner through
                # OperatorContext without moving its revision counter.
                "manager_directive": self._manager_directive_signature(),
                "manager_feedback": (
                    None
                    if feedback is None
                    else [
                        str(feedback.get("stage") or ""),
                        str(feedback.get("diagnostic") or ""),
                        str(feedback.get("reason") or ""),
                        int(feedback.get("attempts") or 0),
                    ]
                ),
                "dropped_dependency_note": bool(
                    getattr(self, "_planner_dropped_dependency_keys", []) or []
                ),
                "dropped_parallel_note": bool(
                    getattr(self, "_planner_dropped_parallel_marks", []) or []
                ),
            }
        except Exception:  # noqa: BLE001 - an unreadable input disables the skip
            log.debug("planner inputs unreadable; skip disabled", exc_info=True)
            return ""
        return fact_text(payload)

    def _maybe_skip_unchanged_planner_cycle(self, state: Any) -> str | None:
        """Keep the last decision without new work when nothing the Planner reads moved.

        The previous Planner call already answered exactly this input state
        with no new work; asking again buys the same answer at model price.
        Nor is anything the Planner's while every live task is one the Manager
        handed straight to an Engineer, still in that Engineer's or the
        operator's hands: the Planner hears of it when it ends. This is how the
        Planner is called only when something is handed to it. The gate runs
        only after every earlier intake short circuit has declined; elapsed
        time alone is not news. Returns the plan-cycle outcome when skipping,
        else ``None`` — and always leaves the freshly computed signature on
        ``state`` so the cycle's real outcome can arm the next skip.

        The record stays in memory: a turn without new work never acknowledges
        the operator input it read, so it is reconsidered after a restart, and a
        turn that carried operator words does not arm it.
        """
        current = self._planner_visible_input_signature(
            operator_context_revision=int(
                getattr(state, "operator_context_revision", 0) or 0
            )
        )
        transient_ids = [
            message.delivery_id
            for message in getattr(state, "inbox_delivery_messages", ())
            if getattr(message, "ephemeral", False)
            and not getattr(message, "host_report", False)
        ]
        if current and transient_ids:
            current += ":inbox:" + fact_text(sorted(transient_ids))
        state.planner_input_signature = current
        live = [
            item for item in self.memory.backlog.active()
            if item.status not in _TERMINAL_STATUSES
        ]
        direct_only = bool(live) and all(manager_direct_in_hand(item) for item in live)
        armed = str(getattr(self, "_planner_unchanged_skip_signature", "") or "")
        if not direct_only and (not current or current != armed):
            return None
        # Preserve whichever backoff family the campaign is already in: an
        # armed idle-exit clock keeps running, a recoverable pause stays one.
        if getattr(self, "_idle_since", None) is not None:
            self._enter_idle_backoff()
        else:
            self._enter_pause_backoff()
        # Said once when the Planner starts waiting, not on every pass that finds nothing new.
        if self._should_journal_idle_repeat("direct_handoff" if direct_only else "inputs_unchanged"):
            self._emit({
                "type": EventType.LIFE_PLANNER_IDLE,
                "cycle": self._planning_cycles,
                "reason": (
                    "every live task is the Manager's direct handoff; the "
                    "Planner is asked when one ends"
                    if direct_only else
                    "nothing the Planner reads has changed since its last "
                    "decision; keeping that decision without another model call"
                ),
                "model_call_skipped": True,
            })
        return PLAN_AWAITING

    def _arm_unchanged_planner_skip(self, state: Any) -> None:
        """Record or clear the unchanged-input skip after a real Planner call.

        Armed when the model answered this cycle without proposing work,
        whatever it said: the same inputs would only buy the same answer
        again, so the Planner is asked next when something it reads changes.
        Proposed work (enqueued or not), a completion accepted, or a failed
        backend call (which may be transient) clears the record. Cycles that
        never reached the model, including the skip itself, leave the record
        untouched so consecutive unchanged cycles keep skipping until an input
        changes.
        """
        if not bool(getattr(state, "planner_invoked", False)):
            return
        verdict = state.verdict
        if (
            str(getattr(state, "planner_input_signature", "") or "")
            and verdict is not None
            and not planner_failure_kind(verdict)
            and not getattr(verdict, "new_tasks", None)
            and not state.completion_accepted
        ):
            self._planner_unchanged_skip_signature = state.planner_input_signature
        else:
            self._planner_unchanged_skip_signature = ""

    def _backlog_planning_signature(self) -> str:
        """Digest of live backlog item ids and statuses.

        Feedback recorded because every proposed task duplicated existing
        backlog work stays true exactly as long as those items keep their
        status. Project files rewritten by live background jobs are not new
        planning evidence for that kind of feedback — judging it by the
        whole-tree signature made the feedback evaporate every cycle and the
        planner replan blind at the base backoff indefinitely. A task the
        Manager handed straight to an Engineer counts once it leaves their
        hands, not while it is queued or worked on.
        """
        rows = sorted(
            f"{item.id}:{item.status}" for item in self.memory.backlog.active()
            if not manager_direct_in_hand(item)
        )
        return fact_text(rows)

    def _manager_feedback_signature_for(self, diagnostic: str) -> str:
        if diagnostic == PLANNER_TASKS_FILTERED_DIAGNOSTIC:
            return self._backlog_planning_signature()
        if diagnostic == "bounded_completion_invariant_failed":
            return (
                f"{self._backlog_planning_signature()}:"
                f"{self._manager_feedback_evidence_signature()}"
            )
        return self._manager_feedback_evidence_signature()

    def _persist_manager_planner_feedback(
        self,
        *,
        stage: str,
        reason: str,
        diagnostic: str,
    ) -> bool:
        stage = str(stage or "").strip()
        reason = str(reason or "").strip()
        diagnostic = str(diagnostic or "").strip()
        evidence_signature = self._manager_feedback_signature_for(diagnostic)
        previous = self._load_manager_planner_feedback()
        # For filtered-task feedback the reason text embeds the planner's own
        # phrasing of the rejected titles, which shifts between otherwise
        # identical verdicts — an exact-reason match would restart the attempt
        # count every cycle, so the repeat limit could never engage.
        same_feedback = bool(
            previous is not None
            and str(previous.get("stage") or "") == stage
            and str(previous.get("diagnostic") or "") == diagnostic
            and str(previous.get("evidence_signature") or "") == evidence_signature
            and (
                diagnostic == PLANNER_TASKS_FILTERED_DIAGNOSTIC
                or str(previous.get("reason") or "") == reason
            )
        )
        attempts = int(previous.get("attempts") or 0) + 1 if same_feedback else 1
        created_at = (
            float(previous.get("created_at") or time.time())
            if same_feedback and previous is not None
            else time.time()
        )
        return self._write_manager_planner_feedback(
            {
                "version": 1,
                "active": True,
                "objective_fingerprint": self._planner_objective_fingerprint(),
                "stage": stage,
                "reason": reason,
                "diagnostic": diagnostic,
                "evidence_signature": evidence_signature,
                "operator_context_revision": getattr(
                    self, "_planning_operator_context_revision", 0,
                ),
                "attempts": attempts,
                "created_at": created_at,
                "updated_at": time.time(),
            }
        )

    def _record_manager_feedback_no_progress(self, reason: str) -> bool:
        feedback = self._load_manager_planner_feedback()
        if feedback is None:
            return True
        payload = dict(feedback)
        payload.update(
            attempts=int(feedback.get("attempts", 1) or 0) + 1,
            updated_at=time.time(),
            last_rejection=reason,
        )
        if not self._write_manager_planner_feedback(payload):
            self._emit_status("failed to persist unresolved Manager feedback")
            return False
        self._emit({
            "type": "life.manager.feedback.unresolved",
            "reason": reason,
            "attempts": payload["attempts"],
        })
        return True

    def _clear_manager_planner_feedback(self) -> None:
        state = self._load_manager_planner_feedback()
        if state is None:
            return
        state["active"] = False
        state["resolved_at"] = time.time()
        self._write_manager_planner_feedback(state)

    def _planner_dropped_dependency_runtime_note(self) -> str:
        """Tell the planner once which dependency keys its last DAG got wrong."""
        dropped = list(getattr(self, "_planner_dropped_dependency_keys", []) or [])
        if not dropped:
            return ""
        self._planner_dropped_dependency_keys = []
        lines = "\n".join(
            f"- {title!r} named {', '.join(repr(key) for key in keys)}"
            for title, keys in dropped
        )
        return (
            "DEPENDENCY KEYS DROPPED FROM YOUR LAST PLAN:\n"
            f"{lines}\n"
            "Those keys matched no backlog node and no durable background job, so "
            "the tasks were enqueued without them. Team ids, task labels quoted "
            "in evidence, and nodes you have not created are not dependencies. "
            "Depend only on node keys from this plan or on existing backlog items."
        )

    def _planner_dropped_parallel_runtime_note(self) -> str:
        """Tell the planner once which tasks lost their parallel flag."""
        dropped = list(getattr(self, "_planner_dropped_parallel_marks", []) or [])
        if not dropped:
            return ""
        self._planner_dropped_parallel_marks = []
        lines = "\n".join(
            f"- {title!r}: {reason}" for title, reason in dropped
        )
        return (
            "PARALLEL FLAGS DROPPED FROM YOUR LAST PLAN:\n"
            f"{lines}\n"
            "A task shares a mission slot only when it sets "
            "TASK_PARALLEL_SAFE=true together with usable TASK_OWNS_PATHS — "
            "literal, relative, project-internal paths (no wildcards), "
            "disjoint from every co-running task's paths. Without them the "
            "task runs serially."
        )

    def _manager_planner_feedback_runtime_note(self) -> str:
        state = self._load_manager_planner_feedback()
        if state is None:
            return ""
        diagnostic = str(state.get("diagnostic") or "")
        # These diagnostics already identify the missing process-owned record.
        # The enqueue boundary applies the required scope/review metadata to the
        # next task; the Planner only describes the verification work naturally.
        prescribes_final_submission = diagnostic in {
            "final_certification_missing",
            "research_target_incomplete",
        }
        prescribes_stage_closing = diagnostic == "staged_goal_gate_incomplete"
        if prescribes_final_submission:
            task_instruction = (
                "The missing invariant is final independent certification. Describe "
                "the next executable verification task naturally; the Host will "
                "record its final-submission scope and independent-review requirement."
            )
        elif prescribes_stage_closing:
            task_instruction = (
                "The missing invariant is the current stage's certified completion. Describe the "
                "next executable verification task naturally; the Host will record "
                "it as stage-closing work requiring independent review."
            )
        elif (
            diagnostic == "bounded_completion_invariant_failed"
            and "live backlog remains" in str(state.get("reason") or "")
        ):
            task_instruction = (
                "Existing live backlog work still owns completion. Do not create "
                "duplicate tasks for those rows. Plan only independent work that "
                "can proceed now, or end with `→ engineer` while the work in "
                "flight goes on."
            )
        else:
            task_instruction = (
                "You decide which tasks, if any, are appropriate; the harness does "
                "not prescribe a repair or delivery task."
            )
        return (
            "PLANNER VERDICT REJECTION (durable and unresolved):\n"
            f"- current_stage: {state.get('stage') or ''}\n"
            f"- diagnostic: {diagnostic}\n"
            f"- repeated_attempts: {int(state.get('attempts') or 1)}\n"
            f"- rejection_reason: {state.get('reason') or ''}\n"
            "The previous plan or completion verdict failed a framework-owned "
            "invariant. Re-plan now from the rejection reason and current evidence. "
            f"{task_instruction} Do not repeat the rejected "
            "verdict without new evidence."
        )

    def _current_planner_input_signature(self) -> str:
        """The Planner-visible inputs now, for deciding whether to ask again."""
        return self._planner_visible_input_signature(
            operator_context_revision=int(
                getattr(self, "_planning_operator_context_revision", 0) or 0
            )
        )

    def _wiki_collect_task_if_due_under_blocker(self) -> Any | None:
        """No automatic Wiki collection: Agents maintain semantic pages in missions."""
        return None

    def _enqueue_wiki_collect_task(self, task: Any) -> bool | str:
        item = BacklogItem.new(
            title=task.title,
            objective=task.objective,
            priority=100,
            tags=[*self._planner_task_tags(task), "wiki_collect"],
            iterate=True,
            iteration_max_cycles=1,
        )
        self.memory.backlog.add(item)
        self._emit(
            {
                "type": EventType.LIFE_PLANNER_TASK_ADDED,
                "cycle": self._planning_cycles,
                "item_id": item.id,
                "title": item.title,
                "objective": item.objective,
                "deps": list(item.deps),
                "priority": item.priority,
                "branch_id": item.id,
                "parent_branch_id": item.deps[0] if item.deps else None,
                "impact_score": task.impact_score,
                "impact_area": task.impact_area,
            }
        )
        delivered = self._emit_planner_verdict(
            status=PlannerVerdictStatus.PLANNED,
            completion_kind="tasks_scheduled",
            resume_outcome=PLAN_RETRY,
            cycle=self._planning_cycles,
            project_done=False,
            reason="external blocker present; scheduling one wiki_collect escape-valve mission",
            task_count=1,
            enqueued_tasks=1,
            skipped_duplicate_tasks=0,
            enqueued_titles=[item.title],
            enqueued_impact_scores=[task.impact_score],
            skipped_duplicate_titles=[],
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )
        return True if delivered else "planner_retry"


__all__ = ["PlanningContextMixin"]
