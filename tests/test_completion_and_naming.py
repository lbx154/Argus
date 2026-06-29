"""Phase 3b: mission-completion footer + session auto-naming.

The operator's complaint was a bare ``任务完成`` with (a) no reviewer
conclusion — only the engineer's last word showed — and (b) no save location.
These tests pin the richer footer and the Codex/Claude-Code-style session name
derived from the first real task.
"""
from __future__ import annotations

from argus_skill.apps.cli._follow import _format_follow_mission_context
from argus_skill.manager.repl import (
    _derive_session_name,
    _format_completion,
    _maybe_name_session,
)


# ---- completion footer ----------------------------------------------------

def test_completion_surfaces_reviewer_conclusion_and_locations():
    final = {
        "status": "ok",
        "rounds": 4,
        "cost_usd": 1.2345,
        "_last_review": {
            "status": "accept",
            "confidence": 0.9,
            "reason": "kernel correct, 2.1x speedup verified on B200",
        },
    }
    lines = _format_completion(final, "it-7", "/root/projects/s-abcd", workdir="/repo")
    assert lines[0] == "✅ it-7 done · status=ok · 4r · cost=$1.2345"
    # The reviewer verdict (done-ness authority) is shown, not just the engineer.
    assert any("reviewer accept (conf 0.90)" in ln and "2.1x speedup" in ln for ln in lines)
    assert any(ln.strip() == "record: /root/projects/s-abcd" for ln in lines)
    assert any(ln.strip() == "workdir: /repo" for ln in lines)


def test_completion_degrades_without_review_or_cost():
    lines = _format_completion(
        {"status": "supervisor_error", "rounds": 0}, "it-8", "/p", workdir="/p"
    )
    assert lines[0] == "✅ it-8 done · status=supervisor_error"  # no rounds/cost noise
    assert not any("reviewer" in ln for ln in lines)  # no fabricated verdict
    # workdir == record -> the redundant workdir line is suppressed.
    assert [ln for ln in lines if "workdir:" in ln] == []
    assert any("record: /p" in ln for ln in lines)


def test_completion_keeps_full_reviewer_reason():
    # reviewer is the done-ness authority — its verdict is NOT truncated (terminal wraps).
    final = {"status": "ok", "_last_review": {"reason": "x" * 500}}
    lines = _format_completion(final, "i", "/p", workdir="/p")
    review_line = next(ln for ln in lines if "reviewer" in ln)
    assert not review_line.endswith("…")
    assert "x" * 500 in review_line  # full reason kept


# ---- session auto-naming --------------------------------------------------

def test_derive_session_name_first_line_then_truncate():
    assert _derive_session_name("优化 079 kernel\nsecond line") == "优化 079 kernel"
    assert _derive_session_name("   \n\n  real task here") == "real task here"
    long = "a" * 80
    out = _derive_session_name(long)
    assert out.endswith("…") and len(out) == 48
    assert _derive_session_name("") == ""


def test_maybe_name_session_is_idempotent_and_failsoft():
    # already named -> no-op
    cs = {"session_named": True, "session_id": "s-y", "global_root": "/tmp"}
    _maybe_name_session(cs, "task")
    assert cs["session_named"] is True
    # missing global_root -> no crash, stays unnamed
    cs2 = {"session_named": False, "session_id": "s-x", "global_root": None}
    _maybe_name_session(cs2, "task")
    assert cs2["session_named"] is False


def test_maybe_name_session_names_a_fresh_session(tmp_path):
    from argus_skill.core.session import resolve_session, read_session_meta

    sid, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=1)
    cs = {"session_named": False, "session_id": sid, "global_root": tmp_path}
    _maybe_name_session(cs, "optimize the 079 kernel\nmore detail")
    assert cs["session_named"] is True
    assert read_session_meta(tmp_path, sid).display_name == "optimize the 079 kernel"


# ---- objective=- root-cause fix ------------------------------------------

def test_mission_context_renders_objective_when_event_carries_it():
    # The daemon now emits `objective` on life.mission.started, so the follow
    # mission-context line shows the real goal instead of "objective=-".
    ev = {"item_id": "it-1", "title": "kernel work", "objective": "hit SOL on 079"}
    bits = _format_follow_mission_context(ev)
    assert "objective=hit SOL on 079" in bits
    # ...and degrades to "-" only when genuinely absent.
    bits2 = _format_follow_mission_context({"item_id": "it-2"})
    assert "objective=-" in bits2
