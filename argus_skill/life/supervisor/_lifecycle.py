"""Project lifecycle sidecar migration and dispatch gate."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ..memory import BacklogItem
from ..project_lifecycle import (
    LifecycleEvent,
    ProjectState,
    apply_event,
    decide_next_state,
    infer_observable_status,
    is_token_allocatable,
)
from ..project_lifecycle_io import (
    LifecycleIOError,
    apply_persisted_to_status,
)
from ..project_lifecycle_io import append_event as _lifecycle_append_event
from ..project_lifecycle_io import lifecycle_path as _lifecycle_path
from ..project_lifecycle_io import load_persisted as _lifecycle_load_persisted
from ._constants import (
    LIFECYCLE_BLOCK_HEARTBEAT_SECONDS as _LIFECYCLE_BLOCK_HEARTBEAT_SECONDS,
)

log = logging.getLogger(__name__)


def resolved_vertical_or_default(artifact_root: object) -> str:
    """The active vertical, never raising.

    The whole lifecycle gate runs inside one ``except Exception`` that logs and
    allows dispatch. A raise from here would therefore skip the *block* check
    below it, letting a project that should be held through — turning a
    completion detail into a fail-open. The completion API already fails closed
    on an unreadable vertical, so handing it the default is the safe answer.
    """
    from ...skills.vertical_select import resolve_vertical

    try:
        return resolve_vertical(artifact_root)
    except Exception:  # noqa: BLE001 — see docstring; must not fail open
        from ...verticals._base import DEFAULT_VERTICAL

        return DEFAULT_VERTICAL


class LifecycleMixin:
    def _lifecycle_root(self) -> Path:
        """Return the per-project directory holding ``lifecycle.json``."""
        project_state_dir = getattr(self.config, "project_state_dir", None)
        if project_state_dir is not None:
            return Path(project_state_dir)
        return Path(getattr(self.memory, "root", None) or ".")

    def _migrate_global_lifecycle_if_needed(self, per_root: Path) -> None:
        """Carry the legacy global lifecycle sidecar into this project once."""
        if getattr(self.config, "project_state_dir", None) is None:
            return
        if getattr(self, "_lifecycle_migrated", False):
            return
        self._lifecycle_migrated = True
        try:
            per_file = _lifecycle_path(per_root)
            if per_file.exists():
                return
            global_root = Path(getattr(self.memory, "root", None) or ".")
            if global_root == per_root:
                return
            global_file = _lifecycle_path(global_root)
            if not global_file.exists():
                return
            per_root.mkdir(parents=True, exist_ok=True)
            data = global_file.read_text(encoding="utf-8")
            tmp = per_file.with_name(per_file.name + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, per_file)
            try:
                global_file.replace(
                    global_file.with_name(global_file.name + ".migrated")
                )
            except OSError:
                log.warning(
                    "lifecycle: copied global sidecar to %s but could not "
                    "retire %s; future projects may inherit it",
                    per_file,
                    global_file,
                )
            log.info(
                "lifecycle: migrated legacy global sidecar into per-project "
                "dir %s",
                per_file,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "lifecycle migration failed; continuing with fresh "
                "per-project state"
            )

    def _maybe_block_on_lifecycle(
        self,
        item: BacklogItem,
    ) -> dict[str, Any] | None:
        """Run one lifecycle tick and short-circuit non-allocatable projects."""
        try:
            memory_root = self._lifecycle_root()
            self._migrate_global_lifecycle_if_needed(memory_root)
            project_root = self._project_workdir()
            spent_usd, budget_usd = self._lifecycle_budget_snapshot()

            status = infer_observable_status(
                project_root,
                project_id=memory_root.name,
                budget_usd=budget_usd,
                spent_usd=spent_usd,
            )
            try:
                persisted = _lifecycle_load_persisted(memory_root)
            except LifecycleIOError as exc:
                log.warning(
                    "lifecycle sidecar at %s is malformed (%s); "
                    "treating project as fresh",
                    memory_root,
                    exc,
                )
                persisted = {}
            status = apply_persisted_to_status(status, persisted)

            artifact_root = (
                self._artifact_root()
                if hasattr(self, "_artifact_root")
                else memory_root
            )
            uncertified_final_result = (
                self._effective_final_certification_gate(artifact_root)
                and not self._journal_has_final_certification()
            )
            if (
                uncertified_final_result
                and status.state == ProjectState.DONE
                and persisted.get("state") == ProjectState.DONE.value
            ):
                repair_event = LifecycleEvent(
                    at=datetime.now(timezone.utc),
                    from_state=ProjectState.DONE,
                    to_state=ProjectState.WRITING,
                    reason="final_certification_gate_not_certified",
                )
                status = apply_event(status, repair_event)
                try:
                    _lifecycle_append_event(
                        memory_root,
                        new_status=status,
                        event=repair_event,
                    )
                except OSError as exc:
                    log.warning(
                        "could not repair premature-DONE lifecycle "
                        "sidecar at %s: %s",
                        memory_root,
                        exc,
                    )
                self._emit({
                    "type": EventType.LIFE_LIFECYCLE_TRANSITION,
                    "from_state": ProjectState.DONE.value,
                    "to_state": ProjectState.WRITING.value,
                    "reason": "final_certification_gate_not_certified",
                    "agent_layer": "supervisor",
                })

            event = decide_next_state(status)
            if (
                self._effective_final_certification_gate(artifact_root)
                and self._journal_has_final_certification()
                and status.state not in (ProjectState.DONE, ProjectState.ARCHIVED)
                and status.has_submission_artifact
            ):
                # The single DONE write path. The conditions above are exactly
                # the ones this branch already used, so nothing completes that
                # did not complete before; what changed is that the write, the
                # strength check and the `project.completed` event now happen in
                # one place instead of being inlined here.
                from ...core.project_api import (
                    SOURCE_INDEPENDENT_CERTIFICATION,
                    CompletionSource,
                    complete_project,
                )
                from ...verticals._base import load_vertical_contract

                vertical = resolved_vertical_or_default(artifact_root)
                outcome = complete_project(
                    memory_root=memory_root,
                    vertical=vertical,
                    required_gate=load_vertical_contract(
                        vertical, project_root=artifact_root
                    ).completion_gate,
                    source=CompletionSource(
                        kind=SOURCE_INDEPENDENT_CERTIFICATION,
                        evidence_refs=("journal:final_certification",),
                        detail="independent Reviewer certified the final result",
                    ),
                    status=status,
                    reason="reviewer_certified_final_result",
                    on_event=self._emit,
                )
                if outcome.accepted:
                    done_event = LifecycleEvent(
                        at=datetime.now(timezone.utc),
                        from_state=status.state,
                        to_state=ProjectState.DONE,
                        reason="reviewer_certified_final_result",
                    )
                    status = apply_event(status, done_event)
                    self._emit({
                        "type": EventType.LIFE_LIFECYCLE_TRANSITION,
                        "from_state": done_event.from_state.value,
                        "to_state": done_event.to_state.value,
                        "reason": done_event.reason,
                        "agent_layer": "supervisor",
                    })
                    event = None
                else:
                    log.warning(
                        "completion refused for %s: %s",
                        memory_root,
                        outcome.reason,
                    )
            if event is not None:
                status = apply_event(status, event)
                try:
                    _lifecycle_append_event(
                        memory_root,
                        new_status=status,
                        event=event,
                    )
                except OSError as exc:
                    log.warning(
                        "could not persist lifecycle transition to %s: %s",
                        memory_root,
                        exc,
                    )
                self._emit({
                    "type": EventType.LIFE_LIFECYCLE_TRANSITION,
                    "from_state": event.from_state.value,
                    "to_state": event.to_state.value,
                    "reason": event.reason,
                    "agent_layer": "supervisor",
                })

            if not is_token_allocatable(status):
                state_value = status.state.value
                signature = (state_value, item.id)
                now = time.monotonic()
                reason = (
                    f"project lifecycle is {state_value}; "
                    "resume with --lifecycle-resume or archive with "
                    "--lifecycle-archive"
                )
                last_signature = getattr(self, "_last_lifecycle_block_sig", None)
                last_at = getattr(self, "_last_lifecycle_block_at", 0.0)
                should_emit = (
                    signature != last_signature
                    or (now - last_at) >= _LIFECYCLE_BLOCK_HEARTBEAT_SECONDS
                )
                if should_emit:
                    self._last_lifecycle_block_sig = signature
                    self._last_lifecycle_block_at = now
                    self._emit_status(
                        f"lifecycle gate: project state={state_value}; "
                        f"backlog item {item.id!r} held"
                    )
                    self._emit({
                        "type": EventType.LIFE_LIFECYCLE_BLOCK,
                        "item_id": item.id,
                        "title": item.title,
                        "lifecycle_state": state_value,
                        "reason": reason,
                        "agent_layer": "supervisor",
                    })
                return {
                    "status": "lifecycle_block",
                    "item_id": item.id,
                    "lifecycle_state": state_value,
                    "reason": reason,
                }
        except Exception:  # noqa: BLE001
            log.exception("lifecycle gate failed; allowing dispatch")
        return None

    def _lifecycle_budget_snapshot(self) -> tuple[float, float]:
        """Project lifecycle no longer owns a monetary budget."""
        return (0.0, 0.0)



__all__ = ["LifecycleMixin", "resolved_vertical_or_default"]
