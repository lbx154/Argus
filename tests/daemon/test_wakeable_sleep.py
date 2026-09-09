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
