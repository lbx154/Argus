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
    resolve_codex_execution_model,
    resolve_pricing_model,
)
from argus_skill.core.models import RunnerOptions

from .test_agent_cli_backend import _make_argus_result


@pytest.fixture(autouse=True)
def _isolate_codex_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_CODEX_CONFIG", raising=False)


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
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.6-sol"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    backend = AgentCliBackend(backend="codex")
    assert backend._configured_pricing_model() == "gpt-5.6-sol"
    # Non-codex backends keep their existing behaviour (copilot prices via
    # premium requests; claude echoes its model) -> no backfill.
    backend._is_codex = False
    assert backend._configured_pricing_model() == ""


def test_codex_execution_model_honors_final_cli_override() -> None:
    assert resolve_codex_execution_model(
        None,
        "gpt-5.6-sol",
        None,
        ["--model", "gpt-5.5"],
    ) == "gpt-5.5"
    assert resolve_codex_execution_model(
        "gpt-5.5",
        "gpt-5.6-sol",
        None,
        ["-c", 'model="gpt-5.4"'],
    ) == "gpt-5.5"


def test_codex_model_args_normalize_to_one_direct_flag(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.6-sol"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    backend = AgentCliBackend(
        backend="codex",
        default_extra_args=["--model", "gpt-5.5"],
    )
    resolved = backend._resolve_execution_options(
        RunnerOptions(extra_args=["-c", 'model="gpt-5.4"']),
    )
    command = backend._argus_runner._build_codex_command(
        resume_thread_id=None,
        options=backend._translate_options(resolved),
    )

    assert resolved.model == "gpt-5.5"
    assert command.count("-m") == 1
    assert "--model" not in command
    assert 'model="gpt-5.4"' not in command


def test_codex_profile_model_is_pinned_without_dropping_profile(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.5"\n',
        encoding="utf-8",
    )
    (codex_home / "research.config.toml").write_text(
        'model = "gpt-5.6-sol"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    backend = AgentCliBackend(
        backend="codex",
        default_extra_args=["--profile", "research"],
    )
    resolved = backend._resolve_execution_options(RunnerOptions())
    command = backend._argus_runner._build_codex_command(
        resume_thread_id=None,
        options=backend._translate_options(resolved),
    )

    assert resolved.model == "gpt-5.6-sol"
    assert command.count("-m") == 1
    assert command[command.index("--profile") + 1] == "research"


def test_call_profile_replaces_default_profile_once(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.4"\n',
        encoding="utf-8",
    )
    (codex_home / "default.config.toml").write_text(
        'model = "gpt-5.5"\n',
        encoding="utf-8",
    )
    (codex_home / "call.config.toml").write_text(
        'model = "gpt-5.6-sol"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    backend = AgentCliBackend(
        backend="codex",
        default_extra_args=["--profile", "default", "--strict-config"],
    )
    resolved = backend._resolve_execution_options(
        RunnerOptions(extra_args=["--profile=call", "--ephemeral"]),
    )
    command = backend._argus_runner._build_codex_command(
        resume_thread_id=None,
        options=backend._translate_options(resolved),
    )

    assert resolved.model == "gpt-5.6-sol"
    assert command.count("--profile") == 1
    assert command[command.index("--profile") + 1] == "call"
    assert "--strict-config" in command
    assert "--ephemeral" in command


def test_configured_pricing_model_stays_unknown_without_codex_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-codex-home"))
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    backend = AgentCliBackend(backend="codex")
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
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, mission_id="m1")
    seen_models: list[str | None] = []

    def fake_run_exec(self: Any, **kwargs: Any):  # noqa: ANN401
        seen_models.append(kwargs["options"].model)
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
    return backend, root, seen_models


def test_codex_call_without_pinned_model_is_priced_not_blocked(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, root, seen_models = _codex_backend(tmp_path, monkeypatch)

    # RunnerOptions() with NO model — exactly the Manager classify shape.
    first = backend.run_exec(
        prompt="status?",
        options=RunnerOptions(),
        run_label="manager-frontdoor-classify",
    )
    assert first.pricing_status == "priced"
    assert first.cost_usd is not None and first.cost_usd > 0
    assert seen_models == ["gpt-5.5"]

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
    usage_path = root / "projects" / "p1" / "usage.jsonl"
    usage_rows = [
        json.loads(line)
        for line in usage_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["model"] for row in usage_rows} == {"gpt-5.5"}


def test_codex_unknown_pinned_model_still_unpriced_and_blocks(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Safety: the fallback must NOT paper over a genuinely unknown *pinned*
    # model. A caller that explicitly asked for an unpriceable model still
    # trips the guard (fallback only fires when the request model is empty).
    backend, root, _seen_models = _codex_backend(tmp_path, monkeypatch)

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
