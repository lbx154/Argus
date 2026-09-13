"""A stale heartbeat must not remove or overwrite a newly claimed owner."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from argus_skill.apps import _inbox, _inbox_delivery


def test_late_heartbeat_failure_cannot_poison_same_identity_new_owner(
    tmp_path, monkeypatch
):
    entered, release, new_renewed = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    old = SimpleNamespace(
        identity={
            "stream": "private",
            "generation": 1,
            "sequence": 1,
            "digest": "a" * 64,
        },
        owner="old-owner",
    )
    new = SimpleNamespace(identity=dict(old.identity), owner="new-owner")
    monkeypatch.setattr(_inbox_delivery, "LEASE_SECONDS", 0.03)

    def renew(root, value, **kwargs):
        if value.owner == old.owner:
            entered.set()
            assert release.wait(3)
            raise _inbox.InboxLeaseLost("old owner is no longer current")
        new_renewed.set()
        return value

    monkeypatch.setattr(_inbox, "renew_inbox_claim", renew)
    keeper = _inbox_delivery._ClaimKeeper(tmp_path)
    keeper.hold(old)
    worker = keeper.thread
    try:
        assert entered.wait(2)
        keeper.drop(old)
        keeper.hold(new)
        release.set()
        deadline = time.monotonic() + 2
        while (
            time.monotonic() < deadline
            and not new_renewed.is_set()
            and worker.is_alive()
        ):
            time.sleep(0.005)
        keeper.check(new)
        assert new_renewed.is_set(), (
            "The late old-owner heartbeat removed the new lease"
        )
    finally:
        release.set()
        keeper.drop(new)
        worker.join(2)
    assert not worker.is_alive()
