"""Mission lifecycle phases: claim/context setup and runner invocation.

``MissionExecutionRuntimeMixin`` covers the first half of one claimed
backlog item's life: building its prelude/context packet/cost sink, invoking
the skill-loop runner (including the restricted validator-repair capability
claim), and deriving the basic outcome fields (success/status/stop_kind) plus
the budget/provider pause short-circuit. The second half (repair-capability
settlement, dynamic-plan stage guard, final status + journal) lives in
``_mission_execution_settlement.py``.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.stop_kinds import normalize_stop_kind, pause_status_for_stop_kind
from ...core.usage import UsageLedger, UsageRecord
from ..memory import BacklogItem
from ..mission_outcome import mission_outcome_class, mission_outcome_dimensions
from ._cost import _CostTrackingSink
from ._mission_execution_helpers import _MissionRunState

log = logging.getLogger(__name__)


def _maintenance_sidecar_path(life_root: Path | str, item_id: str) -> Path:
    return Path(life_root) / "maintenance" / "pending" / f"{item_id}.json"


def dispose_maintenance_worktree(
    life_root: Path | str,
    item_id: str,
    *,
    keep_sidecar: bool = False,
) -> None:
    """Remove the authoring worktree recorded for one maintenance mission."""
    sidecar = _maintenance_sidecar_path(life_root, item_id)
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    repository = Path(metadata["repository"]).expanduser().resolve(strict=True)
    worktree = Path(metadata["worktree"]).expanduser().resolve()
    if worktree.exists():
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError("maintenance authoring worktree could not be removed")
    if not keep_sidecar:
        sidecar.unlink(missing_ok=True)


class MissionExecutionRuntimeMixin:
    """Claim/context setup and runner invocation for one mission."""

    # ------------------------------------------------------------------
    # Phase: claim/context
    # ------------------------------------------------------------------

    def _build_mission_prelude(self, item: BacklogItem) -> str:
        try:
            prelude = self.memory.render_prelude(objective=item.objective)
        except TypeError:
            # Compatibility with narrow host-provided memory views.
            prelude = self.memory.render_prelude()
        from ...core.operator_context import build_operator_context_block

        operator_context, _revision = build_operator_context_block(
            "engineer",
            self.memory.root,
            mission_id=item.id,
            consume_once=False,
        )
        if operator_context:
            # Live facts belong at the tail for provider prefix caching and
            # model recency; role/task policy above remains byte-stable.
            prelude = (
                prelude + "\n\n---\n\n" + operator_context
                if prelude
                else operator_context
            )
        from ..research_plan import render_research_plan_for_mission

        research_plan = render_research_plan_for_mission(self.memory.root)
        if research_plan:
            prelude = (
                research_plan + "\n\n---\n\n" + prelude
                if prelude
                else research_plan
            )
        item_metadata = self._render_backlog_item_metadata(item)
        if item_metadata:
            prelude = (
                item_metadata + "\n---\n\n" + prelude if prelude else item_metadata
            )
        rt = self.config.runtime_context
        if rt:
            prelude = rt + "\n---\n\n" + prelude if prelude else rt
        return prelude

    def _resolve_mission_workdir(self, item: BacklogItem) -> Path:
        """Resolve/adopt an ordinary Planner-selected nested repository."""
        requested = str(getattr(item, "execution_workdir", "") or "").strip()
        tags = {str(tag or "").strip().lower() for tag in item.tags}
        current = self._project_workdir().expanduser().resolve(strict=True)
        if "framework_maintenance" in tags:
            from ...core.runtime_identity import source_root

            repository = source_root().expanduser().resolve(strict=True)
            maintenance_root = Path(self.memory.root) / "maintenance"
            worktree = maintenance_root / "worktrees" / item.id
            sidecar = _maintenance_sidecar_path(self.memory.root, item.id)
            if sidecar.is_file():
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                recorded_repository = Path(metadata["repository"]).expanduser().resolve(
                    strict=True
                )
                recorded_worktree = Path(metadata["worktree"]).expanduser().resolve()
                requested_worktree = (
                    Path(requested).expanduser().resolve() if requested else recorded_worktree
                )
                if (
                    recorded_repository != repository
                    or recorded_worktree != worktree.resolve()
                    or requested_worktree != recorded_worktree
                ):
                    raise ValueError("framework maintenance worktree record is inconsistent")
                if recorded_worktree.is_dir():
                    self.memory.backlog.update(
                        item.id,
                        execution_workdir=str(recorded_worktree),
                    )
                    item.execution_workdir = str(recorded_worktree)
                    return recorded_worktree
            worktree.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            public_base = subprocess.run(
                ["git", "rev-parse", "refs/remotes/origin/main"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            try:
                subprocess.run(
                    [
                        "git", "worktree", "add", "--detach",
                        str(worktree), public_base,
                    ],
                    cwd=repository,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                pending = maintenance_root / "pending"
                pending.mkdir(parents=True, exist_ok=True)
                (pending / f"{item.id}.json").write_text(
                    json.dumps({
                        "repository": str(repository),
                        "public_base": public_base,
                        "worktree": str(worktree),
                    }, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except (OSError, subprocess.CalledProcessError):
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree)],
                    cwd=repository,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                raise
            self.memory.backlog.update(
                item.id,
                execution_workdir=str(worktree),
            )
            item.execution_workdir = str(worktree)
            return worktree
        if not requested:
            return current
        configured_reader = getattr(self, "_configured_worktree", None)
        base = configured_reader() if callable(configured_reader) else current
        base = Path(base or current).expanduser().resolve(strict=True)
        from ...core.campaign_workdir import adopt_campaign_workdir

        adopted = adopt_campaign_workdir(
            state_root=self.memory.root,
            base_root=base,
            current_root=current,
            requested=requested,
        )
        if adopted != current:
            self._emit_status(f"campaign workdir adopted: {adopted}")
        return adopted

    def _freeze_reviewed_maintenance_change(self, state: _MissionRunState) -> str:
        item = state.item
        sidecar = _maintenance_sidecar_path(self.memory.root, item.id)
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        repository = Path(metadata["repository"]).expanduser().resolve(strict=True)
        worktree = Path(metadata["worktree"]).expanduser().resolve(strict=True)
        if worktree != Path(state.execution_workdir).resolve(strict=True):
            raise ValueError("maintenance worktree does not match its runtime record")

        runtime_dir = worktree / ".argus-self-maintenance-runtime"
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)

        if subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip():
            subprocess.run(
                ["git", "add", "--all"],
                cwd=worktree,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c", "user.name=Argus Runtime",
                    "-c", "user.email=argus-runtime@localhost",
                    "commit", "-m", "Reviewed maintenance change",
                ],
                cwd=worktree,
                check=True,
                capture_output=True,
                text=True,
            )
        candidate = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        acceptance_command = tuple(shlex.split(item.acceptance_check))
        if not acceptance_command:
            raise ValueError("maintenance mission requires an executable acceptance command")

        from ...maintenance.deploy_boundary import (
            ReviewedChange,
            deployment_input_digest,
        )

        change = ReviewedChange(
            repository=repository,
            public_base=str(metadata["public_base"]),
            reviewed_candidate=candidate,
            reviewer_verdict="done",
            acceptance_command=acceptance_command,
            evidence_refs=tuple(
                json.dumps(ref, sort_keys=True, separators=(",", ":"))
                for ref in item.context_refs
            ),
            mission_id=item.id,
            receipt_dir=Path(self.memory.root) / "maintenance" / "receipts",
        )
        input_digest = deployment_input_digest(change)
        metadata.update({
            "reviewed_candidate": change.reviewed_candidate,
            "reviewer_verdict": change.reviewer_verdict,
            "acceptance_command": list(change.acceptance_command),
            "evidence_refs": list(change.evidence_refs),
            "mission_id": change.mission_id,
            "receipt_dir": str(change.receipt_dir),
            "origin_remote": change.origin_remote,
            "private_remote": change.private_remote,
            "input_digest": input_digest,
        })
        sidecar.write_text(
            json.dumps(metadata, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return input_digest

    def _mission_vertical_root(
        self,
        item: BacklogItem,
        resolved_mission_workdir: Path,
    ) -> Path:
        """Select repository policy without moving durable harness state."""
        requested = str(getattr(item, "execution_workdir", "") or "").strip()
        tags = {str(tag or "").strip().lower() for tag in item.tags}
        if requested and "framework_maintenance" not in tags:
            return resolved_mission_workdir
        # Preserve the session-state contract for framework maintenance and
        # legacy items that do not select a node workdir explicitly.
        return Path(self._artifact_root())

    def _prepare_mission_context(
        self,
        item: BacklogItem,
        prelude: str,
        resolved_mission_workdir: Path,
        vertical_root: Path,
    ) -> _MissionRunState:
        """Build per-mission context: packet, cost sink, and isolation.

        Emits ``LIFE_MISSION_STARTED``. Returns the scratch state that the
        rest of the lifecycle phases read from and write to.
        """
        state = _MissionRunState(item)
        state.prelude = prelude
        # Explicit node contracts keep stage and vertical policy beside the
        # target repository. Durable backlog/context state remains in memory.root.
        requested = str(getattr(item, "execution_workdir", "") or "").strip()
        item_tags = {str(tag or "").strip().lower() for tag in item.tags}
        if requested and "framework_maintenance" not in item_tags:
            from ...skills.stage_machine import current_stage

            state.pipeline_stage_at_start = current_stage(vertical_root)
        else:
            state.pipeline_stage_at_start = self._current_pipeline_stage() or ""
        if "framework_maintenance" not in {
            str(tag or "").strip().lower() for tag in item.tags
        }:
            from ...verticals._base import vertical_mission_prelude

            block = vertical_mission_prelude(
                vertical_root=vertical_root,
                project_root=resolved_mission_workdir,
                state_root=self.memory.root,
                stage=state.pipeline_stage_at_start,
                mission=item,
            )
            if block:
                state.prelude = (
                    block + "\n\n---\n" + state.prelude
                    if state.prelude
                    else block
                )
        state.usage_attempt_id = f"{item.id}:attempt:{max(1, int(item.attempt or 1))}"
        self._missions_started += 1
        state.item_scope = self._planner_scope_from_item(item)
        if item.plan_id and item.plan_version is not None:
            try:
                active_item_ids = [
                    row.id
                    for row in self.memory.backlog.active()
                    if row.plan_id == item.plan_id
                    and row.plan_version == item.plan_version
                    and row.status
                    not in {"done", "failed", "aborted", "skipped", "superseded"}
                ]
            except Exception:  # noqa: BLE001 - revision conflicts fail closed later
                log.exception("life supervisor: failed to capture plan revision witness")
                active_item_ids = []
            if item.id in active_item_ids:
                state.plan_revision_witness = {
                    "plan_id": item.plan_id,
                    "plan_version": item.plan_version,
                    "source_item_id": item.id,
                    "active_item_ids": active_item_ids,
                    "captured_at": time.time(),
                }

        self._emit({
            "type": EventType.LIFE_MISSION_STARTED,
            "item_id": item.id,
            "title": item.title,
            # Carry the objective on the event itself (not just the journal
            # entry) so the live mission-context line renders the
            # real goal instead of "objective=-".
            "objective": item.objective,
            "scope": state.item_scope,
            "independent_review_required": (
                self._item_requires_independent_review(item)
            ),
            "missions_started": self._missions_started,
            "attempt": item.attempt,
            "usage_attempt_id": state.usage_attempt_id,
        })

        # Phase-change callback.
        def _phase_cb(layer: str, info: dict[str, Any]) -> None:
            try:
                self._emit({
                    "type": EventType.LIFE_PHASE_STARTED,
                    "item_id": item.id,
                    "agent_layer": layer,
                    "round_index": info.get("round_index", 0),
                })
            except Exception:  # noqa: BLE001
                log.debug("phase_change event failed; non-critical")

        state.usage_root = Path(
            getattr(self.memory, "project_root", None)
            or getattr(self.memory, "root", None)
            or self._artifact_root()
        )
        try:
            from ..context_packet import create_mission_context

            state.context_packet_path = create_mission_context(
                life_dir=state.usage_root,
                mission_id=item.id,
                stage=state.pipeline_stage_at_start,
                scope=state.item_scope,
                work_kind=item.work_kind,
                objective=item.objective,
                acceptance_check=getattr(item, "acceptance_check", ""),
                plan_hypothesis=getattr(item, "plan_hypothesis", ""),
                goal_contribution=getattr(item, "goal_contribution", ""),
                expected_regressions=getattr(item, "expected_regressions", ""),
                decision_rule=getattr(item, "decision_rule", ""),
                execution_workdir=str(resolved_mission_workdir),
                owns_paths=list(getattr(item, "owns_paths", []) or []),
                non_goals=list(getattr(item, "non_goals", []) or []),
                context_refs=list(getattr(item, "context_refs", []) or []),
                plan_id=item.plan_id,
                plan_version=item.plan_version,
                node_key=item.node_key,
                deps=item.deps,
                tags=item.tags,
            )
        except Exception:  # noqa: BLE001 - packet persistence must fail soft
            log.exception("life supervisor: failed to create mission context packet")
        state.usage_ledger = (
            UsageLedger(state.usage_root)
            if hasattr(self.runner, "_set_usage_context")
            else None
        )
        state.cost_sink = _CostTrackingSink(
            self.sink,
            engineer_model=self.engineer_model,
            reviewer_model=self.reviewer_model,
            on_phase_change=_phase_cb,
            usage_ledger=state.usage_ledger,
            mission_id=state.usage_attempt_id,
        )

        state.item_tags = {
            str(tag).strip().lower()
            for tag in getattr(item, "tags", [])
        }
        state.execution_workdir = resolved_mission_workdir
        state.vertical_root = vertical_root
        state.configured_execution_workdir = str(
            getattr(item, "execution_workdir", "") or ""
        ).strip()
        if (
            state.configured_execution_workdir
            and "framework_maintenance" in state.item_tags
        ):
            state.execution_workdir = Path(
                state.configured_execution_workdir
            ).expanduser().resolve()
            if not state.execution_workdir.is_dir():
                raise ValueError("framework maintenance worktree is unavailable")
        # Per-item codex SESSION ISOLATION (anti context-pollution). The runner
        # chains its codex thread across execute() calls; left unchecked, a brand
        # new, unrelated backlog item RESUMES the previous mission's session and
        # inherits all its context (a plain "你上一个任务干了什么" was resuming a
        # kernel-optimization session and reading its GROUND_TRUTH). A NEW item
        # must start a FRESH session; only iteration cycles of the SAME item keep
        # the thread for continuity. Curated cross-mission memory still flows via
        # the checkpoint/prelude — this only resets the raw thread bleed.
        if getattr(self, "_last_mission_item_id", None) != item.id:
            for _attr in ("_next_seed_thread_id", "last_thread_id"):
                try:
                    if hasattr(self.runner, _attr):
                        setattr(self.runner, _attr, None)
                except Exception:  # noqa: BLE001
                    pass
        self._last_mission_item_id = item.id
        return state

    # ------------------------------------------------------------------
    # Phase: runner invocation (+ restricted validator-repair capability)
    # ------------------------------------------------------------------

    def _invoke_mission_runner(self, state: _MissionRunState) -> None:
        """Call ``self.runner.execute(...)`` and record the raw outcome.

        Mutates ``state`` in place (``outcome``, ``exc_str``, ``elapsed``,
        the repair-capability trio).
        """
        item = state.item
        state.t0 = time.time()
        try:
            execute_kwargs: dict[str, Any] = {
                "objective": item.objective,
                "sink": state.cost_sink,
                "prelude_context": state.prelude,
                "scope": state.item_scope,
            }
            review_lines = [item.objective]
            acceptance_check = str(
                getattr(item, "acceptance_check", "") or ""
            ).strip()
            if acceptance_check:
                review_lines.append(f"Acceptance check: {acceptance_check}")
            non_goals = [
                str(value).strip()
                for value in getattr(item, "non_goals", [])
                if str(value).strip()
            ]
            if non_goals:
                review_lines.append("Non-goals: " + "; ".join(non_goals))
            review_objective = "\n".join(review_lines)
            original_objective = (
                getattr(item, "original_objective", "") or item.objective
            )
            authorization_id = str(
                getattr(item, "authorization_id", "") or ""
            ).strip()
            authorization_action = str(
                getattr(item, "authorization_action", "") or ""
            ).strip().lower()
            if bool(authorization_id) != bool(authorization_action):
                raise ValueError("backlog authorization reference is incomplete")
            if authorization_id:
                if authorization_action != "validator_repair":
                    raise ValueError("unsupported authorized mission action")
                from ...manager.control_state import CampaignControlStore

                state.repair_store = CampaignControlStore(
                    Path(self.memory.root),
                    project_root=self._project_workdir(),
                )
                existing = state.repair_store.current_repair_capability(
                    mission_id=item.id,
                )
                if existing is not None:
                    if (
                        existing.get("authorization_id") != authorization_id
                        or existing.get("action") != authorization_action
                    ):
                        raise ValueError("running repair capability does not match backlog")
                    state.repair_identity = state.repair_store.campaign_identity(
                        campaign_epoch=int(existing.get("campaign_epoch") or 0),
                    )
                    state.repair_capability = existing
                    if existing.get("event") == "closed":
                        state.recovered_repair_settlement = existing
                else:
                    authorization = state.repair_store.get_authorization(authorization_id)
                    if authorization is None:
                        raise ValueError("Manager authorization is unavailable")
                    state.repair_identity = state.repair_store.campaign_identity(
                        campaign_epoch=int(authorization.get("campaign_epoch") or 0),
                    )
                    claimed = state.repair_store.claim_repair_capability(
                        authorization_id=authorization_id,
                        nonce=str(authorization.get("nonce") or ""),
                        action=authorization_action,
                        identity=state.repair_identity,
                        mission_id=item.id,
                    )
                    state.repair_capability = {
                        name: getattr(claimed, name)
                        for name in claimed.__dataclass_fields__
                    }
                if state.repair_capability.get("status") == "claimed":
                    started = state.repair_store.begin_acceptance_retry(
                        capability_id=str(state.repair_capability["capability_id"]),
                        nonce=str(state.repair_capability["nonce"]),
                        identity=state.repair_identity,
                    )
                    state.repair_capability = {
                        name: getattr(started, name)
                        for name in started.__dataclass_fields__
                    }
                public_repair = (
                    "## Restricted validator repair capability\n"
                    f"- authorization_id: {authorization_id}\n"
                    f"- capability_id: {state.repair_capability['capability_id']}\n"
                    f"- validator_id: {state.repair_capability['validator_id']}\n"
                    "- allowed_write_paths: "
                    + ", ".join(state.repair_capability.get("allowed_write_paths") or [])
                    + "\n- scientific evidence, preregistration, thresholds, and "
                    "success criteria are frozen. Edit only the listed paths. "
                    "Run the same acceptance checks once. Reviewer must compare "
                    "the old and new validator logic and reject any lowered "
                    "scientific standard."
                )
                execute_kwargs["prelude_context"] = (
                    public_repair + "\n\n---\n" + state.prelude
                    if state.prelude else public_repair
                )
            manager_decision = (
                item.manager_decision
                if isinstance(item.manager_decision, dict)
                else {}
            )
            execution_vertical = str(
                manager_decision.get("vertical") or ""
            ).strip()
            if execution_vertical:
                from ...skills.vertical_select import (
                    UnknownVerticalError,
                    require_vertical,
                )
                from ...verticals._data_domain import (
                    materialize_learned_data_domain,
                )

                vertical_root = Path(state.vertical_root)
                materialize_learned_data_domain(
                    self._budget_global_root(),
                    vertical_root,
                    execution_vertical,
                )
                try:
                    require_vertical(execution_vertical, vertical_root)
                except UnknownVerticalError:
                    # The backlog guard already attempted a fresh Manager route.
                    # If that authority is temporarily unavailable, execute under
                    # the persisted project contract rather than crash repeatedly
                    # on stale cross-machine route metadata.
                    execution_vertical = ""
            try:
                from inspect import Parameter, signature

                params = signature(self.runner.execute).parameters
                _accepts_kw = any(
                    p.kind == Parameter.VAR_KEYWORD for p in params.values()
                )
                if "original_objective" in params or _accepts_kw:
                    execute_kwargs["original_objective"] = original_objective
                if "review_objective" in params or _accepts_kw:
                    execute_kwargs["review_objective"] = review_objective
                if "preplanned" in params or _accepts_kw:
                    execute_kwargs["preplanned"] = any(
                        str(tag).strip().lower() == "planner"
                        for tag in getattr(item, "tags", [])
                    )
                if "require_independent_review" in params or _accepts_kw:
                    execute_kwargs["require_independent_review"] = (
                        self._item_requires_independent_review(item)
                    )
                if "skip_stage_transition" in params or _accepts_kw:
                    execute_kwargs["skip_stage_transition"] = (
                        self._item_skips_stage_transition(item)
                    )
                if "stage_closing" in params or _accepts_kw:
                    execute_kwargs["stage_closing"] = (
                        self._item_is_stage_closing(item)
                    )
                if "holds_stage_authority" in params or _accepts_kw:
                    execute_kwargs["holds_stage_authority"] = bool(
                        getattr(self.config, "holds_stage_authority", True)
                    )
                if "mission_id" in params or _accepts_kw:
                    execute_kwargs["mission_id"] = item.id
                if "usage_mission_id" in params or _accepts_kw:
                    execute_kwargs["usage_mission_id"] = state.usage_attempt_id
                if "context_packet_path" in params or _accepts_kw:
                    execute_kwargs["context_packet_path"] = (
                        str(state.context_packet_path)
                        if state.context_packet_path else ""
                    )
                if "working_dir_override" in params or _accepts_kw:
                    execute_kwargs["working_dir_override"] = (
                        str(state.execution_workdir)
                        if state.configured_execution_workdir
                        else ""
                    )
                maintenance_mission = "framework_maintenance" in state.item_tags
                if "maintenance_mission" in params or _accepts_kw:
                    execute_kwargs["maintenance_mission"] = maintenance_mission
                if "allow_skill_changes" in params or _accepts_kw:
                    execute_kwargs["allow_skill_changes"] = (
                        "skill_changes:allowed" in state.item_tags
                    )
                if "vertical_override" in params or _accepts_kw:
                    execute_kwargs["vertical_override"] = execution_vertical
                if "work_kind" in params or _accepts_kw:
                    execute_kwargs["work_kind"] = str(
                        getattr(item, "work_kind", "") or ""
                    ).strip()
                if state.repair_capability is not None:
                    if "max_rounds_override" in params or _accepts_kw:
                        execute_kwargs["max_rounds_override"] = 1
                    if "workflow_mode_override" in params or _accepts_kw:
                        execute_kwargs["workflow_mode_override"] = "direct"
            except (TypeError, ValueError):
                execute_kwargs["original_objective"] = original_objective
                execute_kwargs["mission_id"] = item.id
                execute_kwargs["usage_mission_id"] = state.usage_attempt_id
                execute_kwargs["require_independent_review"] = (
                    self._item_requires_independent_review(item)
                )
                execute_kwargs["skip_stage_transition"] = (
                    self._item_skips_stage_transition(item)
                )
                execute_kwargs["stage_closing"] = self._item_is_stage_closing(item)
                execute_kwargs["holds_stage_authority"] = bool(
                    getattr(self.config, "holds_stage_authority", True)
                )
                execute_kwargs["allow_skill_changes"] = (
                    "skill_changes:allowed" in state.item_tags
                )
                execute_kwargs["context_packet_path"] = (
                    str(state.context_packet_path) if state.context_packet_path else ""
                )
                execute_kwargs["vertical_override"] = execution_vertical
                execute_kwargs["work_kind"] = str(
                    getattr(item, "work_kind", "") or ""
                ).strip()
                if state.repair_capability is not None:
                    execute_kwargs["max_rounds_override"] = 1
                    execute_kwargs["workflow_mode_override"] = "direct"
            if state.recovered_repair_settlement is not None:
                from types import SimpleNamespace

                recovered_accepted = bool(
                    state.recovered_repair_settlement.get("accepted")
                )
                state.outcome = SimpleNamespace(
                    success=recovered_accepted,
                    status="done" if recovered_accepted else "error",
                    stop_reason=str(
                        state.recovered_repair_settlement.get("reason") or ""
                    ),
                    rounds=0,
                    final_review_status=(
                        "done" if recovered_accepted else "not_assessed"
                    ),
                    stage_transition={},
                )
            else:
                tracks_active_mission = hasattr(
                    self.runner,
                    "_active_mission_id",
                )
                if tracks_active_mission:
                    self.runner._active_mission_id = item.id
                # The production runner uses its artifact root for active-
                # vertical validation and as Manager's stage policy root. For
                # an explicit nested node, those repository-facing operations
                # belong to the canonical node worktree, not durable life state.
                overrides_runner_policy_root = bool(
                    state.configured_execution_workdir
                    and "framework_maintenance" not in state.item_tags
                    and hasattr(self.runner, "_artifact_root")
                )
                previous_runner_policy_root = getattr(
                    self.runner,
                    "_artifact_root",
                    None,
                )
                if overrides_runner_policy_root:
                    self.runner._artifact_root = Path(state.vertical_root)
                try:
                    state.outcome = self.runner.execute(**execute_kwargs)
                finally:
                    if overrides_runner_policy_root:
                        self.runner._artifact_root = previous_runner_policy_root
                    if tracks_active_mission:
                        self.runner._active_mission_id = ""
        except Exception as exc:  # noqa: BLE001
            state.exc_str = f"{type(exc).__name__}: {exc}"
            log.exception("life supervisor: mission raised")
            try:
                from ..runtime_failure_circuit import record_runtime_failure_circuit

                circuit = record_runtime_failure_circuit(
                    self.memory.root,
                    exc,
                    item_id=item.id,
                )
                self._emit({
                    "type": EventType.LIFE_RUNTIME_FAILURE_CIRCUIT_OPENED,
                    "item_id": item.id,
                    "fingerprint": circuit.get("fingerprint"),
                    "exception_type": circuit.get("exception_type"),
                    "callsite": circuit.get("callsite"),
                    "normalized_error": circuit.get("normalized_error"),
                    "occurrence_count": circuit.get("occurrence_count"),
                    "runtime_identity": circuit.get("runtime_identity"),
                    "newly_opened": circuit.get("newly_opened"),
                    "operator_alert": True,
                })
            except Exception:  # noqa: BLE001 - circuit failure cannot hide original
                log.exception("failed to persist mission runtime failure circuit")
        state.elapsed = time.time() - state.t0

    # ------------------------------------------------------------------
    # Phase: basic outcome derivation + budget/provider pause
    # ------------------------------------------------------------------

    def _derive_basic_outcome_fields(self, state: _MissionRunState) -> None:
        """Fill in success/status/stop_kind and settle mission-level bookkeeping.

        This covers the legacy usage-ledger fallback append and the
        ``auth_failure`` advisory event. Skill evolution waits for final
        settlement because repair rejection or a Manager HOLD may still turn a
        locally successful run into a failed mission.
        """
        outcome = state.outcome
        item = state.item
        state.success = bool(getattr(outcome, "success", False)) if outcome else False
        state.status = str(getattr(outcome, "status", "error") if outcome else "error")
        state.rounds = int(getattr(outcome, "rounds", 0) or 0)
        state.stop_reason = str(getattr(outcome, "stop_reason", "") or "")
        state.stop_kind = normalize_stop_kind(getattr(outcome, "stop_kind", None))
        if state.status == "budget_exhausted" and state.stop_kind is None:
            state.stop_kind = "budget_exhausted"
        usage_summary = state.cost_sink.usage_summary()
        state.usage_summary = usage_summary
        state.usd = usage_summary.cost_usd
        state.known_usd = usage_summary.known_cost_usd
        if state.usage_ledger is None:
            # Deterministic/memory runners used by tests do not own real
            # ``run_exec`` calls. Persist their aggregate once so subsequent
            # budget checks still exercise the same ledger-only read path.
            UsageLedger(state.usage_root, migrate_legacy=False).append(
                UsageRecord(
                    call_id=f"memory-mission:{item.id}:{int(state.t0 * 1_000_000)}",
                    project_id=state.usage_root.name,
                    mission_id=state.usage_attempt_id,
                    provider="memory",
                    model="",
                    run_label="memory.mission.aggregate",
                    started_at=state.t0,
                    completed_at=time.time(),
                    status="completed",
                    input_tokens=usage_summary.input_tokens,
                    cached_input_tokens=usage_summary.cached_input_tokens,
                    output_tokens=usage_summary.output_tokens,
                    reasoning_output_tokens=(
                        usage_summary.reasoning_output_tokens
                    ),
                    premium_requests=usage_summary.premium_requests,
                    pricing_status="priced",
                    pricing_tier="memory_aggregate",
                    cost_usd=state.known_usd,
                    cost_basis="legacy_aggregate",
                    source="legacy.events",
                )
            )

        # Auth failure: the codex backend detected an expired/invalid
        # token. Stop this drain pass so we do not immediately continue
        # with stale credentials, but do not signal the daemon's global
        # stop_event. A 7x24 worker should stay alive so it can recover
        # after credentials are refreshed, and transient provider errors
        # should not kill the supervising process.
        state.auth_failure = bool(getattr(outcome, "auth_failure", False))
        if state.auth_failure:
            self._emit({
                "type": "life.auth_failure",
                "item_id": item.id,
                "text": (
                    "⚠️  codex authentication failed — run `codex login` "
                    "to refresh credentials if this persists; the daemon "
                    "will keep polling."
                ),
            })

        # There is no post-mission Critic call: the L1 Engineer works and the L2
        # Reviewer verifies. A vertical may turn a trusted charter shortfall
        # from that verdict into another bounded cycle during settlement.

    def _maybe_pause_for_recoverable_stop(
        self, state: _MissionRunState,
    ) -> dict[str, Any] | None:
        """Return a pause result dict, or ``None`` to continue the lifecycle."""
        outcome = state.outcome
        item = state.item
        if state.status == "paused_external_work":
            from ...engineer.external_work import (
                inspect_external_work,
                parse_external_wait_request,
            )

            wait_request = parse_external_wait_request(
                str(
                    getattr(outcome, "final_message", "")
                    or getattr(outcome, "summary", "")
                    or ""
                )
            )
            if wait_request is None:
                state.status = "error"
                state.stop_reason = "external-work pause lacks a structured wait request"
                return None
            wait_kind, work_id = wait_request
            workdir = Path(state.execution_workdir)
            external_work = inspect_external_work(workdir, work_id)
            if external_work is None or not external_work.waitable:
                self.memory.backlog.update(
                    item.id,
                    status="pending",
                    started_ts=None,
                    running_owner="",
                    last_error="external work changed before pause settlement",
                )
                return {
                    "success": False,
                    "status": "external_work_changed",
                    "item_id": item.id,
                    "external_wait": {
                        "kind": wait_kind,
                        "work_id": work_id,
                        "workdir": str(workdir),
                    },
                }
            pause_outcome = mission_outcome_dimensions(
                status=state.status,
                success=False,
                review_status="",
                stop_kind=None,
                resumable=True,
            )
            pause_outcome["external_wait"] = {
                "kind": wait_kind,
                "work_id": work_id,
                "workdir": str(workdir),
            }
            self.memory.backlog.update(
                item.id,
                status=state.status,
                started_ts=None,
                finished_ts=time.time(),
                running_owner="",
                last_error=state.stop_reason,
                outcome=pause_outcome,
            )
            self._emit({
                "type": EventType.LIFE_MISSION_COMPLETED,
                "item_id": item.id,
                "success": False,
                "status": state.status,
                "outcome_class": mission_outcome_class(
                    status=state.status,
                    success=False,
                ),
                "outcome": pause_outcome,
                "stop_kind": None,
                "recoverable": True,
                "external_wait": pause_outcome["external_wait"],
                "cost_usd": state.usd,
                "known_cost_usd": state.known_usd,
                "pricing_status": state.usage_summary.pricing_status,
                "spent_usd": state.known_usd,
            })
            return {
                "success": False,
                "status": state.status,
                "item_id": item.id,
                "recoverable": True,
                "external_wait": pause_outcome["external_wait"],
                "cost_usd": state.usd,
                "known_cost_usd": state.known_usd,
                "pricing_status": state.usage_summary.pricing_status,
            }
        pause_status = pause_status_for_stop_kind(state.stop_kind)
        if state.status == "budget_exhausted":
            state.status = "paused_budget"
            pause_status = state.status
        if not pause_status:
            return None
        pause_outcome = mission_outcome_dimensions(
            status=pause_status,
            success=False,
            review_status=str(
                getattr(outcome, "final_review_status", "") or ""
            ),
            stop_kind=state.stop_kind,
            resumable=True,
        )
        self.memory.backlog.update(
            item.id,
            status=pause_status,
            finished_ts=time.time(),
            last_error=state.stop_reason,
            outcome=pause_outcome,
        )
        self._emit({
            "type": EventType.LIFE_MISSION_COMPLETED,
            "item_id": item.id,
            "success": False,
            "status": pause_status,
            "outcome_class": mission_outcome_class(
                status=pause_status,
                success=False,
            ),
            "outcome": pause_outcome,
            "stop_kind": state.stop_kind,
            "recoverable": True,
            "cost_usd": state.usd,
            "known_cost_usd": state.known_usd,
            "pricing_status": state.usage_summary.pricing_status,
            "spent_usd": state.known_usd,
            "context_packet": (
                str(state.context_packet_path.parent / "latest.json")
                if state.context_packet_path is not None
                else ""
            ),
        })
        return {
            "status": pause_status,
            "item_id": item.id,
            "success": False,
            "stop_kind": state.stop_kind,
            "recoverable": True,
            "cost_usd": state.usd,
            "known_cost_usd": state.known_usd,
            "pricing_status": state.usage_summary.pricing_status,
            "context_packet": (
                str(state.context_packet_path.parent / "latest.json")
                if state.context_packet_path is not None
                else ""
            ),
        }


__all__ = ["MissionExecutionRuntimeMixin", "dispose_maintenance_worktree"]
