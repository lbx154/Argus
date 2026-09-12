"""A reader brief interprets attributable records without changing the research."""
import copy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.webapi import map_narrative
from argus_skill.webapi.map_view import normalize_events, read_map


def brief():
    return {
        "why": "核对这一步使用的条件，避免把有限范围的结果推广到全部对象。",
        "concept": {
            "name": "反例",
            "explanation": "一个不满足命题要求的例子，就能推翻声称对所有对象都成立的命题。",
            "example": "示意例子：命题说所有整数都是偶数，整数 3 就是反例；这不是本次任务的实验结果。",
            "connection": "本步记录在检查一个例外是否使原先的推广失效。",
        },
        "scope": "执行者只报告完成条件核对；尚未见独立复核记录，也未证明原始问题的所有情形。",
        "next": "下一步尚未记录。",
    }


@pytest.fixture
def project(tmp_path):
    sid = "s-reader-test"
    write_session_meta(tmp_path, SessionMeta(id=sid, created=1, last_active=1, display_name="Investigate a claim"))
    life = tmp_path / "projects" / sid
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem(
        id="a", ts=1, title="Check the exception", objective="Check the stated conditions", status="running",
        goal_contribution="Determine where the argument applies", plan_hypothesis="A boundary case may fail",
        non_goals=["A proof of every case"], acceptance_check="Record the conditions and a reproducible check",
        outcome={"execution_status": "running", "review_status": "not_reviewed", "resumable": True},
    ))
    (life / "events.jsonl").write_text(json.dumps({
        "event_id": "review-a", "type": "round.review.completed", "item_id": "a", "round_index": 2,
        "status": "done", "reason": "The executor checked its own result", "review_source": "engineer_self_review",
        "review_skipped": False, "ts": 10,
    }) + "\n")
    return sid, life, memory


def test_task_purpose_scope_and_review_provenance_reach_the_card_evidence(tmp_path, project):
    sid, life, _ = project
    data = read_map(sid, tmp_path, life)
    task = data["tasks"][0]
    assert task["goal_contribution"] == "Determine where the argument applies"
    assert task["plan_hypothesis"] == "A boundary case may fail"
    assert task["non_goals"] == ["A proof of every case"]
    assert task["outcome"] == {"execution_status": "running", "review_status": "not_reviewed", "resumable": True}
    request = {"key": "a", "task_id": "a", "kind": "task", "event_ids": ["review-a"]}
    document = map_narrative.card_evidence(data, [request])[0]
    assert document["task"]["outcome"]["review_status"] == "not_reviewed"
    assert document["events"][0]["review_source"] == "engineer_self_review"
    assert document["events"][0]["review_skipped"] is False
    assert document["events"][0]["round_index"] == 2
    historical = map_narrative.card_evidence(data, [{**request, "key": "review-a", "kind": "review"}])[0]
    assert "status" not in historical["task"] and "outcome" not in historical["task"]
    assert historical["task"]["non_goals"] == ["A proof of every case"]


def test_outcome_projection_preserves_only_explicit_public_dimensions():
    events = normalize_events([{
        "event_id": "done-a", "type": "life.mission.completed", "item_id": "a", "ts": 2,
        "outcome": {"execution_status": "completed", "review_status": "skipped",
                    "stage_certification": "not_certified", "interruption_kind": "none", "resumable": False,
                    "arbitrary_internal_payload": {"not": "public"}},
    }], {"a"})
    assert events[0]["outcome"] == {
        "execution_status": "completed", "review_status": "skipped", "stage_certification": "not_certified",
        "interruption_kind": "none", "resumable": False,
    }
    legacy = normalize_events([{"type": "round.review.completed", "item_id": "a", "ts": 3}], {"a"})[0]
    assert "review_source" not in legacy and "outcome" not in legacy


def test_schema_limits_a_brief_to_one_optional_concept():
    value = {"cards": {"a": {"title": "Task", "summary": "Summary", "detail": "Detail", "reader_brief": brief()}}, "relations": []}
    validator = Draft202012Validator(map_narrative.schema(["a"], ["a"]))
    validator.validate(value)
    value["cards"]["a"]["reader_brief"]["concept"] = None
    validator.validate(value)
    value["cards"]["a"]["reader_brief"]["concept"] = [brief()["concept"]]
    assert list(validator.iter_errors(value))


@pytest.mark.parametrize("invalid", [None, {}, {**brief(), "next": ""}, {**brief(), "concept": {"name": "Only a name"}}])
def test_generation_rejects_missing_or_malformed_briefs(monkeypatch, invalid):
    monkeypatch.setattr(map_narrative, "run_map_model", lambda *args, **kwargs: {
        "cards": {"a": {"title": "Task", "summary": "Summary", "detail": "Detail", "reader_brief": invalid}},
        "relations": [],
    })
    with pytest.raises(ValueError, match="reader brief"):
        map_narrative.generate([{"key": "a"}], [{"id": "a"}], "en-US", config=None, project_root=None, global_root=None)


def test_brief_is_cached_with_source_revisions_and_does_not_change_research(tmp_path, project, monkeypatch):
    sid, life, memory = project
    data = read_map(sid, tmp_path, life)
    before = {name: (life / name).read_bytes() for name in ("backlog.jsonl", "events.jsonl")}
    original = copy.deepcopy(data)
    calls = []

    def generate(documents, *args, **kwargs):
        calls.append(documents)
        return {"cards": [{"key": document["key"], "title": "Check the conditions", "summary": "An executor report",
                            "detail": "Independent review has not been recorded", "reader_brief": brief()}
                           for document in documents], "relations": []}

    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "resolve_map_model", lambda: SimpleNamespace(revision="test-model"))
    monkeypatch.setattr(map_narrative, "generate", generate)
    requests = [{"key": "a", "task_id": "a", "kind": "task", "event_ids": ["review-a"]}]
    first = map_narrative.enrich(tmp_path, data, requests, "zh-CN", project_root=life)
    second = map_narrative.enrich(tmp_path, data, requests, "zh-CN", project_root=life)
    saved = second["cards"]["a"]
    assert second["cached"] and len(calls) == 1
    assert saved["reader_brief"] == brief()
    assert saved["event_ids"] == ["review-a"]
    assert saved["event_revisions"] == [data["events"][0]["revision"]]
    assert saved["task_status"] == "running" and saved["version"] == map_narrative.PROMPT_VERSION
    assert saved == first["cards"]["a"] and data == original
    assert before == {name: (life / name).read_bytes() for name in before}

    # A later review-state change makes the current brief eligible for refresh.
    memory.backlog.update("a", outcome={"execution_status": "completed", "review_status": "accepted"})
    source = data["id"] + ":zh-CN"
    cache = map_narrative.read_cache(tmp_path, source)
    cache["attempt_at"] = 0
    map_narrative._write_cache(map_narrative.cache_path(tmp_path, source), cache)
    map_narrative.enrich(tmp_path, read_map(sid, tmp_path, life), requests, "zh-CN", project_root=life)
    assert len(calls) == 2
    assert calls[1][0]["task"]["outcome"]["review_status"] == "accepted"


def test_requested_legacy_card_is_upgraded_without_losing_evidence(tmp_path, project, monkeypatch):
    sid, life, _ = project
    data = read_map(sid, tmp_path, life)
    path = map_narrative.cache_path(tmp_path, data["id"] + ":en-US")
    path.parent.mkdir()
    path.write_text(json.dumps({"cards": {"a": {
        "version": 9, "task_revision": data["tasks"][0]["revision"], "event_ids": ["review-a"],
        "title": "Legacy", "summary": "Old prose", "detail": "Old detail",
    }}, "relations": []}))
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "generate", lambda *args, **kwargs: {"cards": [{
        "key": "a", "title": "Refreshed", "summary": "Executor report", "detail": "Review remains unverified",
        "reader_brief": {**brief(), "concept": None},
    }], "relations": []})
    result = map_narrative.enrich(tmp_path, data, [
        {"key": "a", "task_id": "a", "kind": "task", "event_ids": ["review-a"]},
    ], "en-US", project_root=life)
    assert result["cards"]["a"]["reader_brief"]["concept"] is None
    assert result["cards"]["a"]["event_ids"] == ["review-a"]
    assert result["cards"]["a"]["version"] == map_narrative.PROMPT_VERSION
