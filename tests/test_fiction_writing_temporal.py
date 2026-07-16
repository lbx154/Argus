"""Unit tests for the deterministic TEMPORAL/age-consistency check.

The class of bug that had NO home before (fields didn't exist): a stated age that
contradicts current_year − birth_year, a birth in the future, or a timeline whose
order and year disagree. Also proves the engine STORES the new fields via a patch
round-trip while doing NO arithmetic itself.
"""
from __future__ import annotations

from argus_skill.verticals.fiction_writing.state import apply_patch, validate_state
from argus_skill.verticals.fiction_writing.temporal import (
    TEMPORAL_FINDING_TYPE,
    check_temporal_consistency,
)


def _state_with(**meta_clock):
    s, _ = apply_patch(None, {"patch_id": "p", "ops": [
        {"op": "set_meta", "set": {"world_clock": meta_clock}},
    ]})
    return s


def test_age_contradicts_birth_and_clock_is_blocking():
    s, _ = apply_patch(None, {"patch_id": "p", "ops": [
        {"op": "set_meta", "set": {"world_clock": {"current_year": 2042}}},
        {"op": "add_character", "id": "c", "value": {"name": "林默", "birth_year": 2008, "age": 20}},
    ]})
    findings = check_temporal_consistency(s)
    assert findings
    assert all(f["type"] == TEMPORAL_FINDING_TYPE and f["blocking"] for f in findings)
    assert "34" in findings[0]["detail"]  # 2042 - 2008 = 34, but stated 20


def test_consistent_age_is_silent():
    s, _ = apply_patch(None, {"patch_id": "p", "ops": [
        {"op": "set_meta", "set": {"world_clock": {"current_year": 2042}}},
        {"op": "add_character", "id": "c", "value": {"name": "林默", "birth_year": 2008, "age": 34}},
    ]})
    assert check_temporal_consistency(s) == []


def test_birth_in_the_future_is_flagged():
    s, _ = apply_patch(None, {"patch_id": "p", "ops": [
        {"op": "set_meta", "set": {"world_clock": {"current_year": 2042}}},
        {"op": "add_character", "id": "c", "value": {"name": "X", "birth_year": 2050}},
    ]})
    detail = " ".join(f["detail"] for f in check_temporal_consistency(s))
    assert "future" in detail


def test_timeline_order_year_inversion_is_flagged():
    # models the 白签名 shape: a licensing event ordered AFTER the system launch
    # but carrying an earlier year than the launch.
    s, _ = apply_patch(None, {"patch_id": "p", "ops": [
        {"op": "add_timeline", "value": {"id": "t_launch", "order": 1, "year": 2042,
                                         "label": "记忆公证系统启用"}},
        {"op": "add_timeline", "value": {"id": "t_license", "order": 2, "year": 2028,
                                         "label": "林默获鉴定师执照"}},
    ]})
    findings = check_temporal_consistency(s)
    assert findings
    assert all(f["blocking"] for f in findings)


def test_all_absent_numbers_stay_silent():
    # a story that opts out of temporal tracking is never flagged
    s, _ = apply_patch(None, {"patch_id": "p", "ops": [
        {"op": "add_character", "id": "c", "value": {"name": "林默"}},
        {"op": "add_timeline", "value": {"id": "t1", "order": 1, "label": "开场"}},
    ]})
    assert check_temporal_consistency(s) == []


def test_engine_stores_new_fields_and_stays_schema_valid():
    s, res = apply_patch(None, {"patch_id": "p1", "ops": [
        {"op": "set_meta", "set": {"world_clock": {"calendar": "gregorian", "current_year": 2042}}},
        {"op": "add_character", "id": "c", "value": {"name": "林默", "birth_year": 2008, "age": 34}},
        {"op": "add_timeline", "value": {"id": "t1", "order": 1, "year": 2042, "label": "案发"}},
    ]})
    assert res["applied"] is True
    validate_state(s)
    assert s["meta"]["world_clock"]["current_year"] == 2042
    assert s["characters"]["c"]["birth_year"] == 2008
    assert s["characters"]["c"]["age"] == 34
    assert s["timeline"][0]["year"] == 2042
    # update_character can also set age/birth_year
    s2, _ = apply_patch(s, {"patch_id": "p2", "ops": [
        {"op": "update_character", "id": "c", "set": {"age": 35}},
    ]})
    validate_state(s2)
    assert s2["characters"]["c"]["age"] == 35
