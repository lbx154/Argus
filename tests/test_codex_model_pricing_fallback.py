"""Regression tests for the codex empty-model-id pricing bug.

Background: a codex call that does not pin ``options.model`` (every Manager
classify call — ``manager-frontdoor-classify`` / ``manager-route`` / ... build
``RunnerOptions(...)`` with no ``model=``) gets no model echoed back in the
codex response.  The usage record was then written with an empty model, which
prices as ``unpriced``; one unresolved ``unpriced`` call trips
``cost_control``'s block guard and freezes EVERY subsequent provider call on the
whole root (observed 2026-07-11).

The fix backfills the recorded model with the configured/canonical model
(``resolve_pricing_model`` + ``AgentCliBackend._configured_pricing_model``),
while still leaving a genuinely unknown *pinned* model ``unpriced`` so the
budget guard keeps protecting against real unknown-cost calls.

We never spawn a real codex CLI: ``AgentCliRunner.run_exec`` is monkeypatched to
return a synthetic result, mirroring ``test_unpriced_call_blocks_next_provider_spawn``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from argus_skill.adapters.agent_cli_backend import (
    AgentCliBackend,
    resolve_pricing_model,
)
from argus_skill.core.models import RunnerOptions

from .test_agent_cli_backend import _make_argus_result


# --- pure helper: model selection + traceable fallback source ---------------

def test_resolve_pricing_model_prefers_response_model() -> None:
    assert resolve_pricing_model("gpt-5.5", "req", "def") == ("gpt-5.5", "")


def test_resolve_pricing_model_falls_back_to_request_when_response_empty() -> None:
    assert resolve_pricing_model("", "req-model", "def") == ("req-model", "request")
    # whitespace-only response is treated as empty
    assert resolve_pricing_model("   ", "req-model", "def") == ("req-model", "request")


def test_resolve_pricing_model_falls_back_to_configured_default() -> None:
    assert resolve_pricing_model("", "", "gpt-5.5") == (
        "gpt-5.5",
        "configured_default",
    )
    assert resolve_pricing_model(None, None, "gpt-5.5") == (
        "gpt-5.5",
        "configured_default",
    )


def test_resolve_pricing_model_empty_when_nothing_usable() -> None:
    # No reliable fallback -> stay empty so pricing HONESTLY blocks (never fake
    # a priced model).
    assert resolve_pricing_model("", "", "") == ("", "none")
    assert resolve_pricing_model(None, None, None) == ("", "none")


def test_configured_pricing_model_is_codex_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "gpt-5.5")
    backend = AgentCliBackend(backend="codex")
    assert backend._configured_pricing_model() == "gpt-5.5"
    # Non-codex backends keep their existing behaviour (copilot prices via
    # premium requests; claude echoes its model) -> no backfill.
    backend._is_codex = False
    assert backend._configured_pricing_model() == ""


# --- integration: the actual bug + the guard it must not weaken -------------

def _codex_backend(tmp_path, monkeypatch, *, model_env: str = "gpt-5.5"):
    root = tmp_path / "home"
    project = root / "projects" / "p1"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    monkeypatch.setenv("ARGUS_SKILL_MODEL", model_env)
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, mission_id="m1")

    def fake_run_exec(self: Any, **kwargs: Any):  # noqa: ANN401
        # codex echoes NO model in its response (the shape that caused the bug)
        return _make_argus_result(
            json_events=[{
                "type": "token_count",
                "input_tokens": 100,
                "output_tokens": 20,
            }],
            thread_id="thr-x",
        )

    monkeypatch.setattr(
        backend._argus_runner.__class__, "run_exec", fake_run_exec, raising=True,
    )
    return backend, root


def test_codex_call_without_pinned_model_is_priced_not_blocked(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, root = _codex_backend(tmp_path, monkeypatch)

    # RunnerOptions() with NO model — exactly the Manager classify shape.
    first = backend.run_exec(
        prompt="status?",
        options=RunnerOptions(),
        run_label="manager-frontdoor-classify",
    )
    assert first.pricing_status == "priced"
    assert first.cost_usd is not None and first.cost_usd > 0

    # The bug was that the first (unpriced) call blocked the NEXT one. With the
    # fix there is no unresolved unpriced call, so the second call runs fine.
    second = backend.run_exec(
        prompt="still up?",
        options=RunnerOptions(),
        run_label="manager-route",
    )
    assert second.pricing_status == "priced"
    assert "unresolved provider cost blocks new calls" not in str(
        second.fatal_error or ""
    )

    state = json.loads((root / "cost-control.json").read_text())
    assert state["unresolved"] == []


def test_codex_unknown_pinned_model_still_unpriced_and_blocks(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Safety: the fallback must NOT paper over a genuinely unknown *pinned*
    # model. A caller that explicitly asked for an unpriceable model still
    # trips the guard (fallback only fires when the request model is empty).
    backend, root = _codex_backend(tmp_path, monkeypatch)

    first = backend.run_exec(
        prompt="unknown price",
        options=RunnerOptions(model="future-model"),
        run_label="engineer-r1",
    )
    second = backend.run_exec(
        prompt="must not spawn",
        options=RunnerOptions(model="gpt-5.6-sol"),
        run_label="reviewer",
    )

    assert first.pricing_status == "unpriced"
    assert first.cost_usd is None
    assert "unresolved provider cost blocks new calls" in str(second.fatal_error)
    assert second.pricing_status == "not_billed"
