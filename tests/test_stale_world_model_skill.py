"""External fact guidance is available without treating metadata as permission."""
from argus.skills.builtins import iter_builtin_skill_texts


def test_mutable_fact_skill_remains_global_and_separates_readiness_conditions():
    body = dict(iter_builtin_skill_texts())["stale-blocker-verification-probe.md"]
    normalized = " ".join(body.split())
    assert "identity, permission and execution" in body
    assert "does not grant downloads" in normalized
    assert "Download permission does not prove" in normalized
    assert "A 200 means you can have it" not in body
    assert "Do not start every task with a survey" in body
    assert "identity is the claim" in normalized
