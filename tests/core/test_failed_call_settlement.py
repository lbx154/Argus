"""A provider-rejected call settles as failed; a call that ran stays held."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from argus.agent_cli.models import AgentRunResult
from argus.core.cost_control import (
    cost_admission_reason,
    cost_control_snapshot,
    reserve_call_budget,
)
from argus.core.models import RunnerResult
from argus.core.runner_errors import (
    is_provider_http_rejection,
    model_output_observed,
    result_rejected_before_output,
)
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

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
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
    assert result_rejected_before_output(failed, error=RELAY_REJECTION, model_output=False)
    # Unknown runner evidence is treated as output: the call stays held.
    assert not result_rejected_before_output(failed, error=RELAY_REJECTION)
    # Model output observed by the runner (e.g. a reasoning item) means it ran.
    assert not result_rejected_before_output(failed, error=RELAY_REJECTION, model_output=True)
    # Evidence the call ran: assistant text, tool activity, a completed turn.
    assert not result_rejected_before_output(
        RunnerResult(exit_code=1, fatal_error=RELAY_REJECTION, agent_messages=["partial"]),
        error=RELAY_REJECTION, model_output=False)
    assert not result_rejected_before_output(
        RunnerResult(exit_code=1, fatal_error=RELAY_REJECTION, tool_activity_observed=True),
        error=RELAY_REJECTION, model_output=False)
    # A failure that is not an HTTP rejection keeps waiting for usage.
    assert not result_rejected_before_output(
        RunnerResult(exit_code=1, fatal_error="stream disconnected before completion"),
        error="stream disconnected before completion", model_output=False)
    # A named accounting-pending cause is never cleared by this path.
    assert not result_rejected_before_output(
        failed, error=RELAY_REJECTION + "\naccounting_pending: lost_session_identity",
        model_output=False)


def _cli_result(events: list[dict], **fields) -> AgentRunResult:
    return AgentRunResult(command=["codex"], exit_code=1, json_events=events,
                          json_event_count=len(events), **fields)


LIFECYCLE = [{"type": "thread.started"}, {"type": "turn.started"},
             {"type": "error", "message": RELAY_REJECTION},
             {"type": "turn.failed", "error": {"message": RELAY_REJECTION}}]


def test_model_output_evidence_from_the_runner() -> None:
    # Request lifecycle alone is not output, even though it counts as progress.
    assert not model_output_observed(_cli_result(LIFECYCLE, model_progress_observed=True))
    reasoning = {"type": "item.completed", "item": {"type": "reasoning", "text": "x"}}
    assert model_output_observed(_cli_result([*LIFECYCLE[:2], reasoning, *LIFECYCLE[2:]]))
    assert model_output_observed(_cli_result([*LIFECYCLE[:2], {"type": "item.started"}]))
    assert model_output_observed(_cli_result(LIFECYCLE, model_output_observed=True))
    assert model_output_observed(_cli_result(LIFECYCLE, provider_turns=1))
    # Events dropped from the retained capture cannot prove absence of output.
    truncated = _cli_result(LIFECYCLE)
    truncated.json_event_count = len(LIFECYCLE) + 1
    assert model_output_observed(truncated)
    # Progress with no retained events to explain it counts as output.
    assert model_output_observed(_cli_result([], model_progress_observed=True))


_FAKE_CODEX = """#!/bin/bash
if [[ "$*" == *"--version"* ]]; then echo "codex-cli 0.200.0"; exit 0; fi
cat >/dev/null
echo '{"type":"thread.started","thread_id":"019a-thread"}'
echo '{"type":"turn.started"}'
if [[ "$FAKE_MODE" == "reasoning" ]]; then
  echo '{"type":"item.completed","item":{"id":"i0","type":"reasoning","text":"thinking"}}'
fi
MSG='unexpected status 502 Bad Gateway: upstream died'
echo "{\\"type\\":\\"error\\",\\"message\\":\\"$MSG\\"}"
echo "{\\"type\\":\\"turn.failed\\",\\"error\\":{\\"message\\":\\"$MSG\\"}}"
exit 1
"""


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake CLI is a bash shebang script, which Windows cannot execute from PATH",
)
@pytest.mark.parametrize(("mode", "settles"), [("", True), ("reasoning", False)])
def test_cli_call_rejected_after_reasoning_stays_unsettled_and_counted(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, settles: bool,
) -> None:
    from argus.adapters.agent_cli_backend import AgentCliBackend
    from argus.core.models import RunnerOptions

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codex"
    fake.write_text(_FAKE_CODEX)
    fake.chmod(0o755)
    (tmp_path / "codex-home").mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    monkeypatch.setenv("FAKE_MODE", mode)

    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=home / "projects" / "p1", mission_id="map")
    result = backend.run_exec(prompt="summarize", options=RunnerOptions(model=MODEL),
                              run_label="map-summary")
    assert result.exit_code != 0
    [row] = UsageLedger(home / "projects" / "p1", migrate_legacy=False).records()
    assert row.status == "error"
    assert cost_admission_reason(global_root=home) == ""
    snapshot = cost_control_snapshot(global_root=home)
    if settles:
        assert (row.pricing_status, row.pricing_tier) == ("not_billed", PROVIDER_REJECTED_TIER)
        assert snapshot["unresolved_calls"] == 0
    else:
        assert row.pricing_status != "not_billed"
        assert [item["call_id"] for item in snapshot["unresolved"]] == [row.call_id]


def test_provider_rejected_call_settles_as_failed(home: Path) -> None:
    record = _failed_record(home, "call-rejected", rejected=True)
    assert record.pricing_status == "not_billed"
    assert record.pricing_tier == PROVIDER_REJECTED_TIER
    assert record.cost_usd == 0.0
    assert record.error.startswith("unexpected status 502")
    _settle(home, "call-rejected", record)

    assert cost_admission_reason(global_root=home) == ""
    snapshot = cost_control_snapshot(global_root=home)
    assert snapshot["unresolved_calls"] == 0
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


def test_call_that_ran_without_usage_is_counted_and_names_the_role(home: Path) -> None:
    record = _failed_record(home, "call-ran", rejected=False)
    assert record.pricing_status in {"partial", "unpriced"}
    _settle(home, "call-ran", record)

    assert cost_admission_reason(global_root=home) == ""
    snapshot = cost_control_snapshot(global_root=home)
    [row] = snapshot["unresolved"]
    assert row["call_id"] == "call-ran" and row["run_label"] == "map-summary"
    assert row["project_id"] == "p1"

    admitted, reason = _reserve(home, "call-next", provider="copilot", model="", run_label="manager")
    assert admitted is not None and reason == ""
    admitted.release(reason="test")
