"""Provider-side spend fences derived from one call budget reservation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

FenceEnforcement = Literal["hard", "soft", "unsupported", "none"]

_AI_CREDITS_PER_USD = 100
_COPILOT_MIN_AI_CREDITS = 30


@dataclass(frozen=True)
class ProviderSpendFence:
    enforcement: FenceEnforcement
    limit_usd: float | None = None
    max_ai_credits: int | None = None
    reason: str = ""

    def event_fields(self) -> dict[str, object]:
        return {
            "fence_enforcement": self.enforcement,
            "fence_limit_usd": self.limit_usd,
            "fence_limit_ai_credits": self.max_ai_credits,
            "fence_reason": self.reason,
        }


def provider_spend_fence(provider: str, reservation_usd: float) -> ProviderSpendFence:
    """Return the strongest provider-side fence the installed CLI can express."""
    normalized = str(provider or "").strip().lower()
    limit = max(0.0, float(reservation_usd or 0.0))
    if limit <= 0.0:
        return ProviderSpendFence(
            enforcement="none",
            reason="no finite positive call reservation",
        )
    if normalized == "claude":
        return ProviderSpendFence(
            enforcement="hard",
            limit_usd=limit,
            reason="Claude CLI --max-budget-usd",
        )
    if normalized == "copilot":
        credits = math.floor(limit * _AI_CREDITS_PER_USD + 1e-9)
        if credits < _COPILOT_MIN_AI_CREDITS:
            return ProviderSpendFence(
                enforcement="unsupported",
                limit_usd=limit,
                reason=(
                    "Copilot CLI requires at least 30 AI credits ($0.30); "
                    "the reservation is smaller"
                ),
            )
        return ProviderSpendFence(
            enforcement="soft",
            limit_usd=credits / _AI_CREDITS_PER_USD,
            max_ai_credits=credits,
            reason=(
                "Copilot CLI --max-ai-credits is checked after each model "
                "response and can overrun once"
            ),
        )
    if normalized == "codex":
        return ProviderSpendFence(
            enforcement="unsupported",
            limit_usd=limit,
            reason="Codex CLI exposes no per-call token or dollar limit",
        )
    return ProviderSpendFence(
        enforcement="unsupported",
        limit_usd=limit,
        reason=f"provider {normalized or '(missing)'} exposes no known spend fence",
    )


__all__ = [
    "FenceEnforcement",
    "ProviderSpendFence",
    "provider_spend_fence",
]
