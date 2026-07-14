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
from ..memory import BacklogItem
from ._constants import (
    FULL_PAPER_GATE_DESCRIPTION,
    PLAN_AWAITING,
    PLANNER_SCOPE_BOUNDED,
    PLANNER_SCOPE_FINAL_SUBMISSION,
    STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS,
    VERIFICATION_PROBE_AFTER_IDLE_CYCLES,
    VERIFICATION_PROBE_COOLDOWN_SECONDS,
)
from ._helpers import (
    _legacy_final_submission_marker,
    _operator_only_external_blocker_wait_reason_for_project,
)

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
        if (
            scope == PLANNER_SCOPE_FINAL_SUBMISSION
            and not self._effective_full_paper_gate(self._artifact_root())
        ):
            # ``final_submission`` is a paper-only transport scope. A Planner
            # may still choose it for another vertical's terminal review task,
            # but persisting that tag makes ``tick()`` retire the task as stale
            # and re-plan it forever. Normalize at the enqueue boundary; the
            # old skip path remains as migration support for persisted rows.
            scope = PLANNER_SCOPE_BOUNDED
        return ["planner", f"scope:{scope}"]

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

    @classmethod
    def _item_is_final_submission(cls, item: BacklogItem) -> bool:
        """True when a backlog item is a project-final ``final_submission``
        task. Prefers the structured ``scope:final_submission`` tag; falls back
        to the legacy objective-prose marker only for items persisted before
        scope tagging existed (resumed-daemon compatibility)."""
        if cls._planner_scope_from_item(item) == PLANNER_SCOPE_FINAL_SUBMISSION:
            return True
        return _legacy_final_submission_marker(getattr(item, "objective", "") or "")

    def _render_backlog_item_metadata(self, item: BacklogItem) -> str:
        scope = self._planner_scope_from_item(item)
        if not scope and not item.tags:
            return ""
        is_paper_long_horizon = self.config.paper_mission
        lines = ["## Backlog item metadata"]
        if scope:
            lines.append(f"- planner_scope: {scope}")
        if item.tags:
            lines.append("- tags: " + ", ".join(item.tags))
        if scope == PLANNER_SCOPE_FINAL_SUBMISSION:
            lines.append(
                f"- final_submission_gate: {FULL_PAPER_GATE_DESCRIPTION} must be "
                "fully satisfied (every checklist item certified by the reviewer "
                "with concrete evidence) before this item can be marked done."
            )
        elif scope == PLANNER_SCOPE_BOUNDED:
            if is_paper_long_horizon:
                lines.append(
                    "- paper_optimization_task: this is a bounded mission, but it is "
                    "part of a long-horizon paper/submission objective. First satisfy "
                    "the named acceptance criteria, then continue through adjacent "
                    "paper blockers while budget allows; do not mark done only because "
                    "one narrow check passed if the relevant stage checklist items "
                    "(manuscript, evidence, review, layout, figure/table, citation, "
                    "manifest, or assurance) are still unmet. Full-pipeline "
                    "certification is required only for `final_submission`, but fresh "
                    "concrete evidence for the items you touched is required here."
                )
            else:
                lines.append(
                    "- bounded_task: judge this item against its own acceptance criteria; "
                    "do not require the project-final EMNLP gate unless the objective "
                    "explicitly asks for it."
                )
        return "\n".join(lines)

    def _objective_with_item_scope_context(
        self,
        item: BacklogItem,
        objective: str,
    ) -> str:
        metadata = self._render_backlog_item_metadata(item)
        if not metadata:
            return objective
        return f"{metadata}\n\nOriginal operator objective:\n{objective.strip()}"

    @staticmethod
    def _completion_evidence_from_outcome(outcome: Any) -> str:
        for attr in ("final_message", "completion_summary_markdown", "stop_reason"):
            value = getattr(outcome, attr, "") or ""
            if value:
                return str(value)[:4000]
        return ""

    def _journal_has_full_paper_gate_success(self) -> bool:
        """Decide whether the project-final completion gate has passed.

        Source of truth (post-validator-retirement): the event timeline. A
        ``final_submission`` mission is certified complete only when the
        reviewer returns a full-pipeline completion verdict, which the
        supervisor records as a ``life.mission.completed`` event carrying
        ``final_submission_certified = True``. We no longer call the
        hardcoded ``validate_full_paper_readiness`` validator — the reviewer's
        checklist verdict is the single source of truth.

        Fail-closed: only an explicit certified entry counts. We scan the
        recent event-backed history tail for such an entry.
        """
        if self._final_submission_cert_path().exists():
            return True
        try:
            entries = self.memory.journal.tail(50)
        except Exception:  # noqa: BLE001
            return False
        for entry in entries:
            if getattr(entry, "kind", "") != "mission_complete":
                continue
            extra = getattr(entry, "extra", {}) or {}
            if isinstance(extra, dict) and bool(
                extra.get("final_submission_certified")
            ):
                return True
        return False

    def _effective_full_paper_gate(self, workdir: object) -> bool:
        """Whether the full-pipeline final-submission gate applies here.

        Returns ``self.config.full_paper_gate`` AND the active vertical's
        completion gate being the paper gate (``"full_paper"``). The
        final-submission completion gate only makes sense for a *research*
        vertical: a ``speedrun`` mission runs just the optimize+measure stages
        and has no submission package to certify, so requiring the gate would
        wedge it forever. AND-ing with the vertical's own completion gate keeps
        research behavior identical (gate stays on) while letting speedrun
        missions accept ``project_done`` straight from the run loop (gate off).
        The read side is deterministic and exception-free, so this never spends
        a token.
        """
        if not self.config.full_paper_gate:
            return False
        from ...skills.vertical_select import (
            VerticalResolutionError,
            resolve_vertical,
        )
        from ...verticals._base import (
            load_vertical,
            vertical_completion_gate,
        )

        try:
            vertical = resolve_vertical(workdir)
        except VerticalResolutionError:
            # The Manager has not decided + persisted the vertical yet. An
            # undecided mission is definitionally not at its final-submission
            # gate, so the gate does not apply (keep running); it is NOT a silent
            # default to research — resolve_vertical still raised loudly, we just
            # treat "no vertical yet" as "gate not satisfied" for THIS check.
            return False
        mod = load_vertical(vertical, project_root=workdir)
        return vertical_completion_gate(mod) == "full_paper"

    def _final_submission_cert_path(self) -> Path:
        root = Path(
            getattr(self.config, "telemetry_dir", None)
            or getattr(self.memory, "root", None)
            or "."
        )
        return root / "final_submission_certified.json"

    def _persist_final_submission_certification(self, *, title: str) -> None:
        path = self._final_submission_cert_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        payload = {
            "certified_at": time.time(),
            "title": title,
        }
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _operator_only_external_blocker_wait_reason(self) -> str:
        """Return a waiting reason for an operator-only external blocker.

        Generic: scans for operator-only external blocker artifacts,
        validates that local engineering is exhausted, and returns a human
        reason string. Empty string when nothing matches or when local action
        is still required.
        """
        return _operator_only_external_blocker_wait_reason_for_project(
            self._project_workdir()
        )

    @staticmethod
    def _operator_external_blocker_short_circuit_decision(
        *, project_root: Path
    ) -> Any | None:
        """Return a waiting verdict before planner runs when operator-only
        external artifacts are still absent.
        """
        reason = _operator_only_external_blocker_wait_reason_for_project(project_root)
        if not reason:
            return None
        from ...planner.planner import PlannerVerdict

        return PlannerVerdict(
            project_done=False,
            reason=(
                f"{reason}; skipping planner cycle to avoid impossible "
                "repair-task loop"
            ),
            waiting=True,
            waiting_reason=(
                f"{reason}; skipping planner cycle to avoid impossible "
                "repair-task loop"
            ),
            new_tasks=[],
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )

    def _defer_project_done_for_operator_external_blocker(self, verdict: Any) -> Any:
        if not (
            getattr(verdict, "project_done", False)
            and self._effective_full_paper_gate(self._artifact_root())
            and not self._journal_has_full_paper_gate_success()
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
                root = getattr(self.config, "telemetry_dir", None)
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
                        if event.get("continuous_generation") != target_generation:
                            continue
                        data = event
                        break
                if data is not None:
                    break
            if data is None:
                return {}
            keep = (
                "intent_id", "source", "execution_task", "vertical", "kind",
                "continuous_generation",
                "regular", "stages", "reason", "text", "error",
            )
            return {k: data.get(k) for k in keep if k in data}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _manager_intent_prompt_block(intent: dict[str, Any]) -> str:
        if not intent:
            return ""
        parts = [
            "## Manager intent boundary (authoritative)",
            f"- intent_id: {intent.get('intent_id') or ''}",
            f"- source: {intent.get('source') or ''}",
            f"- execution_objective: "
            f"{intent.get('execution_task') or ''}",
            f"- interpreted_vertical: {intent.get('vertical') or ''}",
            f"- kind: {intent.get('kind') or ''}",
            f"- stages: {', '.join(str(s) for s in (intent.get('stages') or []))}",
            f"- reason: {intent.get('reason') or intent.get('text') or ''}",
            "",
            "Plan only work consistent with this Manager boundary. If it appears "
            "wrong, surface a Manager/Planner mismatch instead of silently "
            "switching scope.",
        ]
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
            enabled, objective = provider()
            self.config.continuous = enabled
            if objective:
                self.config.continuous_objective = objective
        except Exception:  # noqa: BLE001
            log.debug("continuous config provider raised; keeping current values")

    # ------------------------------------------------------------------
    # Planner — continuous improvement mode
    # ------------------------------------------------------------------

    def _record_planner_waiting(self, verdict: Any, *, planner_cost_usd: float) -> str:
        contract = getattr(verdict, "waiting_contract", None)
        contract_state = (
            self._persist_planner_waiting_contract(contract)
            if contract is not None
            else None
        )
        sleep_s = self._enter_idle_backoff()
        reason = verdict.waiting_reason or verdict.reason or "awaiting external dependency"
        self._emit({
            "type": EventType.LIFE_PLANNER_WAITING,
            "cycle": self._planning_cycles,
            "reason": reason,
            "consecutive_idle_cycles": self._consecutive_idle_planner_cycles,
            "suggested_sleep_s": sleep_s,
            "input_tokens": getattr(verdict, "input_tokens", 0),
            "cached_input_tokens": getattr(verdict, "cached_input_tokens", 0),
            "output_tokens": getattr(verdict, "output_tokens", 0),
            "cost_usd": planner_cost_usd,
            "waiting_contract": self._waiting_contract_event_payload(
                contract_state,
                contract,
            ),
            "waiting_contract_persisted": (
                contract is None or contract_state is not None
            ),
        })
        self._emit_status(f"awaiting external dependency: {reason}")
        return PLAN_AWAITING

    def _planner_waiting_contract_path(self) -> Path:
        root = Path(
            getattr(self.config, "telemetry_dir", None)
            or getattr(self.memory, "root", None)
            or "."
        )
        objective_fingerprint = self._planner_waiting_objective_fingerprint()
        return root / f"planner-waiting-contract-{objective_fingerprint[:16]}.json"

    def _planner_waiting_objective_fingerprint(self) -> str:
        objective = str(getattr(self.config, "continuous_objective", "") or "")
        return hashlib.sha256(objective.encode("utf-8")).hexdigest()

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
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
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
        if not str(payload.get("blocker_fingerprint") or "").strip():
            return None
        if not str(payload.get("recheck_token") or "").strip():
            return None
        return payload

    def _write_planner_waiting_contract_state(
        self,
        payload: dict[str, Any],
    ) -> bool:
        path = self._planner_waiting_contract_path()
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

    def _persist_planner_waiting_contract(
        self,
        contract: Any,
    ) -> dict[str, Any] | None:
        blocker_fingerprint, recheck_token = self._waiting_contract_key(contract)
        recheck_condition = str(
            getattr(contract, "recheck_condition", "") or ""
        ).strip()
        if not blocker_fingerprint or not recheck_token or not recheck_condition:
            return None
        previous = self._load_planner_waiting_contract_state() or {}
        same_condition = (
            previous.get("blocker_fingerprint") == blocker_fingerprint
            and previous.get("recheck_token") == recheck_token
        )
        now = time.time()
        payload = {
            "version": 1,
            "objective_fingerprint": self._planner_waiting_objective_fingerprint(),
            "blocker_fingerprint": blocker_fingerprint,
            "recheck_condition": recheck_condition,
            "recheck_token": recheck_token,
            "allow_verification_probe": bool(
                getattr(contract, "allow_verification_probe", False)
            ),
            "recheck_after_seconds": max(
                0,
                min(
                    604800,
                    int(getattr(contract, "recheck_after_seconds", 0) or 0),
                ),
            ),
            "first_observed_at": (
                float(previous.get("first_observed_at") or now)
                if same_condition
                else now
            ),
            "updated_at": now,
            "last_probe_fingerprint": str(
                previous.get("last_probe_fingerprint") or ""
            ),
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
            entry
            for entry in (state.get("probed_conditions") or [])
            if isinstance(entry, dict)
        ]
        if not any(
            entry.get("blocker_fingerprint") == blocker_fingerprint
            and entry.get("recheck_token") == recheck_token
            for entry in probed_conditions
        ):
            probed_conditions.append({
                "blocker_fingerprint": blocker_fingerprint,
                "recheck_token": recheck_token,
                "probed_at": probed_at,
            })
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
                getattr(item, "id", "") == item_id
                for item in self.memory.backlog.all()
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "failed to reconcile pending planner verification probe"
            )
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
        }
        return {
            key: source.get(key)
            for key in (
                "blocker_fingerprint",
                "recheck_condition",
                "recheck_token",
                "allow_verification_probe",
                "recheck_after_seconds",
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
            f"- blocker_fingerprint: {state['blocker_fingerprint']}\n"
            f"- recheck_token: {state['recheck_token']}\n"
            f"- recheck_condition: {state.get('recheck_condition') or ''}\n"
            f"- last_probe_at: {state.get('last_probe_at') or 0}\n"
            "If current evidence does not satisfy the declared recheck condition, "
            "reuse the exact fingerprint and token with waiting=true and do not "
            "queue an equivalent polling task. Change the token only when concrete "
            "current evidence changes; the harness does not infer that change."
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
            contract_state = self._reconcile_planner_waiting_contract_probe(
                contract_state
            )
            if contract_state is None:
                return False
            blocker_fingerprint, recheck_token = self._waiting_contract_key(contract)
            if (
                contract_state.get("blocker_fingerprint") != blocker_fingerprint
                or contract_state.get("recheck_token") != recheck_token
                or not bool(contract_state.get("allow_verification_probe"))
            ):
                return False
            if (
                time.time()
                < float(contract_state.get("first_observed_at") or 0.0)
                + float(contract_state.get("recheck_after_seconds") or 0.0)
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
            for it in self.memory.backlog.all():
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
        item_budget = self._item_iteration_budget()
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
            max_cost_usd=item_budget,
            tags=["planner", "scope:bounded", "life", "verification_probe"],
            iterate=True,
            iteration_max_cycles=1,
            iteration_budget_usd=min(item_budget, 5.0),
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
                self._write_planner_waiting_contract_state(
                    contract_state_before_probe
                )
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
        self._emit({
            "type": EventType.LIFE_PLANNER_VERIFICATION_PROBE,
            "cycle": self._planning_cycles,
            "reason": reason,
            "idle_cycles": n,
            "waiting_contract": self._waiting_contract_event_payload(
                contract_state,
                contract,
            ),
        })
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
        self._emit({
            "type": EventType.LIFE_PLANNER_STALL_ESCALATION,
            "consecutive_no_progress_missions": n,
            "objective": (self.config.continuous_objective or "")[:200],
        })

    def _wiki_collect_task_if_due_under_blocker(self) -> Any | None:
        project_root = self._project_workdir()
        if not _operator_only_external_blocker_wait_reason_for_project(project_root):
            return None
        autors = project_root / ".autors"
        if not autors.is_dir():
            return None
        from datetime import datetime, timezone

        from ...planner import TaskSpec
        from ...wiki.bootstrap import is_initialized_wiki
        from ...wiki.bot_state import collect_cooldown_elapsed, load_bot_state

        now = datetime.now(timezone.utc)
        for candidate in sorted(autors.glob("*/wiki")):
            if not is_initialized_wiki(candidate):
                continue
            state = load_bot_state(candidate / "data" / "bot_state.json")
            if not collect_cooldown_elapsed(state=state, now=now):
                continue
            project_name = candidate.parent.name
            return TaskSpec(
                title=f"wiki_collect: refresh {project_name} idea wiki",
                objective=(
                    "wiki_collect mission. Use the `wiki-collector` engineer "
                    "skill to derive 5-10 project-state search queries, ingest "
                    "new paper/repo sources into `.autors/"
                    f"{project_name}/wiki/sources/`, and update "
                    "`data/bot_state.json`. This mission is allowed while the "
                    "project is externally blocked because it is train-free and "
                    "uses the shared per-mission budget. Do not run GPU work."
                ),
                impact_score=4,
                impact_area="discovery",
                evidence="collector cooldown elapsed while project waits on external artifacts",
                scope=PLANNER_SCOPE_BOUNDED,
            )
        return None

    def _enqueue_wiki_collect_task(self, task: Any) -> bool | str:
        item = BacklogItem.new(
            title=task.title,
            objective=task.objective,
            priority=100,
            tags=[*self._planner_task_tags(task), "wiki_collect"],
            iterate=True,
            iteration_max_cycles=1,
            iteration_budget_usd=min(self._item_iteration_budget(), 5.0),
        )
        self.memory.backlog.add(item)
        self._emit({
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
        })
        delivered = self._emit_planner_verdict(
            status=PlannerVerdictStatus.PLANNED,
            completion_kind="tasks_scheduled",
            resume_outcome=True,
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
