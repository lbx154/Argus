from __future__ import annotations

import re

from argus_skill.core import log_view as lv

# ── gap_str ───────────────────────────────────────────────────────────────

def test_gap_str_boundaries() -> None:
    assert lv.gap_str(0) == "+0s"
    assert lv.gap_str(59) == "+59s"
    assert lv.gap_str(60) == "+1m"
    assert lv.gap_str(65) == "+1m5s"
    assert lv.gap_str(3600) == "+1h"
    assert lv.gap_str(3661) == "+1h1m"
    assert lv.gap_str(86400) == "+1d"
    # Clock skew / caller-supplied earlier ts -> clamp, never negative.
    assert lv.gap_str(-5) == "+0s"


# ── format_timestamp ──────────────────────────────────────────────────────

def test_format_timestamp_local_and_relative() -> None:
    out = lv.format_timestamp(1_000_000.0, prev_ts=None)
    assert re.match(r"^\d\d:\d\d:\d\d \(\+0s\)", out)
    assert len(out) == lv.TS_W  # left-justified to a fixed column

    later = lv.format_timestamp(1_000_037.0, prev_ts=1_000_000.0)
    assert "(+37s)" in later


def test_format_timestamp_missing_ts_falls_back() -> None:
    # The activity-log sink can see events without a ts; must not crash.
    out = lv.format_timestamp(None, prev_ts=None)
    assert re.match(r"^\d\d:\d\d:\d\d ", out)


# ── wrap_body ─────────────────────────────────────────────────────────────

def test_wrap_body_roundtrips_and_respects_width() -> None:
    text = "the quick brown fox jumps over the lazy dog " * 3
    lines = lv.wrap_body(text, 24)
    assert lines  # non-empty
    assert all(lv._disp_width(ln) <= 24 for ln in lines)
    # No truncation: collapsed words are all preserved, in order.
    assert " ".join(lines).split() == text.split()


def test_wrap_body_hard_splits_overlong_token() -> None:
    token = "x" * 50
    lines = lv.wrap_body(token, 10)
    assert all(lv._disp_width(ln) <= 10 for ln in lines)
    assert "".join(lines) == token


def test_wrap_body_cjk_width() -> None:
    # Each CJK char counts as 2 cells, so width 6 -> at most 3 per line.
    lines = lv.wrap_body("中文测试内容字符", 6)
    assert all(lv._disp_width(ln) <= 6 for ln in lines)
    assert "".join(lines) == "中文测试内容字符"


def test_wrap_body_empty() -> None:
    assert lv.wrap_body("", 40) == []
    assert lv.wrap_body("   ", 40) == []


# ── advance / LogState lifecycle ──────────────────────────────────────────

def _adv(state: lv.LogState, etype: str, **fields: object) -> str:
    return lv.advance(state, etype, {"type": etype, **fields})


def test_mission_round_lifecycle() -> None:
    s = lv.LogState()
    assert _adv(s, "life.mission.started", item_id="m1", missions_started=3, title="T") == lv.OPEN
    assert s.mission_open and s.mission_seq == 3
    assert _adv(s, "life.phase.started", round_index=1) == lv.MID
    assert _adv(s, "round.main.completed", round_index=1) == lv.MID
    assert _adv(s, "round.review.completed", round_index=1) == lv.MID
    assert s.round_index == 1
    assert _adv(s, "life.mission.completed", item_id="m1", success=True) == lv.CLOSE
    assert not s.mission_open


def test_legacy_lifecycle_names_share_the_canonical_grouping_path() -> None:
    state = lv.LogState()
    assert _adv(state, "mission.started", item_id="m1") == lv.OPEN
    assert _adv(state, "round.started", round_index=1) == lv.MID
    assert _adv(state, "mission.completed", item_id="m1") == lv.CLOSE


def test_failure_nudge_uses_round_field() -> None:
    s = lv.LogState()
    _adv(s, "life.mission.started", item_id="m1")
    _adv(s, "engineer.failure_nudge", round=4)
    assert s.round_index == 4


def test_manager_stage_decision_groups_under_mission() -> None:
    # The Manager's stage decision is emitted before the mission closes, so it
    # nests as an interior line; with nothing open it degrades to FLAT.
    s = lv.LogState()
    _adv(s, "life.mission.started", item_id="m1")
    assert _adv(s, "life.manager.stage_decision", action="advance") == lv.MID
    s2 = lv.LogState()
    assert _adv(s2, "life.manager.stage_decision", action="advance") == lv.FLAT


def test_orphaned_mission_reopens() -> None:
    s = lv.LogState()
    _adv(s, "life.mission.started", item_id="m1")
    # New mission while the previous one never closed -> just open the new one.
    assert _adv(s, "life.mission.started", item_id="m2") == lv.OPEN
    assert s.mission_open and s.mission_id == "m2"


def test_planner_closes_stray_mission() -> None:
    s = lv.LogState()
    _adv(s, "life.mission.started", item_id="m1")
    assert _adv(s, "life.planner.start") == lv.OPEN
    assert s.planner_open and not s.mission_open
    assert _adv(s, "life.planner.task_added", title="x") == lv.MID
    assert _adv(s, "life.planner.verdict", project_done=False) == lv.CLOSE
    assert not s.planner_open


def test_round_event_with_nothing_open_is_flat() -> None:
    s = lv.LogState()
    # Fresh sink / daemon restart: interior event before any mission header.
    assert _adv(s, "round.review.completed", round_index=2) == lv.FLAT


def test_status_inbox_are_flat() -> None:
    s = lv.LogState()
    assert _adv(s, "life.status", text="x") == lv.FLAT
    assert _adv(s, "life.inbox.queued", text="x") == lv.FLAT


# ── block ─────────────────────────────────────────────────────────────────

def test_block_head_only() -> None:
    ts = lv.format_timestamp(1_000_000.0, None)
    out = lv.block(ts, lv.OPEN, "MISSION", "start  #3  Run benchmark")
    assert "\n" not in out
    assert "┌─" in out and "MISSION" in out and "Run benchmark" in out


def test_block_wraps_detail_no_truncation() -> None:
    ts = lv.format_timestamp(1_000_000.0, None)
    reason = "benchmark provenance missing because the harness never recorded the dataset checksum and we cannot reproduce the score"
    out = lv.block(ts, lv.MID, "ROUND", "reviewer round=1 verdict=continue", reason, width=70)
    lines = out.split("\n")
    assert len(lines) >= 2
    assert "…" not in out  # full text, never truncated
    assert lv.MARK in out  # continuation marker present
    # Every word of the reason survives somewhere in the detail lines.
    detail_text = " ".join(lines[1:])
    for word in reason.split():
        assert word in detail_text


def test_block_paint_callbacks_applied() -> None:
    ts = lv.format_timestamp(1_000_000.0, None)
    plain = lv.block(ts, lv.OPEN, "PLANNER", "start")
    painted = lv.block(
        ts, lv.OPEN, "PLANNER", "start",
        paint_connector=lambda s: f"<{s}>",
        paint_category=lambda s: f"[{s}]",
    )
    assert "[PLANNER]" in painted and "<" in painted
    assert "[PLANNER]" not in plain  # identity by default


def test_block_pure_text_flows_from_head() -> None:
    # primary empty -> free-form text starts on the head and flows down,
    # no ↳ marker, never truncated.
    ts = lv.format_timestamp(1_000_000.0, None)
    text = "planner: queued one follow-up task; backlog now has four items pending review and more"
    out = lv.block(ts, lv.FLAT, "STATUS", "", text, width=70)
    lines = out.split("\n")
    assert len(lines) >= 2
    assert lv.MARK not in out           # flow, not ↳ detail
    assert "…" not in out               # full text
    assert "STATUS" in lines[0]
    assert " ".join([lines[0].split("STATUS")[-1]] + lines[1:]).split() == text.split()
