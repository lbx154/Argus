"""External fact guidance is available without treating metadata as permission."""
from argus.skills.builtins import iter_builtin_skill_texts
from argus.verticals._base import load_vertical_contract


def test_mutable_fact_skill_remains_global_and_separates_readiness_conditions():
    body = dict(iter_builtin_skill_texts())["engineer/stale-world-model.md"]
    normalized = " ".join(body.split())
    assert "identity, permission and execution" in body
    assert "does not grant downloads" in normalized
    assert "Download permission does not prove" in normalized
    assert "A 200 means you can have it" not in body
    assert "Do not start every task with a survey" in body
    assert "identity is the claim" in normalized


def test_research_role_still_links_current_fact_verification():
    engineer = load_vertical_contract("research").banner("engineer")
    assert "Verify current models" in engineer
    assert "live sources instead of memory" in engineer
