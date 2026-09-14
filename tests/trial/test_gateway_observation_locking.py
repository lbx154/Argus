"""Optional gateway observations must not stall cancellation or accounting."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
from cryptography.fernet import Fernet

from argus_skill.trial.gateway import Settings, create_app
from argus_skill.trial.gateway_observation import GatewayAttempt
from argus_skill.trial.secrets import Vault, write_private
from argus_skill.trial.store import Store


@contextmanager
def held_writer(path):
    held, release, finished = (threading.Event() for _ in range(3))

    def hold():
        try:
            with sqlite3.connect(path) as db:
                db.execute("BEGIN IMMEDIATE")
                held.set()
                release.wait(0.6)
        finally:
            finished.set()

    worker = threading.Thread(target=hold, daemon=True)
    worker.start()
    try:
        assert held.wait(2), "The real SQLite writer did not acquire its lock"
        yield finished
    finally:
        release.set()
        worker.join(2)
        assert not worker.is_alive()


def test_queued_request_cancels_before_observation_writer_releases(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    key = tmp_path / "key"
    write_private(key, Fernet.generate_key())
    Vault(key, state / "github-token.enc").save("offline-fixture-only")
    provider_calls = []

    def forbidden(request):
        provider_calls.append(str(request.url))
        raise AssertionError("A queued request cannot invoke the provider")

    async def run():
        app = create_app(Settings(state, key, timeout=2), transport=httpx.MockTransport(forbidden))
        async with app.router.lifespan_context(app):
            store = app.state.store
            credential = app.state.vault.credential("a" * 64)
            store.issue("a" * 64, credential)
            app.state.request_slots = asyncio.Semaphore(0)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                assert (await client.get("/healthz")).status_code == 200
                with held_writer(store.path) as writer_finished:
                    started = time.monotonic()
                    ticks = []
                    task = asyncio.create_task(client.post(
                        "/v1/chat/completions",
                        headers={"Authorization": "Bearer " + credential},
                        json={"model": "argus-trial", "messages": [{"role": "user", "content": "offline"}], "max_tokens": 10},
                    ))

                    def cancel():
                        ticks.append(time.monotonic() - started)
                        task.cancel()

                    timer = asyncio.get_running_loop().call_later(0.05, cancel)
                    try:
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    finally:
                        timer.cancel()
                    observation = {
                        "cancelled_after_seconds": time.monotonic() - started,
                        "event_loop_tick_seconds": ticks,
                        "writer_still_held": not writer_finished.is_set(),
                        "provider_calls": len(provider_calls),
                        "tokens_used": store.status("a" * 64)["tokens_used"],
                    }
                    (tmp_path / "observation-lock-timing.json").write_text(json.dumps(observation))
                    assert observation["tokens_used"] == 0 and not provider_calls
                    assert ticks and ticks[0] < 0.25, observation
                    assert observation["cancelled_after_seconds"] < 0.25, observation
                    assert observation["writer_still_held"], observation
            with sqlite3.connect(store.path) as db:
                assert db.execute("SELECT count(*) FROM trial_requests").fetchone()[0] == 0
                assert db.execute("SELECT count(*) FROM trial_gateway_attempts").fetchone()[0] == 0

    asyncio.run(run())


@pytest.mark.parametrize("terminal", [False, True])
def test_observation_update_contention_drops_only_optional_fields(tmp_path, terminal):
    store = Store(tmp_path / "usage.db")
    store.issue("key", "offline")
    attempt = GatewayAttempt(store, "key", 100)
    with held_writer(store.path) as writer_finished:
        started = time.monotonic()
        if terminal:
            attempt.finish("completed", selected_status=200)
        else:
            attempt.upstream()
        elapsed = time.monotonic() - started
        (tmp_path / "observation-lock-timing.json").write_text(json.dumps({
            "operation": "finish" if terminal else "update", "elapsed_seconds": elapsed,
            "writer_still_held": not writer_finished.is_set(),
        }))
        assert elapsed < 0.25 and not writer_finished.is_set()
    with sqlite3.connect(store.path) as db:
        phase, outcome, finished = db.execute("SELECT phase,outcome,finished_at FROM trial_gateway_attempts").fetchone()
    assert phase == "slot" and outcome is None and finished is None
    request = store.reserve("key", 100)
    store.settle(request, 15)
    assert store.status("key")["tokens_used"] == 15
    store.recover()
    with sqlite3.connect(store.path) as db:
        outcome, finished, recovered = db.execute("SELECT outcome,finished_at,recovered_at FROM trial_gateway_attempts").fetchone()
    assert outcome == "interrupted" and finished is None and recovered is not None


def test_observation_recovery_lock_contention_keeps_committed_accounting(tmp_path, monkeypatch):
    store = Store(tmp_path / "usage.db", clock=lambda: 200)
    store.issue("key", "offline")
    store.reserve("key", 100)
    store.begin_gateway_attempt("key", 100)
    transaction = store.transaction
    first = True
    lock = None
    writer_finished = None

    @contextmanager
    def after_accounting(*args, **kwargs):
        nonlocal first, lock, writer_finished
        with transaction(*args, **kwargs) as db:
            yield db
        if first:
            first = False
            lock = held_writer(store.path)
            writer_finished = lock.__enter__()

    monkeypatch.setattr(store, "transaction", after_accounting)
    try:
        started = time.monotonic()
        store.recover()
        elapsed = time.monotonic() - started
        (tmp_path / "observation-lock-timing.json").write_text(json.dumps({
            "operation": "recover", "elapsed_seconds": elapsed,
            "writer_still_held": writer_finished is not None and not writer_finished.is_set(),
        }))
        assert elapsed < 0.25 and writer_finished is not None and not writer_finished.is_set()
        assert store.status("key")["tokens_used"] == 100
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT state FROM trial_requests").fetchone()[0] == "interrupted"
            assert db.execute("SELECT retain_until FROM trial_tpm_reservations").fetchone()[0] == 260
            assert db.execute("SELECT outcome FROM trial_gateway_attempts").fetchone()[0] is None
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)
