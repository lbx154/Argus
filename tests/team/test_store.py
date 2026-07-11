from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from argus_skill.team import _store


def test_atomic_write_then_read_roundtrips(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "data.json"
    _store.atomic_write_json(p, {"a": 1, "b": ["x"]})
    assert _store.read_json(p) == {"a": 1, "b": ["x"]}


def test_read_json_missing_returns_default(tmp_path: Path) -> None:
    assert _store.read_json(tmp_path / "nope.json", default={"d": True}) == {"d": True}


def test_atomic_write_leaves_no_tmp_and_overwrites(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    _store.atomic_write_json(p, {"v": 1})
    _store.atomic_write_json(p, {"v": 2})
    assert _store.read_json(p) == {"v": 2}
    assert list(p.parent.glob(".tmp-*")) == []


def _locked_incr(lock: str, counter: str) -> None:
    from pathlib import Path as P

    from argus_skill.team import _store as s
    for _ in range(50):
        with s.locked(P(lock)):
            cur = s.read_json(P(counter), default={"n": 0})
            cur["n"] += 1
            s.atomic_write_json(P(counter), cur)


@pytest.mark.integration
def test_locked_serializes_concurrent_writers(tmp_path: Path) -> None:
    lock = str(tmp_path / ".lock")
    counter = str(tmp_path / "counter.json")
    procs = [mp.Process(target=_locked_incr, args=(lock, counter)) for _ in range(4)]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join()
    assert _store.read_json(Path(counter)) == {"n": 200}
