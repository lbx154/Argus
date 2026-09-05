"""Planner context, continuous reload, blockers, and escalation policy."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import PlannerVerdictStatus
from ...core.wake_sources import normalize_wake_sources
from ..memory import BacklogItem
from ._constants import (
    OPERATOR_WAIT_TURN_REGRANT_SECONDS,
    PLAN_AWAITING,
    PLAN_RETRY,
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
    PLANNER_TASKS_FILTERED_DIAGNOSTIC,
    STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS,
    VERIFICATION_PROBE_AFTER_IDLE_CYCLES,
    VERIFICATION_PROBE_COOLDOWN_SECONDS,
)
from ._helpers import _operator_only_external_blocker_wait_reason_for_project

log = logging.getLogger(__name__)

_DEGRADED_WAIT_POLL_SECONDS = 300


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
        if bool(getattr(task, "stage_repair", False)):
            tags.append("stage_repair")
        if bool(getattr(task, "allow_skill_changes", False)):
            tags.append("skill_changes:allowed")
        if bool(getattr(task, "parallel_safe", False)):
            tags.append("parallel_safe")
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
                    "- paper_optimization_task: this is a bounded mission, but it is "
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
                        state_root=Path(self.memory.root),
                    ),
                ):
                    return True
        return False

    def _effective_final_certification_gate(self, workdir: object) -> bool:
        """Whether the full-pipeline final-submission gate applies here.

        Returns ``self.config.final_certification_gate`` AND the active vertical's
        completion gate requiring independent final certification. The
        final-submission completion gate only makes sense for a *research*
        vertical: a ``speedrun`` mission runs just the optimize+measure stages
        and has no submission package to certify, so requiring the gate would
        wedge it forever. AND-ing with the vertical's own completion gate keeps
        research behavior identical (gate stays on) while letting speedrun
        missions accept ``project_done`` straight from the run loop (gate off).
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
        ``math`` and ``materials`` demand a scope the enqueue boundary refuses
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
            state_root=Path(self.memory.root),
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
        """Return a waiting reason for an operator-only external blocker.

        Generic: scans for operator-only external blocker artifacts,
        validates that local engineering is exhausted, and returns a human
        reason string. Empty string when nothing matches or when local action
        is still required.
        """
        return _operator_only_external_blocker_wait_reason_for_project(self._project_workdir())

    @staticmethod
    def _operator_external_blocker_short_circuit_decision(*, project_root: Path) -> Any | None:
        """Return a waiting verdict before planner runs when operator-only
        external artifacts are still absent.
        """
        reason = _operator_only_external_blocker_wait_reason_for_project(project_root)
        if not reason:
            return None
        from ...planner.planner import PlannerVerdict

        return PlannerVerdict(
            project_done=False,
            reason=(f"{reason}; skipping planner cycle to avoid impossible repair-task loop"),
            waiting=True,
            waiting_reason=(
                f"{reason}; skipping planner cycle to avoid impossible repair-task loop"
            ),
            new_tasks=[],
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
        return replace(
            verdict,
            project_done=False,
            waiting=True,
            waiting_reason=wait_reason,
            reason=wait_reason,
            new_tasks=[],
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
            enabled, objective, open_ended = provider()
            self.config.continuous = enabled
            self.config.open_ended = open_ended
            self.config.final_certification_gate = bool(
                self.config.paper_mission and open_ended
            )
            if objective:
                self.config.continuous_objective = objective
        except Exception:  # noqa: BLE001
            log.debug("continuous config provider raised; keeping current values")

    # ------------------------------------------------------------------
    # Planner — continuous improvement mode
    # ------------------------------------------------------------------

    def _record_planner_waiting(self, verdict: Any) -> str:
        contract = getattr(verdict, "waiting_contract", None)
        contract_state = (
            self._persist_planner_waiting_contract(contract) if contract is not None else None
        )
        # A declared external wait is active campaign work, not terminal
        # inactivity. Keep exponential polling without arming daemon idle-exit.
        sleep_s = self._enter_pause_backoff()
        reason = verdict.waiting_reason or verdict.reason or "awaiting external dependency"
        self._emit(
            {
                "type": EventType.LIFE_PLANNER_WAITING,
                "cycle": self._planning_cycles,
                "reason": reason,
                "consecutive_idle_cycles": self._consecutive_idle_planner_cycles,
                "suggested_sleep_s": sleep_s,
                "waiting_contract": self._waiting_contract_event_payload(
                    contract_state,
                    contract,
                ),
                "waiting_contract_persisted": (contract is None or contract_state is not None),
            }
        )
        self._emit_status(f"awaiting external dependency: {reason}")
        return PLAN_AWAITING

    def _planner_waiting_contract_path(self) -> Path:
        root = Path(
            getattr(self.config, "project_state_dir", None)
            or getattr(self.memory, "root", None)
            or "."
        )
        objective_fingerprint = self._planner_waiting_objective_fingerprint()
        return root / f"planner-waiting-contract-{objective_fingerprint[:16]}.json"

    def _planner_waiting_objective_fingerprint(self) -> str:
        objective = str(getattr(self.config, "continuous_objective", "") or "")
        return hashlib.sha256(objective.encode("utf-8")).hexdigest()

    @staticmethod
    def _planner_waiting_runtime_revision() -> str:
        """Identify the runtime policy that authored a durable wait."""
        from ...core.runtime_identity import source_root
        from ...release import release_identity

        identity = release_identity(source_root())
        return str(
            identity.get("runtime_source_digest")
            or identity.get("manifest_source_digest")
            or identity.get("release_id")
            or "unknown"
        )

    def _clear_planner_wait_control_binding(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        if payload.get("state_revision") is None:
            return
        from ...manager.control_state import CampaignControlStore

        control = CampaignControlStore(
            Path(self.memory.root),
            project_root=self._project_workdir(),
        )
        identity = control.campaign_identity(
            objective=str(getattr(self.config, "continuous_objective", "") or "")
        )
        control.clear_wait_if_current(
            identity=identity,
            expected_state_revision=int(payload.get("state_revision") or 0),
            expected_wait_id=str(payload.get("wait_id") or ""),
            reason=reason,
        )

    def _manager_planner_feedback_path(self) -> Path:
        root = Path(
            getattr(self.config, "project_state_dir", None)
            or getattr(self.memory, "root", None)
            or "."
        )
        objective_fingerprint = self._planner_waiting_objective_fingerprint()
        return root / f"manager-planner-feedback-{objective_fingerprint[:16]}.json"

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
                state_root=Path(self.memory.root),
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
            != self._planner_waiting_objective_fingerprint()
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

    def _backlog_planning_signature(self) -> str:
        """Digest of live backlog item ids and statuses.

        Feedback recorded because every proposed task duplicated existing
        backlog work stays true exactly as long as those items keep their
        status. Project files rewritten by live background jobs are not new
        planning evidence for that kind of feedback — judging it by the
        whole-tree signature made the feedback evaporate every cycle and the
        planner replan blind at the base backoff indefinitely.
        """
        rows = sorted(
            f"{item.id}:{item.status}" for item in self.memory.backlog.active()
        )
        digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
        return f"backlog:{digest}"

    def _manager_feedback_signature_for(self, diagnostic: str) -> str:
        if diagnostic == PLANNER_TASKS_FILTERED_DIAGNOSTIC:
            return self._backlog_planning_signature()
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
                "objective_fingerprint": self._planner_waiting_objective_fingerprint(),
                "stage": stage,
                "reason": reason,
                "diagnostic": diagnostic,
                "evidence_signature": evidence_signature,
                "attempts": attempts,
                "created_at": created_at,
                "updated_at": time.time(),
            }
        )

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

    @staticmethod
    def _waiting_contract_key(contract: Any) -> tuple[str, str]:
        return (
            str(getattr(contract, "blocker_fingerprint", "") or "").strip(),
            str(getattr(contract, "recheck_token", "") or "").strip(),
        )

    def _load_planner_waiting_contract_state(self) -> dict[str, Any] | None:
        path = self._planner_waiting_contract_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, TypeError, ValueError):
            log.warning("planner waiting contract is unreadable: %s", path, exc_info=True)
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != 1:
            return None
        if (
            str(payload.get("objective_fingerprint") or "")
            != self._planner_waiting_objective_fingerprint()
        ):
            return None
        runtime_revision = self._planner_waiting_runtime_revision()
        authored_revision = str(payload.get("runtime_revision") or "")
        if authored_revision != runtime_revision:
            if bool(payload.get("active")):
                try:
                    self._clear_planner_wait_control_binding(
                        payload,
                        reason="planner wait invalidated by runtime revision change",
                    )
                except (OSError, TypeError, ValueError):
                    log.warning(
                        "failed to clear stale planner wait control binding: %s",
                        path,
                        exc_info=True,
                    )
                payload["active"] = False
                payload["wake_reason"] = "runtime_revision_changed"
                payload["authored_runtime_revision"] = authored_revision
                payload["superseded_by_runtime_revision"] = runtime_revision
                payload["updated_at"] = time.time()
                self._write_planner_waiting_contract_state(payload)
                self._emit(
                    {
                        "type": EventType.LIFE_PLANNER_WAITING_WOKEN,
                        "blocker_fingerprint": payload.get("blocker_fingerprint"),
                        "recheck_token": payload.get("recheck_token"),
                        "wake_reason": "runtime_revision_changed",
                        "authored_runtime_revision": authored_revision,
                        "current_runtime_revision": runtime_revision,
                    }
                )
            return None
        if not str(payload.get("blocker_fingerprint") or "").strip():
            return None
        if not str(payload.get("recheck_token") or "").strip():
            return None
        if payload.get("state_revision") is not None:
            try:
                from ...manager.control_state import CampaignControlStore

                control = CampaignControlStore(
                    Path(self.memory.root),
                    project_root=self._project_workdir(),
                )
                if not control.is_wait_current(
                    campaign_epoch=int(payload.get("campaign_epoch") or 0),
                    state_revision=int(payload.get("state_revision") or 0),
                    wait_id=str(payload.get("wait_id") or ""),
                ):
                    return None
            except (OSError, TypeError, ValueError):
                log.warning(
                    "planner waiting contract revision is invalid: %s",
                    path,
                    exc_info=True,
                )
                return None
        return payload

    def _write_planner_waiting_contract_state(
        self,
        payload: dict[str, Any],
    ) -> bool:
        path = self._planner_waiting_contract_path()
        # The one Planner turn granted per wait belongs to the blocker, not to
        # the contract object: the suppression path rebuilds the contract each
        # cycle, and a flag stored on the instance would be reissued every time
        # -- which is the poll this short circuit exists to avoid. Carry it
        # across rewrites for as long as the same blocker is being waited on.
        try:
            if not payload.get("idle_capacity_turn_used") and path.is_file():
                previous = json.loads(path.read_text(encoding="utf-8"))
                same_blocker = str(previous.get("blocker_fingerprint") or "") == str(
                    payload.get("blocker_fingerprint") or ""
                )
                if same_blocker and previous.get("idle_capacity_turn_used"):
                    payload["idle_capacity_turn_used"] = True
                    if "idle_capacity_turn_ts" in previous:
                        payload["idle_capacity_turn_ts"] = previous[
                            "idle_capacity_turn_ts"
                        ]
                    if "idle_capacity_backlog_revision" in previous:
                        payload["idle_capacity_backlog_revision"] = previous[
                            "idle_capacity_backlog_revision"
                        ]
        except (OSError, ValueError):
            pass
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
            log.exception("failed to persist planner waiting contract: %s", path)
            return False
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _waiting_revision_file(path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError:
            return {"path": str(path), "exists": False}
        entry: dict[str, Any] = {
            "path": str(path),
            "exists": True,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
        if path.is_file() and stat.st_size <= 1_048_576:
            try:
                entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                entry["read_error"] = True
        return entry

    def _nothing_queued_behind_the_wait(self) -> bool:
        """True when no mission is waiting its turn behind the blocked one.

        The mission that owns the wait is itself ``running``, so the question is
        whether anything is queued after it. Nothing queued means the campaign's
        other mission slots will sit empty for as long as the external job runs.

        This reports a fact and concludes nothing from it: a campaign may well
        have nothing worth starting, and that judgement is the Planner's. It
        just cannot make it while a wait contract skips its turn. Fail-safe: an
        unreadable backlog keeps the existing skip rather than waking the
        Planner every cycle.
        """
        try:
            # A pending item the parallel worker cannot claim is not work
            # waiting its turn -- it is the fact Planner must be allowed to
            # see and replace. Counting it here left run-01 idling and
            # respawning every 34 minutes behind a path conflict.
            return self.memory.backlog.next_pending(parallel_only=True) is None
        except Exception:  # noqa: BLE001 - visibility must not break planning
            return False

    @staticmethod
    def _external_work_state_rows(project_root: Path) -> list[dict[str, str]]:
        """Registered background jobs as (work_id, run_id, state) rows.

        This is the wait-relevant view of the external-work registry: it moves
        when a job starts, completes, or fails, and stays put while a healthy
        job merely appends to its own logs. An unreadable registry contributes
        a stable empty view rather than churn.
        """
        try:
            from ...engineer.external_work import scan_external_work

            return [
                {
                    "work_id": status.work_id,
                    "run_id": status.run_id,
                    "state": status.state.value,
                }
                for status in scan_external_work(project_root)
                if status.source == "subagent"
            ]
        except Exception:  # noqa: BLE001 - wait evaluation must stay stable
            log.debug("external-work registry scan failed", exc_info=True)
            return []

    def _planner_waiting_observed_revision(
        self,
        *,
        wake_on: list[str] | tuple[str, ...],
        watched_paths: list[str] | tuple[str, ...],
    ) -> str:
        wake_sources = {str(value).strip().lower() for value in wake_on}
        project_root = self._project_workdir()
        revision: dict[str, Any] = {
            "campaign_epoch": self._planner_waiting_objective_fingerprint(),
            "wake_on": sorted(wake_sources),
        }
        if "authorization" in wake_sources:
            # An authorization wait watched only the Manager control-state log,
            # which nothing writes unless the operator drives that API -- in
            # three live campaigns the file did not exist at all. Answering the
            # documented way, `argus --notify` into inbox.jsonl, changed
            # nothing, so run-04 sat on wake_on ["authorization"] for fifteen
            # hours after being answered. Four campaigns then spent eleven
            # missions trying to canonicalize wake sources by hand.
            # The operator acted either way; both records count.
            root = Path(self.memory.root)
            revision["authorization"] = [
                self._waiting_revision_file(root / "operator-authorizations.jsonl"),
                self._waiting_revision_file(root / "inbox.jsonl"),
            ]
        if "manager_stage" in wake_sources:
            from ...core.pipeline_state import pipeline_state_path

            revision["manager_stage"] = self._waiting_revision_file(
                pipeline_state_path(project_root)
            )
        if "artifact_revision" in wake_sources:
            artifacts: list[dict[str, Any]] = []
            registry_paths = False
            for relative in watched_paths:
                rel = str(relative).strip().lstrip("/")
                parts = Path(rel).parts
                if parts and parts[0] == ".argus_subagents":
                    # A watched path inside the external-work registry points
                    # at a job's own bookkeeping (logs, heartbeats), which is
                    # rewritten for as long as the job runs. Stat-digesting it
                    # woke the planner every cycle of a live job; what the
                    # contract actually waits for is the job's registered
                    # state, which moves exactly at real transitions.
                    registry_paths = True
                    continue
                artifacts.append(self._waiting_revision_file(project_root / rel))
            revision["artifacts"] = artifacts
            if registry_paths and "subagent_state" not in wake_sources:
                revision["registry_jobs"] = self._external_work_state_rows(
                    project_root
                )
        if "subagent_terminal" in wake_sources:
            terminal_rows: list[dict[str, str]] = []
            registry = project_root / ".argus_subagents"
            for path in sorted(registry.glob("*.json")) if registry.is_dir() else []:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                state = str(payload.get("status") or payload.get("state") or "").strip().lower()
                if state not in {
                    "completed",
                    "complete",
                    "done",
                    "failed",
                    "error",
                    "cancelled",
                    "canceled",
                    "stopped",
                    "early_stop",
                }:
                    continue
                terminal_rows.append(
                    {
                        "task_id": str(payload.get("task_id") or path.stem),
                        "state": state,
                        "decision": str(payload.get("decision") or ""),
                    }
                )
            revision["subagent_terminal"] = terminal_rows
        if "subagent_state" in wake_sources:
            revision["subagent_state"] = self._external_work_state_rows(
                project_root
            )
        blob = json.dumps(revision, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _planner_event_wait_outcome(self) -> str:
        state = self._load_planner_waiting_contract_state()
        if state is None or not bool(state.get("active")) or state.get("wait_mode") != "event":
            return ""
        if isinstance(state.get("manager_resolution"), dict):
            return ""
        expires_at = float(state.get("expires_at") or 0.0)
        current_revision = self._planner_waiting_observed_revision(
            wake_on=list(state.get("wake_on") or []),
            watched_paths=list(state.get("watched_paths") or []),
        )
        observed_revision = str(state.get("observed_revision") or "")
        wake_reason = ""
        if expires_at > 0 and time.time() >= expires_at:
            wake_reason = "expired"
        elif observed_revision and current_revision != observed_revision:
            wake_reason = "revision_changed"
        if wake_reason:
            state["active"] = False
            state["superseded_by_revision"] = current_revision
            state["wake_reason"] = wake_reason
            state["updated_at"] = time.time()
            self._write_planner_waiting_contract_state(state)
            self._emit(
                {
                    "type": EventType.LIFE_PLANNER_WAITING_WOKEN,
                    "blocker_fingerprint": state.get("blocker_fingerprint"),
                    "recheck_token": state.get("recheck_token"),
                    "wake_reason": wake_reason,
                    "observed_revision": observed_revision,
                    "current_revision": current_revision,
                }
            )
            self._reset_idle_backoff()
            return ""
        if not observed_revision:
            state["observed_revision"] = current_revision
            state["updated_at"] = time.time()
            self._write_planner_waiting_contract_state(state)

        # A wait contract silences the Planner until the watched revision moves
        # or the contract expires. On a multi-hour GPU job that is hours of not
        # being asked anything, and campaigns run fewer missions than they have
        # slots, so the rest of the campaign idles for exactly as long.
        #
        # The Planner was asked once, when the contract was created, and that
        # turn is where it proposed only a status probe. The suppression reply
        # telling it that independent work is still schedulable therefore
        # arrives with no turn left to act on. Grant exactly one more turn per
        # contract, and only while nothing is queued behind the wait. One turn,
        # not one per cycle: waking it repeatedly is the token-burning poll this
        # short circuit exists to prevent.
        # One turn is the right budget for a wait that ends by itself. A wait
        # that ends only when the operator acts does not end at all overnight:
        # run-04 spent fifteen hours on wake_on ["authorization"] with
        # expires_at 0, having used its single turn in the first minute, while
        # its paper sat finished-looking at 8,107 words with four of its
        # thirty-one figures used. run-05 parked the same way on an
        # authentication decision. So for those, and only those, the turn is
        # re-granted on OPERATOR_WAIT_TURN_REGRANT_SECONDS. That timer is only
        # the backstop behind the three wake paths that already exist -- the
        # first per-contract grant, a backlog revision change, and the
        # authorization event itself -- and each re-grant is a full Planner LLM
        # call, so its cadence is an LLM-call-rate policy, deliberately not
        # tied to the idle sleep cap.
        if self._planner_turn_available_during_wait(state):
            state["idle_capacity_turn_used"] = True
            state["idle_capacity_turn_ts"] = time.time()
            state["idle_capacity_backlog_revision"] = (
                self._waiting_backlog_revision()
            )
            state["updated_at"] = time.time()
            self._write_planner_waiting_contract_state(state)
            return ""

        # Event waits otherwise bypass the Planner entirely. Still feed each
        # unchanged idle cycle through the open-ended Manager reconciliation
        # cadence; otherwise this short circuit prevents the counter from ever
        # reaching its liveness threshold and a stale wait can persist forever.
        from ...planner import PlannerVerdict, WaitingContract

        contract = WaitingContract(
            blocker_fingerprint=str(state.get("blocker_fingerprint") or ""),
            recheck_condition=str(state.get("recheck_condition") or ""),
            recheck_token=str(state.get("recheck_token") or ""),
            allow_verification_probe=bool(state.get("allow_verification_probe", False)),
            recheck_after_seconds=int(state.get("recheck_after_seconds") or 0),
            stage_reconciliation_required=bool(state.get("stage_reconciliation_required", False)),
            wait_mode="event",
            wake_on=tuple(str(value) for value in state.get("wake_on") or []),
            watched_paths=tuple(str(value) for value in state.get("watched_paths") or []),
            expires_at=expires_at,
            operator_action_required=bool(state.get("operator_action_required", False)),
        )
        verdict = PlannerVerdict(
            project_done=False,
            reason=contract.recheck_condition,
            waiting=True,
            waiting_reason=contract.recheck_condition,
            waiting_contract=contract,
        )
        if self._reconcile_open_ended_planner_waiting(verdict):
            return PLAN_RETRY

        sleep_s = self._enter_pause_backoff()
        self._emit(
            {
                "type": EventType.LIFE_PLANNER_WAITING,
                "cycle": self._planning_cycles,
                "reason": state.get("recheck_condition") or "awaiting event",
                "suggested_sleep_s": sleep_s,
                "model_call_skipped": True,
                "wait_mode": "event",
                "observed_revision": current_revision,
                "waiting_contract": self._waiting_contract_event_payload(state, None),
            }
        )
        self._emit_status("awaiting declared event; Planner call skipped")
        return PLAN_AWAITING

    def _waiting_backlog_revision(self) -> str:
        """A cheap stable revision for facts that can change scheduling."""
        try:
            path = Path(self.memory.root) / "backlog.jsonl"
            stat = path.stat()
            return f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            return ""

    def _planner_turn_available_during_wait(self, state: dict) -> bool:
        """Is the Planner owed a turn while this wait contract holds?

        Never while other work is queued behind the wait -- the campaign is
        already busy and waking the Planner would only burn tokens. Otherwise
        once per contract, except for a wait that only the operator can end,
        where "once" means never again and the campaign is simply over.
        """
        if not self._nothing_queued_behind_the_wait():
            return False
        if not state.get("idle_capacity_turn_used"):
            return True
        # New backlog state is new evidence, not another poll of the same
        # blocker. Re-grant one turn so the Planner can route around a task that
        # arrived after the original opportunity or became unclaimable.
        if (
            "idle_capacity_backlog_revision" in state
            and state.get("idle_capacity_backlog_revision")
            != self._waiting_backlog_revision()
        ):
            return True
        if not state.get("operator_action_required"):
            return False
        granted = float(state.get("idle_capacity_turn_ts") or 0.0)
        return time.time() - granted >= OPERATOR_WAIT_TURN_REGRANT_SECONDS

    def _confined_planner_wait_paths(self, values: list[str]) -> list[str]:
        """Validate watched paths before they can influence revision reads."""
        if not values:
            return []
        root = self._project_workdir().expanduser().resolve(strict=False)
        confined: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw or "").strip().replace("\\", "/")
            path = Path(value)
            if (
                not value
                or "\x00" in value
                or path.is_absolute()
                or ".." in path.parts
                or value in {".", "./"}
            ):
                raise ValueError(f"watched path must be a project child: {value!r}")
            normalized = Path(*path.parts).as_posix()
            candidate = (root / normalized).resolve(strict=False)
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"watched path escapes the project root: {value!r}"
                ) from exc
            if normalized not in seen:
                seen.add(normalized)
                confined.append(normalized)
        return confined

    def _persist_planner_waiting_contract(
        self,
        contract: Any,
    ) -> dict[str, Any] | None:
        blocker_fingerprint, recheck_token = self._waiting_contract_key(contract)
        recheck_condition = str(getattr(contract, "recheck_condition", "") or "").strip()
        if not blocker_fingerprint or not recheck_token or not recheck_condition:
            return None
        previous = self._load_planner_waiting_contract_state() or {}
        same_condition = (
            previous.get("blocker_fingerprint") == blocker_fingerprint
            and previous.get("recheck_token") == recheck_token
        )
        now = time.time()
        raw_wait_mode = str(
            getattr(contract, "wait_mode", "poll") or "poll"
        ).strip().casefold()
        normalized_wake_on, unknown_wake_on, wake_changed = normalize_wake_sources(
            getattr(contract, "wake_on", ())
        )
        wake_on = list(normalized_wake_on)
        normalization_reasons: list[str] = []
        if raw_wait_mode not in {"event", "poll"}:
            normalization_reasons.append(
                f"unknown wait_mode {raw_wait_mode!r} normalized from context"
            )
        if wake_changed:
            normalization_reasons.append("wake sources normalized")
        if unknown_wake_on:
            normalization_reasons.append(
                "unsupported wake hints ignored: " + ", ".join(unknown_wake_on)
            )
        try:
            watched_paths = self._confined_planner_wait_paths(
                [str(value) for value in getattr(contract, "watched_paths", ())]
            )
        except ValueError as exc:
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "error": "planner wait has unsafe watched path",
                "detail": str(exc),
                "blocker_fingerprint": blocker_fingerprint,
                "recheck_token": recheck_token,
            })
            return None
        # operator_action_required means only fresh operator input can change
        # this blocker, so the source it wakes on is not the Planner's to pick.
        # run-05 declared operator waits against subagent_state and had
        # nineteen contracts rejected for lacking a revision only the host can
        # compute, then invented operator_answer and operator_message and lost
        # sixteen more. Reading the authority it already declared costs nothing
        # and cannot be got wrong.
        operator_action_required = bool(
            getattr(contract, "operator_action_required", False)
        )
        context_requires_event = False
        if operator_action_required:
            wake_on = ["authorization"]
            context_requires_event = True
            if normalized_wake_on != ("authorization",) or raw_wait_mode != "event":
                normalization_reasons.append(
                    "operator wait bound to authorization events"
                )
        elif watched_paths:
            if "artifact_revision" not in wake_on:
                wake_on.append("artifact_revision")
                normalization_reasons.append(
                    "artifact_revision derived from watched paths"
                )
            context_requires_event = True

        source_wait_id = str(getattr(contract, "wait_id", "") or "").strip()
        resolved_wait = None
        wait_id_source_unknown = False
        if source_wait_id:
            try:
                from ...engineer.external_work import inspect_external_work

                resolved_wait = inspect_external_work(
                    self._project_workdir(), source_wait_id
                )
            except Exception:  # noqa: BLE001 - registry discovery is fail-soft
                log.warning("failed to resolve planner wait registry id", exc_info=True)
            if resolved_wait is not None and resolved_wait.source == "subagent":
                if "subagent_state" not in wake_on:
                    wake_on.append("subagent_state")
                context_requires_event = True
                normalization_reasons.append(
                    "subagent_state derived from resolved wait_id"
                )
            else:
                wait_id_source_unknown = True
                normalization_reasons.append(
                    "wait_id did not resolve to a Host-observed subagent"
                )

        contract_observed_revision = str(
            getattr(contract, "observed_revision", "") or ""
        )

        if "artifact_revision" in wake_on and not watched_paths:
            wake_on = [source for source in wake_on if source != "artifact_revision"]
            normalization_reasons.append(
                "artifact_revision hint ignored without confined watched paths"
            )
        if (
            {"subagent_state", "subagent_terminal"}.intersection(wake_on)
            and not contract_observed_revision
            and not (resolved_wait is not None and resolved_wait.source == "subagent")
        ):
            wake_on = [
                source
                for source in wake_on
                if source not in {"subagent_state", "subagent_terminal"}
            ]
            normalization_reasons.append(
                "subagent wake hint ignored without a Host-observed revision"
            )

        if context_requires_event:
            wait_mode = "event"
        elif raw_wait_mode in {"event", "poll"}:
            wait_mode = raw_wait_mode
        else:
            wait_mode = "event" if wake_on else "poll"
            normalization_reasons.append(f"wait_mode selected as {wait_mode}")

        degraded = False
        if wait_mode == "event" and not wake_on:
            wait_mode = "poll"
            degraded = True
            normalization_reasons.append(
                "event wait degraded to bounded poll without an observable source"
            )
        elif (
            wait_mode == "poll"
            and (unknown_wake_on or wait_id_source_unknown)
            and not wake_on
        ):
            degraded = True
            normalization_reasons.append(
                "unobservable source degraded to bounded poll"
            )
        current_observed_revision = self._planner_waiting_observed_revision(
            wake_on=wake_on,
            watched_paths=watched_paths,
        )
        if (
            wait_mode == "event"
            and {"subagent_state", "subagent_terminal"}.intersection(wake_on)
            and resolved_wait is not None
            and resolved_wait.source == "subagent"
        ):
            contract_observed_revision = current_observed_revision
        if (
            contract_observed_revision
            and current_observed_revision != contract_observed_revision
        ):
            self._emit(
                {
                    "type": EventType.LIFE_PLANNER_WAITING_WOKEN,
                    "blocker_fingerprint": blocker_fingerprint,
                    "recheck_token": recheck_token,
                    "wake_reason": "revision_changed_before_persist",
                    "observed_revision": contract_observed_revision,
                    "current_revision": current_observed_revision,
                }
            )
            return None
        if normalization_reasons:
            self._emit({
                "type": "life.planner.waiting_contract.normalized",
                "blocker_fingerprint": blocker_fingerprint,
                "recheck_token": recheck_token,
                "reasons": normalization_reasons,
                "degraded": degraded,
                "wait_mode": wait_mode,
                "wake_on": wake_on,
            })

        durable_wait_id = hashlib.sha256(
            (
                self._planner_waiting_objective_fingerprint()
                + "\0"
                + blocker_fingerprint
                + "\0"
                + recheck_token
            ).encode("utf-8")
        ).hexdigest()[:24]
        control_binding: dict[str, Any] = {}
        try:
            from ...manager.control_state import CampaignControlStore

            control = CampaignControlStore(
                Path(self.memory.root),
                project_root=self._project_workdir(),
            )
            identity = control.campaign_identity(
                objective=str(getattr(self.config, "continuous_objective", "") or "")
            )
            control_head = control.activate_wait(
                identity=identity,
                wait_id=durable_wait_id,
                blocker_fingerprint=blocker_fingerprint,
                recheck_token=recheck_token,
                watched_paths=watched_paths,
            )
            control_binding = {
                "campaign_id": control_head.campaign_id,
                "campaign_epoch": control_head.campaign_epoch,
                "state_revision": control_head.state_revision,
            }
        except (OSError, TypeError, ValueError):
            log.warning(
                "failed to bind planner wait to Manager control revision",
                exc_info=True,
            )
        payload = {
            "version": 1,
            "objective_fingerprint": self._planner_waiting_objective_fingerprint(),
            "runtime_revision": self._planner_waiting_runtime_revision(),
            "blocker_fingerprint": blocker_fingerprint,
            "recheck_condition": recheck_condition,
            "recheck_token": recheck_token,
            "stage_reconciliation_required": bool(
                getattr(contract, "stage_reconciliation_required", False)
            ),
            "operator_action_required": operator_action_required,
            "allow_verification_probe": bool(getattr(contract, "allow_verification_probe", False)),
            "recheck_after_seconds": max(
                0,
                min(
                    604800,
                    max(
                        _DEGRADED_WAIT_POLL_SECONDS if degraded else 0,
                        int(getattr(contract, "recheck_after_seconds", 0) or 0),
                    ),
                ),
            ),
            "wait_mode": wait_mode,
            "wake_on": wake_on,
            "watched_paths": watched_paths,
            "expires_at": max(
                0.0,
                float(getattr(contract, "expires_at", 0.0) or 0.0),
            ),
            **control_binding,
            "wait_id": durable_wait_id,
            "source_wait_id": source_wait_id,
            "observed_revision": (
                contract_observed_revision or current_observed_revision
            ),
            "first_observed_at": (
                float(previous.get("first_observed_at") or now) if same_condition else now
            ),
            "updated_at": now,
            "last_probe_fingerprint": str(previous.get("last_probe_fingerprint") or ""),
            "last_probe_token": str(previous.get("last_probe_token") or ""),
            "last_probe_at": float(previous.get("last_probe_at") or 0.0),
            "probed_conditions": [
                entry
                for entry in (previous.get("probed_conditions") or [])
                if isinstance(entry, dict)
            ],
            "pending_probe": (
                previous.get("pending_probe")
                if isinstance(previous.get("pending_probe"), dict)
                else None
            ),
            "manager_resolution": (
                previous.get("manager_resolution")
                if same_condition and isinstance(previous.get("manager_resolution"), dict)
                else None
            ),
            "resolution_retry_count": (
                int(previous.get("resolution_retry_count") or 0) if same_condition else 0
            ),
            "active": True,
        }
        if not self._write_planner_waiting_contract_state(payload):
            return None
        return payload

    def _reserve_planner_waiting_contract_probe(
        self,
        contract: Any,
        *,
        item_id: str,
    ) -> bool:
        state = self._load_planner_waiting_contract_state()
        if state is None:
            return False
        blocker_fingerprint, recheck_token = self._waiting_contract_key(contract)
        if (
            state.get("blocker_fingerprint") != blocker_fingerprint
            or state.get("recheck_token") != recheck_token
        ):
            return False
        state["pending_probe"] = {
            "item_id": item_id,
            "blocker_fingerprint": blocker_fingerprint,
            "recheck_token": recheck_token,
            "reserved_at": time.time(),
        }
        state["updated_at"] = state["pending_probe"]["reserved_at"]
        return self._write_planner_waiting_contract_state(state)

    @staticmethod
    def _append_probed_condition(
        state: dict[str, Any],
        *,
        blocker_fingerprint: str,
        recheck_token: str,
        probed_at: float,
    ) -> None:
        state["last_probe_fingerprint"] = blocker_fingerprint
        state["last_probe_token"] = recheck_token
        state["last_probe_at"] = probed_at
        state["updated_at"] = state["last_probe_at"]
        probed_conditions = [
            entry for entry in (state.get("probed_conditions") or []) if isinstance(entry, dict)
        ]
        if not any(
            entry.get("blocker_fingerprint") == blocker_fingerprint
            and entry.get("recheck_token") == recheck_token
            for entry in probed_conditions
        ):
            probed_conditions.append(
                {
                    "blocker_fingerprint": blocker_fingerprint,
                    "recheck_token": recheck_token,
                    "probed_at": probed_at,
                }
            )
        state["probed_conditions"] = probed_conditions

    def _finalize_planner_waiting_contract_probe(
        self,
        contract: Any,
        *,
        item_id: str,
    ) -> bool:
        state = self._load_planner_waiting_contract_state()
        if state is None:
            return False
        blocker_fingerprint, recheck_token = self._waiting_contract_key(contract)
        pending = state.get("pending_probe")
        if not isinstance(pending, dict) or pending.get("item_id") != item_id:
            return False
        if (
            pending.get("blocker_fingerprint") != blocker_fingerprint
            or pending.get("recheck_token") != recheck_token
        ):
            return False
        self._append_probed_condition(
            state,
            blocker_fingerprint=blocker_fingerprint,
            recheck_token=recheck_token,
            probed_at=time.time(),
        )
        state["pending_probe"] = None
        return self._write_planner_waiting_contract_state(state)

    def _reconcile_planner_waiting_contract_probe(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        pending = state.get("pending_probe")
        if not isinstance(pending, dict):
            return state
        item_id = str(pending.get("item_id") or "")
        blocker_fingerprint = str(pending.get("blocker_fingerprint") or "")
        recheck_token = str(pending.get("recheck_token") or "")
        try:
            item_exists = any(
                getattr(item, "id", "") == item_id for item in self.memory.backlog.history()
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to reconcile pending planner verification probe")
            return None
        if item_exists:
            self._append_probed_condition(
                state,
                blocker_fingerprint=blocker_fingerprint,
                recheck_token=recheck_token,
                probed_at=float(pending.get("reserved_at") or time.time()),
            )
        state["pending_probe"] = None
        state["updated_at"] = time.time()
        if not self._write_planner_waiting_contract_state(state):
            return None
        return state

    def _deactivate_planner_waiting_contract(self) -> None:
        state = self._load_planner_waiting_contract_state()
        if state is None or not bool(state.get("active")):
            return
        state["active"] = False
        state["updated_at"] = time.time()
        self._write_planner_waiting_contract_state(state)

    def _resolve_planner_waiting_contract(
        self,
        *,
        manager_reason: str,
        target_stage: str,
    ) -> None:
        """Persist an authoritative Manager resolution for the next Planner.

        Deactivating the stale contract alone is insufficient: Manager stage
        decisions are event-sourced but are not part of the Planner journal
        rendered in the immediate retry. Persist the exact ruling beside the
        objective-scoped waiting contract so the next fresh Planner session sees
        the new authority without mutating the operator objective or project
        evidence.
        """
        state = self._load_planner_waiting_contract_state()
        if state is None:
            return
        now = time.time()
        state["active"] = False
        state["manager_resolution"] = {
            "reason": str(manager_reason or "").strip(),
            "target_stage": str(target_stage or "").strip(),
            "resolved_at": now,
            "blocker_fingerprint": str(state.get("blocker_fingerprint") or ""),
            "recheck_condition": str(state.get("recheck_condition") or ""),
        }
        state["resolution_retry_count"] = 0
        state["updated_at"] = now
        self._write_planner_waiting_contract_state(state)

    def _planner_wait_resolution_runtime_note(self) -> str:
        state = self._load_planner_waiting_contract_state()
        if state is None:
            return ""
        resolution = state.get("manager_resolution")
        if not isinstance(resolution, dict):
            return ""
        reason = str(resolution.get("reason") or "").strip()
        if not reason:
            return ""
        return (
            "AUTHORITATIVE MANAGER WAIT RESOLUTION (current objective):\n"
            f"- stage remains: {resolution.get('target_stage') or '(unchanged)'}\n"
            "- prior recheck condition: "
            f"{resolution.get('recheck_condition') or ''}\n"
            f"- Manager directive: {reason}\n"
            "The Manager set `resolves_wait=true`; do not claim this Manager "
            "authorization/directive is absent. Plan the smallest lawful work "
            "within it, or identify a materially different blocker."
        )

    def _clear_planner_wait_resolution(self) -> None:
        state = self._load_planner_waiting_contract_state()
        if state is None or not isinstance(state.get("manager_resolution"), dict):
            return
        state["manager_resolution"] = None
        state["resolution_retry_count"] = 0
        state["updated_at"] = time.time()
        self._write_planner_waiting_contract_state(state)

    @staticmethod
    def _waiting_contract_event_payload(
        state: dict[str, Any] | None,
        contract: Any,
    ) -> dict[str, Any] | None:
        if contract is None:
            return None
        source = state or {
            "blocker_fingerprint": getattr(contract, "blocker_fingerprint", ""),
            "recheck_condition": getattr(contract, "recheck_condition", ""),
            "recheck_token": getattr(contract, "recheck_token", ""),
            "stage_reconciliation_required": getattr(
                contract,
                "stage_reconciliation_required",
                False,
            ),
            "operator_action_required": getattr(
                contract,
                "operator_action_required",
                False,
            ),
            "allow_verification_probe": getattr(
                contract,
                "allow_verification_probe",
                False,
            ),
            "recheck_after_seconds": getattr(
                contract,
                "recheck_after_seconds",
                0,
            ),
            "wait_mode": getattr(contract, "wait_mode", "poll"),
            "wake_on": list(getattr(contract, "wake_on", ()) or ()),
            "watched_paths": list(getattr(contract, "watched_paths", ()) or ()),
            "expires_at": getattr(contract, "expires_at", 0.0),
        }
        return {
            key: source.get(key)
            for key in (
                "blocker_fingerprint",
                "recheck_condition",
                "recheck_token",
                "runtime_revision",
                "stage_reconciliation_required",
                "operator_action_required",
                "allow_verification_probe",
                "recheck_after_seconds",
                "wait_mode",
                "wake_on",
                "watched_paths",
                "expires_at",
                "campaign_epoch",
                "campaign_id",
                "state_revision",
                "wait_id",
                "observed_revision",
                "superseded_by_revision",
                "superseded_by_runtime_revision",
                "wake_reason",
                "first_observed_at",
                "last_probe_at",
            )
            if key in source
        }

    def _planner_waiting_contract_runtime_note(self) -> str:
        state = self._load_planner_waiting_contract_state()
        if state is None or not bool(state.get("active")):
            return ""
        return (
            "PERSISTED PLANNER WAITING CONTRACT (authored by your prior verdict):\n"
            f"- recheck_token: {state['recheck_token']}\n"
            f"- recheck_condition: {state.get('recheck_condition') or ''}\n"
            f"- wait_mode: {state.get('wait_mode') or 'poll'}\n"
            f"- wake_on: {state.get('wake_on') or []}\n"
            "- stage_reconciliation_required: "
            f"{bool(state.get('stage_reconciliation_required'))}\n"
            "- operator_action_required: "
            f"{bool(state.get('operator_action_required'))}\n"
            f"- last_probe_at: {state.get('last_probe_at') or 0}\n"
            "If current evidence does not satisfy the declared recheck condition, "
            "reuse the same blocker semantics and token with waiting=true and do not "
            "queue an equivalent polling task. Change the token only when concrete "
            "current evidence changes; the harness does not infer that change. "
            "While the named wait is in progress, is there a concrete uncertainty "
            "whose answer could change the route and can be resolved without the "
            "awaited result? If yes, schedule that information-gaining work; otherwise "
            "wait."
        )

    def _maybe_dispatch_verification_probe(self, verdict: Any) -> bool:
        """Stall-breaker: after K consecutive idle cycles on the same external
        dependency, enqueue ONE domain-agnostic verification-probe mission so the
        agent TESTS its (possibly stale) belief against CURRENT reality.

        Returns True iff a probe was enqueued (caller runs it on the next tick).
        This does NOT judge the environment or override the planner's research
        judgment — it forces the agent to gather first-hand evidence so reality,
        not a memory of the blocker, drives the next decision.
        """
        n = int(getattr(self, "_consecutive_idle_planner_cycles", 0))
        if n < VERIFICATION_PROBE_AFTER_IDLE_CYCLES:
            return False
        contract = getattr(verdict, "waiting_contract", None)
        contract_state = None
        if contract is not None:
            contract_state = self._load_planner_waiting_contract_state()
            if contract_state is None:
                return False
            contract_state = self._reconcile_planner_waiting_contract_probe(contract_state)
            if contract_state is None:
                return False
            blocker_fingerprint, recheck_token = self._waiting_contract_key(contract)
            if (
                contract_state.get("blocker_fingerprint") != blocker_fingerprint
                or contract_state.get("recheck_token") != recheck_token
                or not bool(contract_state.get("allow_verification_probe"))
            ):
                return False
            if time.time() < float(contract_state.get("first_observed_at") or 0.0) + float(
                contract_state.get("recheck_after_seconds") or 0.0
            ):
                return False
            if any(
                isinstance(entry, dict)
                and entry.get("blocker_fingerprint") == blocker_fingerprint
                and entry.get("recheck_token") == recheck_token
                for entry in (contract_state.get("probed_conditions") or [])
            ):
                return False
        now = time.monotonic()
        if (now - getattr(self, "_last_verification_probe_at", 0.0)) < (
            VERIFICATION_PROBE_COOLDOWN_SECONDS
        ):
            return False
        # Never stack a second probe while one is still pending/running.
        try:
            for it in self.memory.backlog.active():
                if "verification_probe" in (getattr(it, "tags", []) or []) and getattr(
                    it, "status", ""
                ) in ("pending", "running"):
                    return False
        except Exception:  # noqa: BLE001
            log.exception("verification-probe dedup scan failed; skipping probe")
            return False
        reason = (
            getattr(verdict, "waiting_reason", "")
            or getattr(verdict, "reason", "")
            or "an external dependency"
        )
        item = BacklogItem.new(
            title="verification probe: re-test the recorded external blocker",
            objective=(
                "Verification-probe mission, dispatched by the harness after the "
                f"planner idled {n} consecutive cycles concluding it was blocked. "
                "Do NOT trust the journal's record of the blocker as still current. "
                f'The recorded blocker was: "{reason}". RIGHT NOW, actually attempt '
                "the blocked action — or run the single cheapest decisive probe of "
                "it — and report the REAL present outcome with concrete first-hand "
                "evidence (command output, file existence, an actual score/metric). "
                "State plainly whether it is STILL blocked or has CLEARED. If it has "
                "cleared, perform or unblock the smallest concrete next step. This is "
                "a perception check, not make-work: completion is judged solely by "
                "whether you produced fresh first-hand evidence of the blocker's "
                "current state."
            ),
            priority=50,
            tags=["planner", "scope:bounded", "life", "verification_probe"],
            iterate=True,
            iteration_max_cycles=1,
        )
        try:
            contract_state_before_probe = None
            if contract is not None:
                contract_state_before_probe = json.loads(json.dumps(contract_state))
                if not self._reserve_planner_waiting_contract_probe(
                    contract,
                    item_id=item.id,
                ):
                    log.error(
                        "verification probe was not enqueued because its waiting "
                        "contract could not be reserved"
                    )
                    return False
            self.memory.backlog.add(item)
        except Exception:  # noqa: BLE001
            if contract_state_before_probe is not None:
                self._write_planner_waiting_contract_state(contract_state_before_probe)
            log.exception("failed to enqueue verification probe; continuing")
            return False
        self._last_verification_probe_at = now
        if contract is not None:
            if not self._finalize_planner_waiting_contract_probe(
                contract,
                item_id=item.id,
            ):
                log.warning(
                    "verification probe is durable but its contract reservation "
                    "will require restart reconciliation"
                )
            contract_state = self._load_planner_waiting_contract_state()
        # Reset the idle counter so we don't immediately re-escalate before the
        # probe's real result lands in the event timeline (a real mission run
        # also resets it via _reset_idle_backoff()).
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0
        self._emit(
            {
                "type": EventType.LIFE_PLANNER_VERIFICATION_PROBE,
                "cycle": self._planning_cycles,
                "reason": reason,
                "idle_cycles": n,
                "waiting_contract": self._waiting_contract_event_payload(
                    contract_state,
                    contract,
                ),
            }
        )
        return True

    def _update_no_progress_streak(self, *, kind: str, report: Any) -> None:
        """Track consecutive 'completed but no forward progress' missions and,
        once the reviewer-judged streak crosses a threshold, emit an operator
        attention event (NOT a mission, NOT a verdict).

        Domain-agnostic by construction: it counts ONLY the L2 reviewer's own
        ``forward_progress`` boolean (agent judgment). The harness never decides
        what progress is — it only refuses to let the agent system do hollow work
        forever without surfacing the stall to its human operator. So a project
        that keeps completing no-score / blocked-archive refuges cannot loop
        invisibly: after N such missions the operator is pinged.
        """
        if kind != "mission_complete":
            return
        fp = report.get("forward_progress") if isinstance(report, dict) else None
        if fp is True:
            self._consecutive_no_progress_missions = 0
            return
        if fp is not False:
            return  # unknown / not reported — do not punish missing data
        n = int(getattr(self, "_consecutive_no_progress_missions", 0)) + 1
        self._consecutive_no_progress_missions = n
        if n < STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS:
            return
        # Threshold crossed: surface to the operator, then reset so the alert
        # re-fires after another N (not on every subsequent mission).
        self._consecutive_no_progress_missions = 0
        self._emit(
            {
                "type": EventType.LIFE_PLANNER_STALL_ESCALATION,
                "consecutive_no_progress_missions": n,
                "objective": (self.config.continuous_objective or "")[:200],
            }
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
            skipped_recent_failure_tasks=0,
            skipped_subagent_family_failure_tasks=0,
            enqueued_titles=[item.title],
            enqueued_impact_scores=[task.impact_score],
            skipped_duplicate_titles=[],
            skipped_recent_failure_titles=[],
            skipped_subagent_family_failure_titles=[],
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )
        return True if delivered else "planner_retry"


__all__ = ["PlanningContextMixin"]
