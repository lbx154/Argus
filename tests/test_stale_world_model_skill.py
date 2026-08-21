"""Naming a model from memory is a guess wearing a specific name.

An agent's knowledge of what exists stops at training time and then decays,
but it does not decay noticeably from the inside — a superseded checkpoint
still feels current, with the confidence it had when it was. The failure is
never hesitation; it is a fluent, specific, wrong name.
"""

from __future__ import annotations

from argus_skill.skills.builtins import iter_builtin_skill_texts
from argus_skill.verticals._base import load_vertical, vertical_role_banner

SKILL = "engineer/stale-world-model.md"


def _skill() -> str:
    return dict(iter_builtin_skill_texts())[SKILL]


def test_the_skill_is_available_to_every_vertical() -> None:
    """Stale knowledge is not a research problem; every domain names versions."""
    assert SKILL in dict(iter_builtin_skill_texts())


def test_it_names_the_categories_that_actually_move() -> None:
    body = _skill()
    for moving in ("model and checkpoint names", "library and framework versions",
                   "benchmark names", "API endpoints"):
        assert moving in body
    # And the things that do not, so the rule stays cheap to follow.
    assert "mathematics" in body


def test_it_gives_a_check_that_costs_one_call() -> None:
    body = _skill()
    assert "huggingface.co/api/models" in body
    assert "pip index versions" in body
    assert "401/403" in body


def test_unavailable_is_a_substitution_not_a_blocker() -> None:
    """Three campaign runs blocked on one gated checkpoint rather than swapping."""
    body = _skill()
    assert "substitution, not a blocker" in body
    # Except where identity is the claim, which is the honest carve-out.
    assert "identity is the claim" in body


def test_the_research_engineer_is_pointed_at_it() -> None:
    engineer = vertical_role_banner(load_vertical("research"), "engineer")
    assert "stale-world-model.md" in engineer
    assert "probe it before the plan hardens" in engineer


def test_it_is_discoverable_from_any_domain() -> None:
    """Naming a checkpoint from memory is not a research habit — it is what any
    domain does when nobody says look first. The engineer's fixed prompt is a
    hard 2500-char contract, so the rule reaches every vertical through skill
    matching: the description has to name the triggers rather than a field."""
    front = _skill().split("---")[1]

    assert "ANY domain" in front
    for trigger in ("checkpoint", "version", "API endpoint", "benchmark",
                    "baseline number", "gated"):
        assert trigger in front


def test_it_opens_with_the_stance_not_a_lookup_table() -> None:
    """The point is to start from an accurate estimate of one's own freshness,
    which produces an action; a checklist of things to grep does not."""
    body = " ".join(_skill().split())
    assert "Start ignorant, on purpose" in body
    assert "you cannot miss what you do not know exists" in body
    assert "before the plan hardens" in body
    # And it has to cash out as a routine, or it is only a slogan.
    assert "Orienting, at the start of domain work" in body
    assert "What is the strongest thing my work will be compared against?" in body
