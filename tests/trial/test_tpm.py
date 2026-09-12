from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.trial import GLOBAL_TPM
from argus_skill.trial.store import Store, TrialError


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
