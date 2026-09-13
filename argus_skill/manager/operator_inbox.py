"""Classify operator inbox input without applying its authority.

Durable consumers freeze this result before the canonical write. Classification
can be retried before that freeze; it is not an exactly-once model operation.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..core.file_lock import FileLockCancelled
from ..core.operator_context import IntakeDecision, OperatorContextStore, standing_sounding
from ..core.run_gateway import current_run_interrupt_reason

log = logging.getLogger(__name__)


def check_intake_cancelled() -> None:
    if current_run_interrupt_reason():
        raise FileLockCancelled("operator intake cancelled before acceptance")


def classify_operator_message(
    state_root: Path | str, text: str, *, manager: Any = None,
) -> IntakeDecision:
    """Return one proposed effect; the caller owns freezing and application."""
    from .directive import ACTIVE_MANAGER_DIRECTIVE_PREFIX, STEERING_HEADER

    check_intake_cancelled()
    normalized = str(text or "").strip()
    # These are projections of an already stored directive, not new authority.
    if not normalized or normalized.startswith((STEERING_HEADER, ACTIVE_MANAGER_DIRECTIVE_PREFIX)):
        return IntakeDecision(kind="ephemeral")
    decisions: list[dict[str, Any]] = []
    classifier = getattr(manager, "classify_front_door", None)
    if callable(classifier):
        try:
            classifier(normalized, intake_sink=decisions.append, active_mission=True)
        except FileLockCancelled:
            raise
        except Exception:  # noqa: BLE001 - preserve the plain-guidance fallback
            check_intake_cancelled()
            decisions.clear()
            log.warning("operator classification failed; retaining plain guidance", exc_info=True)
    # Some Manager adapters translate an interrupted call into an empty result.
    # Never freeze that result as a successful fallback after Stop.
    check_intake_cancelled()
    if decisions:
        decision = IntakeDecision(**decisions[-1])
        if (
            decision.kind == "credential_grant"
            and "[stored in capability vault]" in normalized
            and any(
                record.type == "capability" and record.available
                for record in OperatorContextStore(state_root).records()
            )
        ):
            return IntakeDecision(kind="ephemeral")
        return decision
    return IntakeDecision(
        kind="standing_directive" if standing_sounding(normalized) else "objective_amendment",
        scope="project" if standing_sounding(normalized) else "mission",
    )
