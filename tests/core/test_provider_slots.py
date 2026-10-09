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
    interactive_run_label,
    provider_slot_wait_seconds,
    release_provider_slot,
)


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


def test_process_death_releases_slot_without_manual_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", "0")
    child = subprocess.Popen([sys.executable, "-c", '''
import sys, time
from pathlib import Path
from argus.core.provider_slots import acquire_provider_slot
slot, reason = acquire_provider_slot(Path(sys.argv[1]))
assert slot is not None and not reason
print('acquired', flush=True)
time.sleep(30)
''', str(tmp_path)], stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])})
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "acquired"
        slot, reason = acquire_provider_slot(tmp_path)
        assert slot is None and reason
    finally:
        child.terminate()
        child.wait(timeout=5)
        if child.stdout:
            child.stdout.close()
    slot, reason = acquire_provider_slot(tmp_path)
    assert slot is not None and not reason
    release_provider_slot(slot)


def test_caller_queues_for_a_busy_slot_instead_of_failing_at_once(tmp_path, monkeypatch):
    """A worker holding the only slot for a moment must not fail the lead's call."""
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", "5")
    holder, reason = acquire_provider_slot(tmp_path)
    assert holder is not None and not reason

    def release_soon():
        time.sleep(0.6)
        release_provider_slot(holder)

    threading.Thread(target=release_soon, daemon=True).start()
    started = time.monotonic()
    slot, reason = acquire_provider_slot(tmp_path)
    waited = time.monotonic() - started
    try:
        assert slot is not None and not reason
        assert 0.4 <= waited < 4.0
    finally:
        release_provider_slot(slot)


def test_queue_gives_up_after_the_configured_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", "0.5")
    holder, _ = acquire_provider_slot(tmp_path)
    try:
        started = time.monotonic()
        slot, reason = acquire_provider_slot(tmp_path)
        waited = time.monotonic() - started
        assert slot is None and "concurrency limit" in reason
        assert 0.4 <= waited < 3.0
    finally:
        release_provider_slot(holder)


def test_default_wait_is_bounded_and_operator_tunable(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", raising=False)
    assert 0 < provider_slot_wait_seconds() <= 120
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", "99999")
    assert provider_slot_wait_seconds() == 600


def test_a_chat_reply_has_one_slot_beyond_the_cap(tmp_path, monkeypatch):
    """Background work holding every slot must not turn away the person typing."""
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS", "0")
    holder, _ = acquire_provider_slot(tmp_path)
    try:
        assert acquire_provider_slot(tmp_path)[0] is None
        chat, reason = acquire_provider_slot(tmp_path, interactive=True)
        assert chat is not None and not reason
        try:
            # One extra slot, not an open door.
            assert acquire_provider_slot(tmp_path, interactive=True)[0] is None
        finally:
            release_provider_slot(chat)
    finally:
        release_provider_slot(holder)
    assert interactive_run_label("manager-frontdoor-classify")
    assert interactive_run_label("simple-1")
    assert not interactive_run_label("engineer-r1")
    assert not interactive_run_label("map-summary")
