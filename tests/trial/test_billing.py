import time

import pytest

from argus_skill.core.cost_control import reserve_call_budget
from argus_skill.core.token_usage import TokenUsage
from argus_skill.core.usage import UsageLedger, build_usage_record


def test_trial_usage_reads_its_own_cli_store(tmp_path, monkeypatch):
    from argus_skill.provider_integrations.copilot_usage import (
        capture_copilot_usage_cursor,
        copilot_usage_db_candidates,
    )

    monkeypatch.setenv("ARGUS_SKILL_COPILOT_TRIAL", "1")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("COPILOT_HOME", "/unrelated/personal/copilot")
    expected = tmp_path / "copilot-trial-home/session-store.db"
    assert copilot_usage_db_candidates() == [expected]
    cursor = capture_copilot_usage_cursor()
    assert cursor.db_path == expected and cursor.fallback is None


@pytest.mark.parametrize("hosted_trial", [True, False])
def test_trial_preserves_tokens_without_waiting_for_a_user_copilot_bill(tmp_path, hosted_trial):
    project = tmp_path / "project"
    record = build_usage_record(
        call_id="setup", project_root=project, mission_id=None, provider="copilot",
        model="argus-trial", run_label="setup", started_at=time.time() - 1,
        completed_at=time.time(), status="completed",
        token_usage=TokenUsage(input_tokens=100, output_tokens=20,
                               input_tokens_present=True, output_tokens_present=True),
        copilot_token_billing_expected=True, hosted_trial=hosted_trial,
    )
    assert record.input_tokens == 100 and record.output_tokens == 20
    if hosted_trial:
        assert record.cost_usd == 0 and record.pricing_status == "not_billed"
        assert record.pricing_tier == "hosted_trial"
    else:
        assert record.cost_usd is None and record.pricing_status == "partial"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(record)
    ledger.ensure_copilot_usage_reconciled()
    reservation, reason = reserve_call_budget(
        call_id="next", project_root=project, mission_id=None, provider="copilot",
        model="argus-trial", run_label="next", global_root=tmp_path / "argus",
    )
    if hosted_trial:
        assert reservation is not None and not reason
        reservation.release(reason="test_complete")
    else:
        assert reservation is None and "unresolved provider cost" in reason
