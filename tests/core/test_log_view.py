from __future__ import annotations

import re

from argus.core import log_view as lv

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


def test_format_timestamp_missing_ts_falls_back() -> None:
    # The activity-log sink can see events without a ts; must not crash.
    out = lv.format_timestamp(None, prev_ts=None)
    assert re.match(r"^\d\d:\d\d:\d\d ", out)


# ── wrap_body ─────────────────────────────────────────────────────────────


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


def test_legacy_lifecycle_names_share_the_canonical_grouping_path() -> None:
    state = lv.LogState()
    assert _adv(state, "mission.started", item_id="m1") == lv.OPEN
    assert _adv(state, "round.started", round_index=1) == lv.MID
    assert _adv(state, "mission.completed", item_id="m1") == lv.CLOSE


def test_manager_stage_decision_groups_under_mission() -> None:
    # The Manager's stage decision is emitted before the mission closes, so it
    # nests as an interior line; with nothing open it degrades to FLAT.
    s = lv.LogState()
    _adv(s, "life.mission.started", item_id="m1")
    assert _adv(s, "life.manager.stage_decision", action="advance") == lv.MID
    s2 = lv.LogState()
    assert _adv(s2, "life.manager.stage_decision", action="advance") == lv.FLAT


def test_planner_closes_stray_mission() -> None:
    s = lv.LogState()
    _adv(s, "life.mission.started", item_id="m1")
    assert _adv(s, "life.planner.start") == lv.OPEN
    assert s.planner_open and not s.mission_open
    assert _adv(s, "life.planner.task_added", title="x") == lv.MID
    assert _adv(s, "life.planner.verdict", project_done=False) == lv.CLOSE
    assert not s.planner_open


def test_status_inbox_are_flat() -> None:
    s = lv.LogState()
    assert _adv(s, "life.status", text="x") == lv.FLAT
    assert _adv(s, "life.inbox.queued", text="x") == lv.FLAT


# ── block ─────────────────────────────────────────────────────────────────


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
