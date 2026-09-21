"""Authoritative control receipts emitted by the local runner."""

from __future__ import annotations


def is_provider_turn_cap_receipt(value: object) -> bool:
    """Identify the terminal cap receipt, not a mention in historical stderr."""
    return str(value or "").strip().casefold().startswith("provider turn cap reached:")


PROVIDER_BACKGROUND_WAIT_RECEIPT = (
    "Provider background wait ended: the CLI returned an empty final answer "
    "after reporting that its native command was still running. Continue the "
    "unfinished work from actual process and output state before independent review."
)


def is_provider_background_wait_receipt(value: object) -> bool:
    """Identify the runner's incomplete native-command wait receipt."""
    return str(value or "").strip().casefold().startswith("provider background wait ended:")


ACCOUNTING_PENDING_LOST_SESSION_IDENTITY = "lost_session_identity"
ACCOUNTING_PENDING_SESSION_IDENTITY_CONFLICT = "session_identity_conflict"


def accounting_pending_receipt(cause: str) -> str:
    """Name why this call's provider cost cannot be reconciled yet.

    Persisted with the usage record's error text, so the strict-admission
    refusal reads as accounting pending for that cause rather than as a
    spending limit. It never settles, prices, or completes anything.
    """
    return f"accounting_pending: {cause}"
