"""Cut #1 (daemon side): the inter-pass sleep is wakeable.

``LifeWorker._wakeable_sleep`` must return promptly on a stop request and on
fresh user input (a growing ``inbox.jsonl``), so a long await-external backoff
never makes ``/add`` / ``/nudge`` unresponsive.
"""
from __future__ import annotations

import threading
import time

from argus_skill.daemon.life_worker import LifeWorker


def _worker() -> LifeWorker:
    w = LifeWorker.__new__(LifeWorker)
    w._stop = threading.Event()
    return w


def test_wakeable_sleep_returns_on_stop(tmp_path) -> None:
    w = _worker()
    w._stop.set()
    t0 = time.monotonic()
    w._wakeable_sleep(60.0, 5.0, tmp_path)
    assert time.monotonic() - t0 < 1.0


def test_wakeable_sleep_wakes_on_inbox_growth(tmp_path) -> None:
    w = _worker()
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


def test_wakeable_sleep_sleeps_full_when_quiet(tmp_path) -> None:
    w = _worker()
    t0 = time.monotonic()
    w._wakeable_sleep(0.4, 0.1, tmp_path)
    assert time.monotonic() - t0 >= 0.35
