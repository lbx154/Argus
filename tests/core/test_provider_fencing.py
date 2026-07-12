from __future__ import annotations

import pytest

from argus_skill.core.provider_fencing import provider_spend_fence


def test_provider_fence_capabilities_are_explicit() -> None:
    claude = provider_spend_fence("claude", 0.125)
    assert claude.enforcement == "hard"
    assert claude.limit_usd == pytest.approx(0.125)

    codex = provider_spend_fence("codex", 0.125)
    assert codex.enforcement == "unsupported"
    assert "no per-call token or dollar limit" in codex.reason

    too_small = provider_spend_fence("copilot", 0.299)
    assert too_small.enforcement == "unsupported"
    assert too_small.max_ai_credits is None

    copilot = provider_spend_fence("copilot", 0.305)
    assert copilot.enforcement == "soft"
    assert copilot.max_ai_credits == 30
    assert copilot.limit_usd == pytest.approx(0.30)

    unlimited = provider_spend_fence("claude", 0.0)
    assert unlimited.enforcement == "none"
