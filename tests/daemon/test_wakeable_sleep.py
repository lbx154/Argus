"""Cut #1 (daemon side): the inter-pass sleep is wakeable.

``LifeWorker._wakeable_sleep`` must return promptly on a stop request and on
fresh user input (a growing ``inbox.jsonl``), so a long await-external backoff
never makes ``/add`` / ``/nudge`` unresponsive.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from argus_skill.daemon.life_worker import LifeWorker
from argus_skill.life.memory import BacklogItem, LifeMemory


def _worker(tmp_path) -> LifeWorker:
    w = LifeWorker.__new__(LifeWorker)
    w._stop = threading.Event()
    w.config = SimpleNamespace(global_root=tmp_path)
    return w


def test_wakeable_sleep_returns_on_stop(tmp_path) -> None:
    w = _worker(tmp_path)
    w._stop.set()
    t0 = time.monotonic()
    w._wakeable_sleep(60.0, 5.0, tmp_path)
    assert time.monotonic() - t0 < 1.0


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


def test_wakeable_sleep_ignores_fully_consumed_inbox(tmp_path) -> None:
    w = _worker(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text('{"msg": "old"}\n', encoding="utf-8")
    (tmp_path / "inbox.offset").write_text(str(inbox.stat().st_size), encoding="utf-8")

    t0 = time.monotonic()
    w._wakeable_sleep(0.4, 0.1, tmp_path)

    assert time.monotonic() - t0 >= 0.35


def test_wakeable_sleep_sleeps_full_when_quiet(tmp_path) -> None:
    w = _worker(tmp_path)
    t0 = time.monotonic()
    w._wakeable_sleep(0.4, 0.1, tmp_path)
    assert time.monotonic() - t0 >= 0.35


@pytest.mark.parametrize("resume_before_sleep", [False, True])
def test_provider_fence_sleep_wakes_on_explicit_resume(
    tmp_path, resume_before_sleep: bool,
) -> None:
    worker = _worker(tmp_path)
    memory = LifeMemory.open(tmp_path)
    item = BacklogItem.new(
        title="quota held",
        objective="resume after recovery",
        manager_decision={"routed": True, "vertical": "software"},
    )
    item.status = "paused_provider_fence"
    memory.backlog.add(item)
    waits = []

    def resume_during_wait(timeout):
        waits.append(timeout)
        memory.backlog.resume_all_paused()
        return False

    worker._stop.wait = resume_during_wait
    if resume_before_sleep:
        memory.backlog.resume_all_paused()

    worker._wakeable_sleep(
        300.0, 0.1, tmp_path, wake_on_ready_work=True,
    )

    assert len(waits) == (0 if resume_before_sleep else 1)
    assert memory.backlog.all()[0].status == "pending"
    assert memory.backlog.all()[0].attempt == 2
    assert not (tmp_path / "inbox.jsonl").exists()


@pytest.mark.parametrize("status", ["paused_provider_fence", "pending"])
def test_recovery_wake_does_not_bypass_unchanged_fence_or_budget_backoff(
    tmp_path, status: str,
) -> None:
    worker = _worker(tmp_path)
    memory = LifeMemory.open(tmp_path)
    item = BacklogItem.new(
        title="held work",
        objective="wait",
        manager_decision={"routed": True, "vertical": "software"},
    )
    item.status = status
    memory.backlog.add(item)
    waits = []
    worker._stop.wait = lambda timeout: waits.append(timeout) or False

    worker._wakeable_sleep(
        2.0, 0.5, tmp_path,
        wake_on_ready_work=status == "paused_provider_fence",
    )

    assert waits == [0.5] * 4
    assert memory.backlog.all()[0].status == status
    assert memory.backlog.all()[0].attempt == 1


@pytest.mark.parametrize("existing_config", [False, True])
def test_wakeable_sleep_wakes_when_operator_changes_budget(
    tmp_path, existing_config: bool,
) -> None:
    w = _worker(tmp_path)
    config = tmp_path / "config.json"
    if existing_config:
        config.write_text('{"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "1000"}')

    def remove_cap() -> None:
        time.sleep(0.2)
        temporary = tmp_path / "config.tmp"
        temporary.write_text('{"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "0"}')
        temporary.replace(config)

    updater = threading.Thread(target=remove_cap, daemon=True)
    updater.start()
    start = time.monotonic()
    w._wakeable_sleep(5.0, 0.1, tmp_path)
    elapsed = time.monotonic() - start
    updater.join(timeout=1)

    assert not updater.is_alive()
    assert elapsed < 2.0
    assert not (tmp_path / "inbox.jsonl").exists()
