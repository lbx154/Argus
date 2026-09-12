import pytest

from argus_skill.trial.store import Store, TrialError


def test_tester_identity_is_stable_and_not_the_invitation(tmp_path):
    store = Store(tmp_path / "usage.sqlite3")
    store.issue("trial-01", "invitation-one")
    store.issue("trial-02", "invitation-two")
    identity = store.tester_id("trial-01")
    assert identity.startswith("tester_")
    assert identity != "invitation-one"
    assert Store(store.path).tester_id("trial-01") == identity
    assert store.tester_id("trial-02") != identity


def test_disabled_or_expired_invites_cannot_authenticate_or_reserve(tmp_path):
    now = [100]
    store = Store(tmp_path / "usage.sqlite3", clock=lambda: now[0])
    store.issue("trial-01", "invitation")
    store.set_access("trial-01", enabled=False)
    for operation in (lambda: store.authenticate("invitation"), lambda: store.reserve("trial-01", 1)):
        with pytest.raises(TrialError) as error:
            operation()
        assert error.value.code == "trial_access_disabled"
    store.set_access("trial-01", enabled=True, expires_at=110)
    assert store.authenticate("invitation") == "trial-01"
    now[0] = 110
    with pytest.raises(TrialError):
        store.check_access("trial-01")
    assert store.status("trial-01")["tokens_used"] == 0


def test_usage_distinguishes_settlement_reservation_and_uncertainty(tmp_path):
    store = Store(tmp_path / "usage.sqlite3")
    store.issue("trial-01", "invitation")
    settled = store.reserve("trial-01", 50)
    store.settle(settled, 10)
    store.reserve("trial-01", 30)
    unknown = store.reserve("trial-01", 20)
    store.settle(unknown, None)
    status = store.status("trial-01")
    assert status["tokens_used"] == 60
    assert status["tokens_settled"] == 10
    assert status["tokens_reserved"] == 30
    assert status["tokens_uncertain"] == 20
    assert status["tokens_unattributed"] == 0


def test_unlimited_mode_preserves_real_usage_beyond_previous_cap(tmp_path):
    now = [100]
    store = Store(tmp_path / "usage.sqlite3", token_limit=None, clock=lambda: now[0])
    store.issue("trial-01", "invitation")
    request = store.reserve("trial-01", 1)
    store.settle(request, 10_000_001)
    now[0] += 61
    request = store.reserve("trial-01", 100)
    store.settle(request, 3)
    status = store.status("trial-01")
    assert status["tokens_used"] == 10_000_004
    assert status["token_unlimited"] is True
    assert status["token_limit"] is None and status["tokens_remaining"] is None
    assert Store(store.path, token_limit=None).status("trial-01")["tokens_used"] == 10_000_004
    assert Store(tmp_path / "desktop.sqlite3").token_limit == 1_000_000
