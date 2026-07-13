"""Bounded structural repair: on a first-attempt PatchError the zh demo makes EXACTLY ONE
repair call, re-validates strictly, and stops after the second failure (never a third
call). Deterministic via a mocked transport (_post_raw) — no real provider call, no LLM."""
from __future__ import annotations

import json
from unittest import mock

import argus_skill.verticals.fiction_writing.evaluations.run_evals as rev


def _text(t):
    return {"content": [{"type": "text", "text": t}], "stop_reason": "end_turn"}


def _tool(inp):
    return {"content": [{"type": "tool_use", "name": "submit_patch", "input": inp}],
            "stop_reason": "tool_use"}


_GOOD_OPS = [
    {"op": "add_character", "id": "c1", "value": {"name": "Ana"}},
    {"op": "add_open_thread", "value": {"id": "th1", "statement": "s"}},
]
# a STRINGIFIED ops array that is INVALID JSON (unescaped inner quotes) — the real failure
_BAD_STRINGIFIED = '[{"op": "add_character", "id": "c1", "value": {"name": "叙述者（"我"）"}}]'


def test_first_attempt_success_makes_no_repair_call(tmp_path, monkeypatch):
    monkeypatch.setattr(rev, "REPORTS", tmp_path)
    monkeypatch.setenv("FW_EVAL_ARTIFACTS", str(tmp_path / "arts"))
    seq = [_text("草稿"), _tool({"patch_id": "ch1", "ops": _GOOD_OPS})]   # draft, forced(valid)
    with mock.patch.object(rev, "_post_raw", side_effect=seq) as m:
        rev.eval_demo_structured()
    assert m.call_count == 2                                       # draft + forced; NO repair call
    assert (tmp_path / "demo_zh_structured_state.json").exists()


def test_repair_recovers_from_invalid_stringified_ops(tmp_path, monkeypatch):
    monkeypatch.setattr(rev, "REPORTS", tmp_path)
    monkeypatch.setenv("FW_EVAL_ARTIFACTS", str(tmp_path / "arts"))
    seq = [_text("草稿"),
           _tool({"patch_id": "ch1", "ops": _BAD_STRINGIFIED}),       # forced: invalid stringified ops
           _tool({"patch_id": "ch1", "ops": _GOOD_OPS})]              # repair: real array
    with mock.patch.object(rev, "_post_raw", side_effect=seq) as m:
        rev.eval_demo_structured()
    assert m.call_count == 3                                       # draft + forced + ONE repair
    assert (tmp_path / "demo_zh_structured_state.json").exists()   # recovered after repair


def test_repair_bounded_stops_after_one_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(rev, "REPORTS", tmp_path)
    monkeypatch.setenv("FW_EVAL_ARTIFACTS", str(tmp_path / "arts"))
    seq = [_text("草稿"),
           _tool({"patch_id": "ch1", "ops": _BAD_STRINGIFIED}),       # forced: invalid
           _tool({"patch_id": "ch1", "ops": _BAD_STRINGIFIED})]       # repair: STILL invalid
    with mock.patch.object(rev, "_post_raw", side_effect=seq) as m:
        rev.eval_demo_structured()
    assert m.call_count == 3                                       # NO third (4th total) call
    out = tmp_path / "arts" / "demo_zh_repair_failed.json"
    assert out.exists()                                           # both rounds saved
    art = json.loads(out.read_text(encoding="utf-8"))
    assert art["round1"]["error"] and art["round2"]["error"]      # both rounds' errors recorded
    assert not (tmp_path / "demo_zh_structured_state.json").exists()  # no success state written
