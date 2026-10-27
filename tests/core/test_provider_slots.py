from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from argus.core.provider_slots import (
    acquire_provider_slot,
    provider_call_priority,
    release_provider_slot,
)


def _wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_waiters_keep_fifo_and_user_messages_precede_map_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "1")
    holder, _ = acquire_provider_slot(tmp_path)
    order = []

    def call(label):
        slot, reason = acquire_provider_slot(tmp_path, wait_seconds=5,
                                             priority=provider_call_priority(label))
        try:
            assert slot and not reason
            order.append(label)
        finally:
            release_provider_slot(slot)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = []
        try:
            for index, label in enumerate(["map-summary", "engineer-r1", "manager-frontdoor-1", "manager-frontdoor-2"]):
                futures.append(pool.submit(call, label))
                _wait_until(lambda: len(list((tmp_path / "provider-slots").glob("wait-*.lock"))) == index + 1)
        finally:
            release_provider_slot(holder)
        for future in futures:
            future.result(timeout=6)
    assert order == ["manager-frontdoor-1", "manager-frontdoor-2", "engineer-r1", "map-summary"]
    assert not list((tmp_path / "provider-slots").glob("wait-*.lock"))


def test_typing_suggestions_use_only_idle_capacity():
    assert provider_call_priority("prompt-complete") == provider_call_priority("map-summary") == 2


def test_cancelled_waiter_releases_its_place_promptly(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "1")
    holder, _ = acquire_provider_slot(tmp_path)
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(acquire_provider_slot, tmp_path, wait_seconds=30,
                             interrupt=lambda: "cancelled" if cancel.is_set() else None)
        try:
            _wait_until(lambda: bool(list((tmp_path / "provider-slots").glob("wait-*.lock"))))
            cancel.set()
            slot, reason = future.result(timeout=2)
            assert slot is None and reason == "External interrupt: cancelled"
            assert not list((tmp_path / "provider-slots").glob("wait-*.lock"))
        finally:
            release_provider_slot(holder)


def test_dead_waiter_does_not_block_the_next_process(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "1")
    holder, _ = acquire_provider_slot(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", '''
import sys
from pathlib import Path
from argus.core.provider_slots import acquire_provider_slot
acquire_provider_slot(Path(sys.argv[1]), wait_seconds=30)
''', str(tmp_path)], env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])})
    try:
        _wait_until(lambda: bool(list((tmp_path / "provider-slots").glob("wait-*.lock"))))
    finally:
        child.terminate()
        child.wait(timeout=5)
        release_provider_slot(holder)
    slot, reason = acquire_provider_slot(tmp_path, wait_seconds=1)
    assert slot and not reason
    release_provider_slot(slot)
    assert not list((tmp_path / "provider-slots").glob("wait-*.lock"))


def test_burst_never_exceeds_shared_process_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", "0")
    release = threading.Event()
    all_attempted = threading.Event()
    lock = threading.Lock()
    attempted = accepted = 0

    def attempt():
        nonlocal attempted, accepted
        slot, reason = acquire_provider_slot(tmp_path)
        with lock:
            attempted += 1
            accepted += bool(slot)
            if attempted == 24:
                all_attempted.set()
        try:
            if slot:
                assert release.wait(5)
                assert not reason
            else:
                assert "concurrency limit" in reason
        finally:
            release_provider_slot(slot)

    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(attempt) for _ in range(24)]
        try:
            assert all_attempted.wait(5)
            assert accepted == 2
        finally:
            release.set()
        for future in futures:
            future.result()
    slot, reason = acquire_provider_slot(tmp_path)
    assert slot and not reason
    release_provider_slot(slot)
