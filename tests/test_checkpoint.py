"""Tests for the curated working-memory checkpoint."""
from __future__ import annotations

import json

from argus_skill.engineer.checkpoint import (
    MAX_ACTIVE_LINE_DESC,
    MAX_DONE_ITEMS,
    MAX_ITEM_CHARS,
    MAX_MATURING_ITEMS,
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


def test_maturing_roundtrip_and_cap():
    raw = {"maturing": [f"dir-{i}" for i in range(50)]}
    cp = CheckpointState.from_dict(raw)
    assert len(cp.maturing) == MAX_MATURING_ITEMS
    again = CheckpointState.from_dict(cp.to_dict())
    assert again.maturing == cp.maturing


def test_maturing_counts_toward_non_empty():
    # A maturing bold direction is load-bearing memory: a checkpoint that has
    # only a maturing entry must NOT be treated as empty (else it gets dropped
    # and the direction is forgotten — the exact bug this field fixes).
    assert not CheckpointState(maturing=["co-tune value-residual gate"]).is_empty()


def test_render_distinguishes_maturing_from_dead_ends():
    cp = CheckpointState(
        tried_and_failed=["approach X collapses gradient"],
        maturing=["value residual: retry with non-zero gate init"],
    )
    text = cp.render_for_engineer()
    # genuine dead ends keep their do-NOT-repeat framing
    assert "do NOT repeat" in text
    assert "TRIED & FAILED" in text
    # maturing is rendered under its OWN header and explicitly NOT a dead end
    assert "MATURING DIRECTIONS" in text
    assert "NOT dead ends" in text
    assert "value residual: retry with non-zero gate init" in text


def test_handoff_request_includes_maturing():
    text = CheckpointState().render_for_engineer()
    assert "maturing:" in text


def test_maturing_persisted_and_stamped(tmp_path):
    path = tmp_path / "cp.json"
    save_checkpoint(path, CheckpointState(maturing=["dir A"]))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["maturing"] == ["dir A"]
    stamped = load_checkpoint(path).stamped(round_no=2)
    assert stamped.maturing == ["dir A"]
    assert stamped.round == 2


# --- active_line: the retained-branch pointer for a bold maturing line -------


def test_active_line_roundtrip_and_caps():
    raw = {
        "active_line": {
            "desc": "d" * 5000,
            "branch_or_path": "p" * 5000,
            "rounds_active": 4,
            "note": "n" * 5000,
        }
    }
    cp = CheckpointState.from_dict(raw)
    assert cp.active_line["rounds_active"] == 4
    assert len(cp.active_line["desc"]) <= MAX_ACTIVE_LINE_DESC + 1  # +1 ellipsis
    again = CheckpointState.from_dict(cp.to_dict())
    assert again.active_line == cp.active_line


def test_active_line_garbage_is_failsoft():
    assert CheckpointState.from_dict({"active_line": "nope"}).active_line == {}
    assert CheckpointState.from_dict({"active_line": {}}).active_line == {}
    # all-blank fields collapse to empty (no phantom active line)
    assert CheckpointState.from_dict(
        {"active_line": {"desc": "", "branch_or_path": "", "note": ""}}
    ).active_line == {}
    # a non-int rounds_active degrades to 0, not a crash
    cp = CheckpointState.from_dict(
        {"active_line": {"desc": "x", "rounds_active": "bad"}}
    )
    assert cp.active_line["rounds_active"] == 0


def test_active_line_counts_toward_non_empty():
    # An active line is load-bearing: a checkpoint with only an active line must
    # NOT be dropped as empty (else the bold direction is forgotten).
    assert not CheckpointState(active_line={"desc": "split-head local raw V"}).is_empty()


def test_render_active_line_says_build_on_it_not_restart():
    cp = CheckpointState(
        active_line={
            "desc": "co-designed capacity reshape",
            "branch_or_path": "active-line/a266",
            "rounds_active": 3,
            "note": "widen head next",
        }
    )
    text = cp.render_for_engineer()
    assert "ACTIVE LINE" in text
    assert "co-designed capacity reshape" in text
    assert "active-line/a266" in text
    assert "BUILD ON THIS" in text
    assert "do NOT restart" in text


def test_handoff_request_includes_active_line():
    text = CheckpointState().render_for_engineer()
    assert "active_line:" in text


def test_active_line_persisted_and_stamped(tmp_path):
    path = tmp_path / "cp.json"
    save_checkpoint(
        path, CheckpointState(active_line={"desc": "bold X", "rounds_active": 2})
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["active_line"]["desc"] == "bold X"
    stamped = load_checkpoint(path).stamped(round_no=5)
    assert stamped.active_line["desc"] == "bold X"
    assert stamped.round == 5
