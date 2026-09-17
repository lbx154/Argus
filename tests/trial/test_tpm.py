import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus.trial import GLOBAL_TPM
from argus.trial.store import Store, TrialError


def test_completed_calls_count_reported_tokens_across_keys(tmp_path):
    store = Store(tmp_path / "usage.db", clock=lambda: 100)
    for i in range(10):
        store.issue(str(i), f"key-{i}")
        request = store.reserve(str(i), 1_000_000)
        store.settle(request, 1)
    assert store.status("0")["global_tpm_reserved"] == 10
    assert store.status("0")["tokens_used"] == 1
    # Large contexts must not keep blocking other agents after usage is known.
    request = store.reserve("0", 999_999)
    store.settle(request, 2)
    assert store.status("0")["global_tpm_reserved"] == 12
    assert store.status("0")["tokens_used"] == 3


def test_global_tpm_spans_keys_and_keeps_real_usage_for_a_rolling_minute(tmp_path):
    now = [59.9]
    store = Store(tmp_path / "usage.db", clock=lambda: now[0], token_limit=None)
    for i in range(10):
        store.issue(str(i), f"key-{i}")
    for i in range(10):
        request = store.reserve(str(i), 1_000_000)
        store.settle(request, 1_000_000)
    assert store.status("0")["global_tpm_limit"] == GLOBAL_TPM == 10_000_000
    assert store.status("0")["tokens_used"] == 1_000_000
    assert store.status("0")["global_tpm_reserved"] == GLOBAL_TPM
    now[0] = 60.1  # A new wall-clock minute does not replenish the rolling limit.
    with pytest.raises(TrialError) as error:
        store.reserve("0", 1)
    assert error.value.code == "trial_tpm_exceeded" and error.value.retry_after == 60
    assert store.status("0")["tokens_used"] == 1_000_000
    now[0] = 119.89
    with pytest.raises(TrialError) as error:
        store.reserve("0", 1)
    assert error.value.retry_after == 1
    now[0] = 119.91
    assert store.reserve("0", 999_999)


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


def test_global_tpm_admission_is_atomic(tmp_path):
    store = Store(tmp_path / "usage.db", clock=lambda: 100, token_limit=None)
    for i in range(10):
        store.issue(str(i), f"key-{i}")

    def request(i):
        try:
            identifier = store.reserve(str(i % 10), 999_000)
            store.settle(identifier, 999_000)
            return "accepted"
        except TrialError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(40)))
    assert results.count("accepted") == 10
    assert results.count("trial_tpm_exceeded") + results.count("trial_quota_exceeded") == 30
    assert store.status("0")["global_tpm_reserved"] == 9_990_000


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


def test_gateway_observation_does_not_invent_legacy_times_or_statuses(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE trial_keys (
                key_id TEXT PRIMARY KEY, credential_hash TEXT UNIQUE NOT NULL,
                used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0), claim_hash TEXT UNIQUE
            );
            CREATE TABLE trial_requests (
                id INTEGER PRIMARY KEY, key_id TEXT NOT NULL REFERENCES trial_keys(key_id),
                reserved INTEGER NOT NULL, charged INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'active'
            );
            INSERT INTO trial_keys VALUES ('legacy', 'existing-credential-hash', 120, NULL);
            INSERT INTO trial_requests VALUES (1, 'legacy', 1000, 20, 'settled');
            INSERT INTO trial_requests VALUES (2, 'legacy', 100, 100, 'active');
        """)
        original_schema = db.execute("SELECT sql FROM sqlite_master WHERE name='trial_requests'").fetchone()[0]
    store = Store(path, clock=lambda: 200)
    store.recover()
    with store.transaction() as db:
        assert db.execute("SELECT sql FROM sqlite_master WHERE name='trial_requests'").fetchone()[0] == original_schema
        assert [tuple(row) for row in db.execute("SELECT * FROM trial_requests ORDER BY id")] == [
            (1, "legacy", 1000, 20, "settled"), (2, "legacy", 100, 100, "interrupted"),
        ]
        assert db.execute("SELECT COUNT(*) FROM trial_gateway_attempts").fetchone()[0] == 0
    assert store.status("legacy")["tokens_used"] == 120


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
