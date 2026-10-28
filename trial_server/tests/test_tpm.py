
import pytest

from argus_trial_server.store import Store, TrialError


def test_long_active_requests_hold_tpm_until_a_minute_after_finish(tmp_path):
    now = [0.0]
    store = Store(tmp_path / "usage.db", clock=lambda: now[0])
    store.issue("trial-key", "key")
    request = store.reserve("trial-key", 1000)
    now[0] = 300
    assert store.status("trial-key")["global_tpm_reserved"] == 1000
    store.settle(request, 3)
    now[0] = 359.9
    assert store.status("trial-key")["global_tpm_reserved"] == 3
    now[0] = 360
    assert store.status("trial-key")["global_tpm_reserved"] == 0


def test_restart_preserves_tpm_and_unknown_usage(tmp_path):
    now = [100.0]
    path = tmp_path / "usage.db"
    store = Store(path, clock=lambda: now[0])
    store.issue("trial-key", "key")
    store.reserve("trial-key", 1000)
    now[0] = 200
    restarted = Store(path, clock=lambda: now[0])
    restarted.recover()
    status = restarted.status("trial-key")
    assert status["active_requests"] == 0
    assert status["tokens_used"] == status["global_tpm_reserved"] == 1000
    now[0] = 259.9
    restarted.recover()
    assert restarted.status("trial-key")["global_tpm_reserved"] == 1000
    now[0] = 260
    assert restarted.status("trial-key")["global_tpm_reserved"] == 0


def test_unknown_usage_retains_estimate_and_reported_usage_can_exceed_it(tmp_path):
    store = Store(tmp_path / "usage.db", clock=lambda: 100)
    store.issue("trial-key", "key")
    uncertain = store.reserve("trial-key", 1000)
    store.settle(uncertain, None)
    known = store.reserve("trial-key", 100)
    store.settle(known, 200)
    assert store.status("trial-key")["global_tpm_reserved"] == 1200
    assert store.status("trial-key")["tokens_used"] == 1200


def test_restart_reconciles_old_completed_estimates_without_resetting_window(tmp_path):
    now = [100.0]
    path = tmp_path / "usage.db"
    store = Store(path, clock=lambda: now[0])
    store.issue("trial-key", "key")
    known = store.reserve("trial-key", 1000)
    store.settle(known, 20)
    uncertain = store.reserve("trial-key", 2000)
    store.settle(uncertain, None)
    with store.transaction() as db:
        db.execute("UPDATE trial_tpm_reservations SET tokens=1000 WHERE request_id=?", (known,))
    now[0] = 150
    restarted = Store(path, clock=lambda: now[0])
    restarted.recover()
    assert restarted.status("trial-key")["tokens_used"] == 2020
    assert restarted.status("trial-key")["global_tpm_reserved"] == 2020
    now[0] = 160
    assert restarted.status("trial-key")["global_tpm_reserved"] == 0


def test_retry_after_waits_until_enough_tokens_expire(tmp_path):
    now = [0.0]
    store = Store(tmp_path / "usage.db", clock=lambda: now[0], token_limit=None)
    store.issue("trial-key", "key")
    first = store.reserve("trial-key", 1_000_000)
    store.settle(first, 1_000_000)
    now[0] = 10
    second = store.reserve("trial-key", 9_000_000)
    store.settle(second, 9_000_000)
    now[0] = 50
    with pytest.raises(TrialError) as error:
        store.reserve("trial-key", 2_000_000)
    assert error.value.retry_after == 20


def test_zero_usage_auth_failure_refunds_both_budgets(tmp_path):
    store = Store(tmp_path / "usage.db")
    store.issue("trial-key", "key")
    request = store.reserve("trial-key", 1000)
    store.settle(request, 0)
    status = store.status("trial-key")
    assert status["global_tpm_reserved"] == status["tokens_used"] == 0


def test_gateway_terminal_observation_is_idempotent_and_recovery_keeps_end_time_unknown(tmp_path):
    now = [100.0]
    store = Store(tmp_path / "usage.db", clock=lambda: now[0])
    store.issue("trial-key", "key")
    pending = store.begin_gateway_attempt("trial-key", 1000)
    finished = store.begin_gateway_attempt("trial-key", 1000)
    store.update_gateway_attempt(pending, phase="tpm", queue_reason="tpm")
    store.update_gateway_attempt(finished, outcome="rejected", finished_at=101,
                                 selected_response_status=429, client_error_code="trial_busy")
    store.update_gateway_attempt(finished, outcome="completed", finished_at=102, selected_response_status=200)
    now[0] = 200
    store.recover()
    now[0] = 300
    store.recover()
    with store.transaction() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM trial_gateway_attempts ORDER BY id")]
        assert db.execute("SELECT COUNT(*) FROM trial_requests").fetchone()[0] == 0
    assert rows[0]["outcome"] == "interrupted" and rows[0]["recovered_at"] == 200
    assert rows[0]["phase"] == "tpm" and rows[0]["queue_reason"] == "tpm"
    assert rows[0]["finished_at"] is None and rows[0]["upstream_status"] is None
    assert rows[0]["selected_response_status"] is None and rows[0]["reservation_id"] is None
    assert rows[1]["outcome"] == "rejected" and rows[1]["finished_at"] == 101
    assert rows[1]["selected_response_status"] == 429 and rows[1]["client_error_code"] == "trial_busy"
    assert rows[1]["recovered_at"] is None


def test_observation_recovery_failure_cannot_roll_back_accounting_recovery(tmp_path):
    store = Store(tmp_path / "usage.db", clock=lambda: 200)
    store.issue("trial-key", "key")
    request = store.reserve("trial-key", 1000)
    with store.transaction() as db:
        db.execute("DROP TABLE trial_gateway_attempts")
    store.recover()
    status = store.status("trial-key")
    assert status["active_requests"] == 0 and status["tokens_used"] == status["global_tpm_reserved"] == 1000
    with store.transaction() as db:
        assert db.execute("SELECT state FROM trial_requests WHERE id=?", (request,)).fetchone()[0] == "interrupted"
        assert db.execute("SELECT retain_until FROM trial_tpm_reservations WHERE request_id=?", (request,)).fetchone()[0] == 260
