from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.team import task_board as tb


def _form(root: Path) -> None:
    tb.form(root, [
        {"task_id": "a", "title": "A", "objective": "do a", "owns_paths": ["a/**"]},
        {
            "task_id": "b",
            "title": "B",
            "objective": "do b",
            "owns_paths": ["b/**"],
            "deps": ["a"],
        },
    ])


def test_claim_top_returns_pending_and_flips_state(tmp_path: Path) -> None:
    _form(tmp_path)
    got = tb.claim_top(tmp_path, "tm-1", now=100.0)
    assert got is not None and got["task_id"] == "a"
    assert got["owner"] == "tm-1" and got["state"] == "claimed"


def test_dependency_blocks_claim_until_done(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim_top(tmp_path, "tm-1", now=1.0)
    assert tb.claim_top(tmp_path, "tm-2", now=2.0) is None
    tb.complete(tmp_path, "a", shard="shards/a.jsonl")
    got = tb.claim_top(tmp_path, "tm-2", now=3.0)
    assert got is not None and got["task_id"] == "b"
    first = {task["task_id"]: task for task in tb.snapshot(tmp_path)}["a"]
    assert first["claim_seq"] < first["finish_seq"] < got["claim_seq"]


def test_claim_top_never_double_claims(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "x"}])
    first = tb.claim_top(tmp_path, "tm-1", now=1.0)
    second = tb.claim_top(tmp_path, "tm-2", now=1.0)
    assert first is not None and first["task_id"] == "a"
    assert second is None


def test_reassign_stale_returns_to_pending(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim_top(tmp_path, "tm-1", now=1.0)
    tb.heartbeat(tmp_path, "a", now=1.0)
    reassigned = tb.reassign_stale(tmp_path, ttl=10.0, now=100.0)
    assert reassigned == ["a"]
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["state"] == "pending" and snap["a"]["attempts"] == 1

    tb.claim_top(tmp_path, "tm-2", now=200.0)
    tb.heartbeat(tmp_path, "a", now=205.0)
    assert tb.reassign_stale(tmp_path, ttl=100.0, now=210.0) == []


@pytest.mark.parametrize("terminal_state", ["done", "failed"])
def test_retry_terminal_returns_task_to_pending(
    tmp_path: Path,
    terminal_state: str,
) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "x"}])
    tb.claim_top(tmp_path, "tm-1", now=1.0)
    if terminal_state == "done":
        tb.complete(tmp_path, "a", shard="shards/a.jsonl")
    else:
        tb.fail(tmp_path, "a", reason="invalid output")

    assert tb.retry_terminal(tmp_path, "a") is True
    task = tb.snapshot(tmp_path)[0]
    assert task["state"] == "pending"
    assert task["owner"] == ""
    assert task["result_shard"] == ""
    assert task["finished_ts"] == 0.0
    assert task["attempts"] == 1
    assert tb.retry_terminal(tmp_path, "a") is False


@pytest.mark.parametrize("task_id", ["", ".", "..", "../escape", "nested/task", r"nested\task"])
def test_task_ids_cannot_escape_task_storage(tmp_path: Path, task_id: str) -> None:
    with pytest.raises(ValueError, match="invalid task_id"):
        tb.form(tmp_path, [{"task_id": task_id, "objective": "bad"}])
    assert not (tmp_path / "escape.json").exists()
    assert not any((tmp_path / "tasks").glob("*.json"))


def test_windows_unsafe_logical_id_round_trips_without_unsafe_filename(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "team::task", "objective": "portable"}])

    assert [task["task_id"] for task in tb.snapshot(tmp_path)] == ["team::task"]
    if __import__("os").name == "nt":
        assert all(":" not in path.name for path in (tmp_path / "tasks").iterdir())


def test_legacy_hashed_task_record_migrates_without_duplicate_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    encode = tb.portable_filename_component
    monkeypatch.setattr(
        tb,
        "portable_filename_component",
        lambda value, *, windows=None: encode(value, windows=True),
    )
    task_id = "team::task"
    legacy = tb._legacy_paths(tmp_path, task_id)[0]
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        '{"task_id":"team::task","state":"claimed","owner":"w1",'
        '"claim_ts":1,"heartbeat_ts":1,"attempts":0,"deps":[]}\n',
        encoding="utf-8",
    )

    tb.form(tmp_path, [{"task_id": task_id, "objective": "portable"}])

    snapshot = tb.snapshot(tmp_path)
    assert len(snapshot) == 1
    assert snapshot[0]["state"] == "claimed"
    assert tb._path(tmp_path, task_id).exists()
    assert not legacy.exists()

    canonical = tb._path(tmp_path, task_id)
    legacy.write_text(
        '{"task_id":"team::task","state":"done","owner":"w1",'
        '"claim_ts":1,"heartbeat_ts":2,"attempts":0,"deps":[]}\n',
        encoding="utf-8",
    )
    canonical_mtime = canonical.stat().st_mtime_ns
    __import__("os").utime(legacy, ns=(canonical_mtime + 1, canonical_mtime + 1))

    assert tb.snapshot(tmp_path)[0]["state"] == "done"


def test_legacy_raw_tilde_record_migrates(tmp_path: Path) -> None:
    task_id = "~legacy"
    legacy = tmp_path / "tasks" / f"{task_id}.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        '{"task_id":"~legacy","state":"pending","owner":"",'
        '"claim_ts":0,"heartbeat_ts":0,"attempts":0,"deps":[]}\n',
        encoding="utf-8",
    )

    tb.form(tmp_path, [{"task_id": task_id, "objective": "portable"}])

    assert tb._path(tmp_path, task_id).exists()
    assert not legacy.exists()
    assert [task["task_id"] for task in tb.snapshot(tmp_path)] == [task_id]


def test_new_oversized_task_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="120"):
        tb.form(tmp_path, [{"task_id": "x" * 121, "objective": "too long"}])


def test_form_rejects_unicode_equivalent_task_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="normalized identity"):
        tb.form(
            tmp_path,
            [
                {"task_id": "café", "objective": "first"},
                {"task_id": "cafe\u0301", "objective": "second"},
            ],
        )


def test_form_rejects_repeated_exact_task_id_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate task_id"):
        tb.form(
            tmp_path,
            [
                {"task_id": "same", "objective": "first"},
                {"task_id": "same", "objective": "second"},
            ],
        )

    assert tb.snapshot(tmp_path) == []


def test_form_validates_complete_batch_before_mutating_existing_board(
    tmp_path: Path,
) -> None:
    tb.form(tmp_path, [{"task_id": "old", "objective": "unchanged"}])

    with pytest.raises(ValueError, match="normalized identity"):
        tb.form(
            tmp_path,
            [
                {"task_id": "new", "objective": "must not be written"},
                {"task_id": "café", "objective": "first"},
                {"task_id": "cafe\u0301", "objective": "conflict"},
            ],
        )

    assert [task["task_id"] for task in tb.snapshot(tmp_path)] == ["old"]


def test_form_rejects_resumed_normalized_task_id_conflict(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "café", "objective": "first"}])

    with pytest.raises(ValueError, match="normalized identity"):
        tb.form(tmp_path, [{"task_id": "cafe\u0301", "objective": "second"}])


def test_form_stores_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"]},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"], "priority": 5},
    ])
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["priority"] == 100
    assert snap["b"]["priority"] == 5


def test_form_stores_task_timeout(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "default", "objective": "x"},
        {"task_id": "bounded", "objective": "y", "timeout_s": 600},
    ])
    snap = {task["task_id"]: task for task in tb.snapshot(tmp_path)}
    assert snap["default"]["timeout_s"] == 0.0
    assert snap["bounded"]["timeout_s"] == 600.0


def test_claim_top_orders_by_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "priority": 100},
        {"task_id": "b", "objective": "y", "priority": 5},
        {"task_id": "c", "objective": "z", "priority": 5},
    ])
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "b"
    assert tb.claim_top(tmp_path, "w2", now=2.0)["task_id"] == "c"
    assert tb.claim_top(tmp_path, "w3", now=3.0)["task_id"] == "a"
    assert tb.claim_top(tmp_path, "w4", now=4.0) is None


def test_claim_top_respects_dependencies_before_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "priority": 100},
        {"task_id": "b", "objective": "y", "priority": 1, "deps": ["a"]},
    ])
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "a"
    assert tb.claim_top(tmp_path, "w2", now=2.0) is None
    tb.complete(tmp_path, "a")
    assert tb.claim_top(tmp_path, "w3", now=3.0)["task_id"] == "b"


def test_count_in_flight(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x"},
        {"task_id": "b", "objective": "y"},
    ])
    assert tb.count_in_flight(tmp_path) == 0
    tb.claim_top(tmp_path, "w1", now=1.0)
    assert tb.count_in_flight(tmp_path) == 1
    tb.heartbeat(tmp_path, "a", now=1.0)
    assert tb.count_in_flight(tmp_path) == 1
    tb.complete(tmp_path, "a")
    assert tb.count_in_flight(tmp_path) == 0


def test_form_preserves_live_ownership_on_reform(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "v1", "priority": 100}])
    tb.claim_top(tmp_path, "w1", now=1.0)
    tb.heartbeat(tmp_path, "a", now=2.0)

    tb.form(tmp_path, [{"task_id": "a", "objective": "v1", "priority": 100}])
    task = {t["task_id"]: t for t in tb.snapshot(tmp_path)}["a"]
    assert task["state"] == "running" and task["owner"] == "w1"
    assert task["heartbeat_ts"] == 2.0
    assert task["objective"] == "v1" and task["priority"] == 100
    assert tb.count_in_flight(tmp_path) == 1


def test_form_rejects_material_change_for_live_task_identity(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "v1", "priority": 100}])
    tb.claim_top(tmp_path, "w1", now=1.0)
    tb.heartbeat(tmp_path, "a", now=2.0)

    with pytest.raises(ValueError, match="materially changed spec"):
        tb.form(tmp_path, [{"task_id": "a", "objective": "v2-updated", "priority": 5}])


def test_form_deliberately_reopens_terminal_task(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "x"}])
    tb.claim_top(tmp_path, "w1", now=1.0)
    tb.complete(tmp_path, "a")
    tb.form(tmp_path, [{"task_id": "a", "objective": "x2"}])
    task = {t["task_id"]: t for t in tb.snapshot(tmp_path)}["a"]
    assert task["state"] == "pending"
    assert task["owner"] == "" and task["objective"] == "x2"


# ── the task's done condition is a field, not something to be inferred ────────

def test_form_carries_the_acceptance_check(tmp_path: Path) -> None:
    # A board task is a mission, and until now the one mission shape with no way
    # to state its own done condition. It is carried verbatim: the board does not
    # parse it, and an absent one is an empty string rather than a missing key, so
    # a reader of the record never has to distinguish "not set" from "not a field".
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "acceptance_check": "goal-7 is closed."},
        {"task_id": "b", "objective": "y"},
    ])
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["acceptance_check"] == "goal-7 is closed."
    assert snap["b"]["acceptance_check"] == ""


def test_form_does_not_interpret_the_acceptance_check(tmp_path: Path) -> None:
    # Opaque in, opaque out. What a well-formed done condition says is the
    # vertical's business; the board must not acquire an opinion about it, and
    # must not let it near the fields it does act on.
    weird = "  ¿ claim-A ∧ ¬claim-B ?  {\"not\": \"json\"}  "
    tb.form(tmp_path, [{"task_id": "a", "objective": "x", "acceptance_check": weird}])
    task = {t["task_id"]: t for t in tb.snapshot(tmp_path)}["a"]
    assert task["acceptance_check"] == weird
    assert task["target"] == "a" and task["state"] == "pending"


def test_form_rejects_changed_acceptance_check_for_live_task(tmp_path: Path) -> None:
    tb.form(tmp_path, [{"task_id": "a", "objective": "v1", "acceptance_check": "old"}])
    tb.claim_top(tmp_path, "w1", now=1.0)
    tb.heartbeat(tmp_path, "a", now=2.0)

    with pytest.raises(ValueError, match="materially changed spec"):
        tb.form(
            tmp_path,
            [{"task_id": "a", "objective": "v1", "acceptance_check": "new"}],
        )

    task = {t["task_id"]: t for t in tb.snapshot(tmp_path)}["a"]
    assert task["acceptance_check"] == "old"
    assert task["state"] == "running" and task["owner"] == "w1"


def test_form_rejects_changed_role_and_owned_roots_for_live_task(
    tmp_path: Path,
) -> None:
    original = {
        "task_id": "a",
        "objective": "v1",
        "role": "implementer",
        "owns_paths": ["src/**"],
        "non_goals": ["docs"],
    }
    tb.form(tmp_path, [original])
    tb.claim_top(tmp_path, "w1", now=1.0)

    with pytest.raises(ValueError, match="materially changed spec"):
        tb.form(
            tmp_path,
            [{
                **original,
                "role": "reviewer",
                "owns_paths": ["tests/**"],
            }],
        )


def test_form_never_takes_lifecycle_fields_from_a_spec(tmp_path: Path) -> None:
    # Why the record is rebuilt field by field instead of copied: the board and
    # the Curator own the lifecycle, so a spec claiming to be done, owned, and
    # heartbeating cannot make itself so. That guard is about the fields the board
    # ACTS on, and it is untouched by carrying one more descriptive field.
    tb.form(tmp_path, [{
        "task_id": "a", "objective": "x", "acceptance_check": "carried",
        "state": "done", "owner": "forged", "attempts": 7, "claim_ts": 99.0,
        "heartbeat_ts": 99.0, "reason": "forged",
    }])
    task = {t["task_id"]: t for t in tb.snapshot(tmp_path)}["a"]
    assert task["state"] == "pending" and task["owner"] == ""
    assert task["attempts"] == 0 and task["claim_ts"] == 0.0
    assert task["heartbeat_ts"] == 0.0 and task["reason"] == ""
    assert task["acceptance_check"] == "carried"
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "a"
