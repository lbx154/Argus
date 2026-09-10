from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.trial import GLOBAL_TPM
from argus_skill.trial.store import Store, TrialError


def test_global_tpm_spans_keys_without_refunding_to_actual(tmp_path):
    now = [59.9]
    store = Store(tmp_path / "usage.db", clock=lambda: now[0])
    for i in range(10):
        store.issue(str(i), f"key-{i}")
    for i in range(10):
        request = store.reserve(str(i), 1_000_000)
        store.settle(request, 1)
    assert store.status("0")["global_tpm_limit"] == GLOBAL_TPM == 10_000_000
    assert store.status("0")["tokens_used"] == 1
    assert store.status("0")["global_tpm_reserved"] == GLOBAL_TPM
    now[0] = 60.1  # A new wall-clock minute does not replenish the rolling limit.
    with pytest.raises(TrialError) as error:
        store.reserve("0", 1)
    assert error.value.code == "trial_tpm_exceeded" and error.value.retry_after == 60
    assert store.status("0")["tokens_used"] == 1
    now[0] = 119.89
    with pytest.raises(TrialError):
        store.reserve("0", 1)
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
    assert store.status("trial-key")["global_tpm_reserved"] == 1000
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
    store = Store(tmp_path / "usage.db", clock=lambda: 100)
    for i in range(10):
        store.issue(str(i), f"key-{i}")

    def request(i):
        try:
            identifier = store.reserve(str(i % 10), 999_000)
            store.settle(identifier, 1)
            return "accepted"
        except TrialError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(40)))
    assert results.count("accepted") == 10
    assert results.count("trial_tpm_exceeded") + results.count("trial_quota_exceeded") == 30
    assert store.status("0")["global_tpm_reserved"] == 9_990_000


def test_zero_usage_auth_failure_refunds_both_budgets(tmp_path):
    store = Store(tmp_path / "usage.db")
    store.issue("trial-key", "key")
    request = store.reserve("trial-key", 1000)
    store.settle(request, 0)
    status = store.status("trial-key")
    assert status["global_tpm_reserved"] == status["tokens_used"] == 0
