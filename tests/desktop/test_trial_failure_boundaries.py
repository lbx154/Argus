"""Hosted-trial safety distinguishes provider failures from local control receipts."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from argus_skill.adapters.agent_cli_backend import _exec
from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.core.runner_receipts import is_provider_turn_cap_receipt
from argus_skill.trial import attention, client

CAP_RECEIPT = (
    "Provider turn cap reached: this engineer-r1 call used 40 provider turns "
    "(allowance 40, ARGUS_SKILL_PROVIDER_TURN_CAP). Continue with a checkpoint."
)


@pytest.fixture
def trial_host(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith(("ARGUS_SKILL_", "ARGUS_WORKBENCH_")) or name == "ARGUS_DESKTOP_TRIAL_PROFILE":
            monkeypatch.delenv(name)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))
    monkeypatch.setenv(client.TRIAL_ENV, "1")
    monkeypatch.setattr(attention, "_fingerprint", lambda: "synthetic-account-fingerprint")
    from argus_skill.core import workbench_plugins
    monkeypatch.setattr(workbench_plugins, "prepare_plugin_run", lambda prompt, options, **kw: (prompt, options))
    monkeypatch.setattr(workbench_plugins, "finish_plugin_run", lambda *_: None)
    return tmp_path


def backend():
    return SimpleNamespace(
        _runner=SimpleNamespace(backend="copilot"), _refresh_known_secret_values=lambda: None,
        _resolve_execution_options=lambda options: options, _known_secret_values=("synthetic-sensitive-value",),
    )


def run(result, monkeypatch):
    monkeypatch.setattr(_exec, "_execute_prepared", lambda *a, **kw: result)
    return _exec.execute(backend(), prompt="Synthetic test only", options=RunnerOptions(), run_label="engineer-r1")


def test_provider_turn_allowance_keeps_the_checkpoint_receipt_and_does_not_pause_the_account(trial_host, monkeypatch):
    original = RunnerResult(exit_code=-15, fatal_error=CAP_RECEIPT, stop_kind="backend_unavailable")
    result = run(original, monkeypatch)
    assert is_provider_turn_cap_receipt(result.fatal_error)
    assert result.stop_kind == "backend_unavailable"
    assert not (trial_host / "trial-attention.json").exists()
    assert not attention.reason()


@pytest.mark.parametrize("kind", [
    "budget_exhausted", "cost_unreconciled", "operator_pause", "operator_abort", "daemon_shutdown",
])
def test_existing_local_stop_kinds_remain_separate_from_trial_balance(trial_host, monkeypatch, kind):
    result = run(RunnerResult(exit_code=1, fatal_error="Local control receipt", stop_kind=kind), monkeypatch)
    assert result.stop_kind == kind
    assert result.fatal_error == "Local control receipt"
    assert not attention.reason()


@pytest.mark.parametrize("code", [
    "trial_quota_exceeded", "trial_tpm_exceeded", "provider_stream_incomplete", "provider_usage_missing",
])
def test_actual_provider_failures_still_pause_and_prevent_replay(trial_host, monkeypatch, code):
    result = run(RunnerResult(exit_code=1, fatal_error=code + ": synthetic detail", stop_kind="backend_unavailable"), monkeypatch)
    assert result.stop_kind == "permanent_error"
    assert code in result.fatal_error and "synthetic detail" in result.fatal_error
    assert attention.reason()
    saved = json.loads((trial_host / "trial-attention.json").read_text(encoding="utf-8"))
    assert saved["code"] == code
    monkeypatch.setattr(_exec, "_execute_prepared", lambda *a, **kw: pytest.fail("A paused account must not replay"))
    refused = _exec.execute(backend(), prompt="Never sent", options=RunnerOptions(), run_label="engineer-r2")
    assert refused.stop_kind == "permanent_error"


def test_unknown_failure_retains_redacted_diagnosis_without_claiming_insufficient_balance(trial_host, monkeypatch):
    result = run(RunnerResult(
        exit_code=1, fatal_error="Synthetic transport diagnostic synthetic-sensitive-value",
        stop_kind="backend_unavailable",
    ), monkeypatch)
    assert "Synthetic transport diagnostic" in result.fatal_error
    assert "synthetic-sensitive-value" not in result.fatal_error
    assert "余额不足" not in result.fatal_error
    assert "synthetic-sensitive-value" not in (trial_host / "trial-attention.json").read_text(encoding="utf-8")
    assert attention.reason()  # Unknown usage still fails closed.


def test_old_generic_pause_is_not_silently_cleared_or_presented_as_insufficient_balance(trial_host):
    file = trial_host / "trial-attention.json"
    file.write_text(json.dumps({
        "profile": "synthetic-account-fingerprint", "code": "trial_request_failed",
        "message": "旧的余额不足误导文案", "resume_after": 0,
    }), encoding="utf-8")
    before = file.read_bytes()
    reason = attention.reason()
    assert reason and "手动恢复" in reason
    assert "余额不足" not in reason
    assert file.read_bytes() == before
