"""Classify operator inbox input without applying its authority.

Durable consumers freeze this result before the canonical write. Classification
can be retried before that freeze; it is not an exactly-once model operation.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..core.file_lock import FileLockCancelled
from ..core.operator_context import IntakeDecision, standing_sounding
from ..core.run_gateway import current_run_interrupt_reason

log = logging.getLogger(__name__)


def check_intake_cancelled() -> None:
    if current_run_interrupt_reason():
        raise FileLockCancelled("operator intake cancelled before acceptance")


def classify_operator_message(state_root: Path | str, text: str) -> IntakeDecision:
    """Return one proposed effect; the caller owns freezing and application."""
    from .directive import ACTIVE_MANAGER_DIRECTIVE_PREFIX, STEERING_HEADER

    check_intake_cancelled()
    normalized = str(text or "").strip()
    # These are projections of an already stored directive, not new authority.
    if not normalized or normalized.startswith((STEERING_HEADER, ACTIVE_MANAGER_DIRECTIVE_PREFIX)):
        return IntakeDecision(kind="ephemeral")
    return IntakeDecision(
        kind="standing_directive" if standing_sounding(normalized) else "objective_amendment",
        scope="project" if standing_sounding(normalized) else "mission",
    )
