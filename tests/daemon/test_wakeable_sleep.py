"""Cut #1 (daemon side): the inter-pass sleep is wakeable.

``LifeWorker._wakeable_sleep`` must return promptly on a stop request, on
fresh user input (a growing ``inbox.jsonl``) and on someone else's backlog
write, so a long backoff never makes ``/add``, ``/nudge`` or an explicit Run
unresponsive.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from argus.daemon.life_worker import LifeWorker


def _worker(tmp_path) -> LifeWorker:
    w = LifeWorker.__new__(LifeWorker)
    w._stop = threading.Event()
    w.config = SimpleNamespace(global_root=tmp_path)
    return w


def test_wakeable_sleep_wakes_on_inbox_growth(tmp_path) -> None:
    w = _worker(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text("", encoding="utf-8")

    def _grow() -> None:
        time.sleep(0.3)
        with inbox.open("a", encoding="utf-8") as fh:
            fh.write('{"msg": "hi"}\n')

    threading.Thread(target=_grow, daemon=True).start()
    t0 = time.monotonic()
    # Poll interval small so the inbox is checked frequently.
    w._wakeable_sleep(60.0, 0.1, tmp_path)
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0  # returned early due to inbox growth, not full 60s


def test_wakeable_sleep_returns_when_inbox_is_already_pending(tmp_path) -> None:
    w = _worker(tmp_path)
    (tmp_path / "inbox.jsonl").write_text('{"msg": "hi"}\n', encoding="utf-8")

    t0 = time.monotonic()
    w._wakeable_sleep(60.0, 5.0, tmp_path)

    assert time.monotonic() - t0 < 1.0


def test_durable_input_wakes_once_and_new_input_interrupts_retained_envelope_wait(tmp_path):
    from argus.apps._inbox import queue_inbox_message

    worker = _worker(tmp_path)
    queue_inbox_message(tmp_path, "A retained instruction", source="test")
    waits = []
    worker._stop.wait = lambda timeout: waits.append(timeout) or False
    worker._wakeable_sleep(2, 0.5, tmp_path)
    assert waits == []
    # Pending retry envelopes must not turn the daemon's backoff into a spin.
    worker._wakeable_sleep(2, 0.5, tmp_path)
    assert waits == [0.5] * 4
    waits.clear()

    def enqueue_during_wait(timeout):
        waits.append(timeout)
        queue_inbox_message(tmp_path, "A new instruction", source="test")
        return False

    worker._stop.wait = enqueue_during_wait
    worker._wakeable_sleep(60, 0.5, tmp_path)
    assert waits == [0.5]


def test_an_explicit_run_wakes_the_sleep_instead_of_waiting_out_the_backoff(tmp_path) -> None:
    from argus.life.memory import BacklogItem, LifeMemory
    from argus.webapi.routes.daemon import _resume_parks_after_start

    worker = _worker(tmp_path)
    memory = LifeMemory.open(tmp_path)
    item = memory.backlog.add(BacklogItem.new(title="fenced", objective="resume after recovery"))
    memory.backlog.update(item.id, park_reason="provider_fence")
    supervisor = SimpleNamespace(memory=memory)
    waits: list[float] = []

    def run_during_wait(timeout):
        waits.append(timeout)
        if len(waits) == 2:
            _resume_parks_after_start(tmp_path, {"rc": 0})
        return False

    worker._stop.wait = run_during_wait
    worker._wakeable_sleep(300.0, 0.5, tmp_path, supervisor=supervisor)

    # The Run's backlog write ended a 300-second backoff after one slice.
    assert waits == [0.5, 0.5]
    assert memory.backlog.all()[0].status == "pending"
    # With nothing new the backoff is slept out in full.
    waits.clear()
    worker._stop.wait = lambda timeout: waits.append(timeout) or False
    worker._wakeable_sleep(2.0, 0.5, tmp_path, supervisor=supervisor)
    assert waits == [0.5] * 4


@pytest.mark.parametrize("existing_config", [False, True])
def test_wakeable_sleep_wakes_when_operator_changes_config(
    tmp_path, existing_config: bool,
) -> None:
    w = _worker(tmp_path)
    config = tmp_path / "config.json"
    if existing_config:
        config.write_text('{"ARGUS_SKILL_MODEL": "model-a"}')

    def change_config() -> None:
        time.sleep(0.2)
        temporary = tmp_path / "config.tmp"
        temporary.write_text('{"ARGUS_SKILL_MODEL": "model-b"}')
        temporary.replace(config)

    updater = threading.Thread(target=change_config, daemon=True)
    updater.start()
    start = time.monotonic()
    w._wakeable_sleep(5.0, 0.1, tmp_path)
    elapsed = time.monotonic() - start
    updater.join(timeout=1)

    assert not updater.is_alive()
    assert elapsed < 2.0
    assert not (tmp_path / "inbox.jsonl").exists()
