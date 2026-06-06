"""Tests for the curated working-memory checkpoint."""
from __future__ import annotations

import json

from argus_skill.engineer.checkpoint import (
    MAX_DONE_ITEMS,
    MAX_ITEM_CHARS,
    MAX_TRIED_ITEMS,
    CheckpointState,
    load_checkpoint,
    save_checkpoint,
)


def test_from_dict_empty_and_garbage():
    assert CheckpointState.from_dict(None).is_empty()
    assert CheckpointState.from_dict("nonsense").is_empty()
    assert CheckpointState.from_dict({}).is_empty()


def test_caps_enforced_in_python():
    raw = {
        "goal": "g" * 5000,
        "done": [f"done-{i}" for i in range(100)],
        "tried_and_failed": [f"fail-{i}" for i in range(100)],
        "open_blocker": "b" * 5000,
        "next_step": "n" * 5000,
    }
    cp = CheckpointState.from_dict(raw)
    assert len(cp.done) == MAX_DONE_ITEMS
    assert len(cp.tried_and_failed) == MAX_TRIED_ITEMS
    assert all(len(item) <= MAX_ITEM_CHARS + 1 for item in cp.done)  # +1 for ellipsis
    assert len(cp.goal) <= 401
    assert len(cp.open_blocker) <= 801
    assert len(cp.next_step) <= 601


def test_dedupe_repeated_memory():
    cp = CheckpointState.from_dict(
        {"done": ["same", "same", "SAME", "other"]}
    )
    assert cp.done == ["same", "other"]


def test_drops_blank_items():
    cp = CheckpointState.from_dict({"done": ["", "  ", "real", None]})
    assert cp.done == ["real"]


def test_render_first_session_has_handoff_request():
    text = CheckpointState().render_for_engineer()
    assert "first session" in text.lower()
    assert "HANDOFF:" in text
    assert "tried_failed:" in text


def test_render_populated_includes_dead_ends():
    cp = CheckpointState(
        goal="ship it",
        done=["wrote module"],
        tried_and_failed=["approach X collapses gradient"],
        open_blocker="conditions identical",
        next_step="redesign separation",
    )
    text = cp.render_for_engineer()
    assert "ship it" in text
    assert "wrote module" in text
    assert "approach X collapses gradient" in text
    assert "conditions identical" in text
    assert "redesign separation" in text
    assert "do NOT repeat" in text


def test_roundtrip_dict():
    cp = CheckpointState(
        goal="g", done=["d"], tried_and_failed=["t"],
        open_blocker="b", next_step="n", round=3,
    )
    again = CheckpointState.from_dict(cp.to_dict())
    assert again.goal == "g"
    assert again.done == ["d"]
    assert again.tried_and_failed == ["t"]
    assert again.open_blocker == "b"
    assert again.next_step == "n"
    assert again.round == 3


def test_save_and_load(tmp_path):
    path = tmp_path / "state" / "checkpoint.json"
    cp = CheckpointState(goal="g", done=["x"], round=2)
    save_checkpoint(path, cp)
    assert path.exists()
    loaded = load_checkpoint(path)
    assert loaded.goal == "g"
    assert loaded.done == ["x"]
    assert loaded.round == 2


def test_load_missing_is_empty(tmp_path):
    assert load_checkpoint(tmp_path / "nope.json").is_empty()
    assert load_checkpoint(None).is_empty()


def test_load_corrupt_file_is_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_checkpoint(path).is_empty()


def test_stamped_sets_round_and_time():
    cp = CheckpointState(goal="g").stamped(round_no=5)
    assert cp.round == 5
    assert cp.updated_at > 0


def test_persisted_json_is_readable(tmp_path):
    path = tmp_path / "cp.json"
    save_checkpoint(path, CheckpointState(goal="g", done=["a", "b"]))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["goal"] == "g"
    assert data["done"] == ["a", "b"]
