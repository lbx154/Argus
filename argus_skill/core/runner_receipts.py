"""Authoritative control receipts emitted by the local runner."""

from __future__ import annotations


def is_provider_turn_cap_receipt(value: object) -> bool:
    """Identify the terminal cap receipt, not a mention in historical stderr."""
    return str(value or "").strip().casefold().startswith("provider turn cap reached:")
