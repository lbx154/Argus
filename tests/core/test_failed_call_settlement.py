"""A provider-rejected call settles as failed; a call that ran stays held."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from argus.core.cost_control import (
    cost_admission_reason,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus.core.models import RunnerResult
from argus.core.operator_messages import budget_refusal_reply
from argus.core.runner_errors import is_provider_http_rejection, result_rejected_before_output
from argus.core.token_usage import TokenUsage
from argus.core.usage import PROVIDER_REJECTED_TIER, UsageLedger, build_usage_record

# The real incident: a side role's call went through a local relay that could
# not route the requested model and answered with an HTTP error.
RELAY_REJECTION = (
    "unexpected status 502 Bad Gateway: Model unlisted-relay-model resolved to "
    "unsupported endpoint openai/completions, url: http://127.0.0.1:41419/v1/responses"
)
MODEL = "unlisted-relay-model"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from argus.core.knob_store import write_persisted_knob

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    write_persisted_knob("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    (tmp_path / "projects" / "p1").mkdir(parents=True)
    return tmp_path


def _reserve(home: Path, call_id: str, *, provider: str = "codex", model: str = MODEL,
             run_label: str = "map-summary"):
    return reserve_call_budget(
        call_id=call_id, project_root=home / "projects" / "p1", mission_id="map",
        provider=provider, model=model, run_label=run_label, global_root=home,
        global_daily_cap_usd=10.0,
    )


def _failed_record(home: Path, call_id: str, *, rejected: bool, usage: TokenUsage | None = None):
    return build_usage_record(
        call_id=call_id, project_root=home / "projects" / "p1", mission_id="map",
        provider="codex", model=MODEL, run_label="map-summary",
        started_at=time.time() - 1, completed_at=time.time(), status="error",
        token_usage=usage, error=RELAY_REJECTION, rejected_before_output=rejected,
    )


def _settle(home: Path, call_id: str, record) -> None:
    reservation, reason = _reserve(home, call_id)
    assert reservation is not None, reason
    UsageLedger(home / "projects" / "p1", migrate_legacy=False).append(record)
    reservation.settle(record)


def test_relay_rejection_is_recognized_from_the_runner_result() -> None:
    assert is_provider_http_rejection(RELAY_REJECTION)
    assert is_provider_http_rejection("unexpected status 429 Too Many Requests")
    assert not is_provider_http_rejection("stream disconnected before completion")
    assert not is_provider_http_rejection("the reply mentioned status 502 in prose")

    failed = RunnerResult(exit_code=1, fatal_error=RELAY_REJECTION)
    assert result_rejected_before_output(failed, error=RELAY_REJECTION)
    # Evidence the call ran: assistant text, tool activity, a completed turn.
    assert not result_rejected_before_output(
        RunnerResult(exit_code=1, fatal_error=RELAY_REJECTION, agent_messages=["partial"]),
        error=RELAY_REJECTION)
    assert not result_rejected_before_output(
        RunnerResult(exit_code=1, fatal_error=RELAY_REJECTION, tool_activity_observed=True),
        error=RELAY_REJECTION)
    # A failure that is not an HTTP rejection keeps waiting for usage.
    assert not result_rejected_before_output(
        RunnerResult(exit_code=1, fatal_error="stream disconnected before completion"),
        error="stream disconnected before completion")
    # A named accounting-pending cause is never cleared by this path.
    assert not result_rejected_before_output(
        failed, error=RELAY_REJECTION + "\naccounting_pending: lost_session_identity")


def test_provider_rejected_call_settles_as_failed_and_does_not_block(home: Path) -> None:
    record = _failed_record(home, "call-rejected", rejected=True)
    assert record.pricing_status == "not_billed"
    assert record.pricing_tier == PROVIDER_REJECTED_TIER
    assert record.cost_usd == 0.0
    assert record.error.startswith("unexpected status 502")
    _settle(home, "call-rejected", record)

    assert cost_admission_reason(global_root=home) == ""
    snapshot = cost_control_snapshot(global_root=home)
    assert snapshot["unresolved_calls"] == 0
    assert snapshot["blocking_unresolved_calls"] == 0
    stored = UsageLedger(home / "projects" / "p1", migrate_legacy=False).records()
    assert [(r.call_id, r.status, r.pricing_status, r.error) for r in stored] == [
        ("call-rejected", "error", "not_billed", record.error)]

    # The next call on another backend is admitted.
    nxt, reason = _reserve(home, "call-next", provider="copilot", model="", run_label="manager")
    assert nxt is not None and reason == ""
    nxt.release(reason="test")


def test_reported_usage_overrides_the_rejection_flag(home: Path) -> None:
    usage = TokenUsage(input_tokens=10, input_tokens_present=True, source="test")
    record = _failed_record(home, "call-ran", rejected=True, usage=usage)
    assert record.pricing_status != "not_billed"


def test_call_that_ran_without_usage_stays_fail_closed_and_names_the_role(home: Path) -> None:
    record = _failed_record(home, "call-ran", rejected=False)
    assert record.pricing_status in {"partial", "unpriced"}
    _settle(home, "call-ran", record)

    reason = cost_admission_reason(global_root=home)
    assert reason.startswith("unresolved provider cost: 1 call(s) awaiting usage reconciliation")
    assert "call=call-ran" in reason
    assert "role=map-summary" in reason
    assert "project=p1" in reason
    assert "unblock: POST /api/projects/p1/cost-control/acknowledge" in reason
    assert '"call_id": "call-ran"' in reason
    assert "ARGUS_SKILL_UNPRICED_COST_POLICY=allow" in reason

    blocked, refusal = _reserve(home, "call-next", provider="copilot", model="", run_label="manager")
    assert blocked is None
    reply = budget_refusal_reply(refusal, language_hint="中文")
    assert reply is not None
    assert "角色 map-summary" in reply
    assert "立即解除：POST /api/projects/p1/cost-control/acknowledge" in reply
    english = budget_refusal_reply(refusal)
    assert english is not None
    assert "(role map-summary)" in english
    assert "To unblock now: POST /api/projects/p1/cost-control/acknowledge" in english
