"""Paper length and reference count are proxies, not standards.

Two arithmetic gates used to reject complete work: a 35-entry / 30-key
bibliography floor, and a rule that the Conclusion must not start before the
second-to-last page. Both punished short, complete papers and rewarded padding.
The venue page count is a *ceiling*; citation sufficiency is proportional to
what the paper claims. What must still fail is fabrication, over-length, and
wrong templates.

Full papers nevertheless have a near-limit writing target, developed through
principle-level analysis rather than an experiment report or a numerical gate.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from argus_skill.verticals.research import venue_profiles

SKILLS = (
    Path(__file__).resolve().parents[1]
    / "argus_skill"
    / "verticals"
    / "research"
    / "skills"
)


def _read(relative: str) -> str:
    return (SKILLS / relative).read_text(encoding="utf-8")


# -- no reference-count quota anywhere --------------------------------------

@pytest.mark.parametrize(
    "relative",
    [
        "reviewer/academic-paper-peer-review-benchmark.md",
        "reviewer/venue-academic-language-review.md",
        "engineer/venue-format-preflight.md",
    ],
)
def test_no_bibliography_count_floor_in_skills(relative) -> None:
    text = _read(relative)

    for banned in ("at least 35", "35 verified BibTeX", "30 unique cited keys"):
        assert banned not in text, f"{relative} still imposes a reference quota: {banned!r}"


def test_reference_count_is_not_a_hard_blocker() -> None:
    text = _read("reviewer/academic-paper-peer-review-benchmark.md")
    blockers = text.split(
        "## Problems that mean the paper does not hold yet", 1
    )[1].split("##", 1)[0]

    assert "BibTeX entries" not in blockers
    assert "cited keys" not in blockers
    # The real citation gate stays: entries must resolve and be genuine.
    assert "unresolved citations" in blockers
    assert "fabricated" in blockers


def test_venue_profiles_no_longer_carry_a_bibliography_quota() -> None:
    # These fields existed but had no consumer, so a venue that set 15 was
    # still judged against the skills' hard-coded 35.
    fields = {f.name for f in dataclasses.fields(venue_profiles.VenueProfile)}

    assert "min_verified_bib_entries" not in fields
    assert "min_cited_keys" not in fields


# -- page budget is a ceiling, not a quota ----------------------------------

def test_page_limit_is_described_as_a_ceiling() -> None:
    text = _read("engineer/venue-format-preflight.md")

    assert "ceiling, not a quota" in text
    # The old rule forced body content until the Conclusion reached page 7.
    assert "Conclusion must reach page 7" not in text
    assert "should not appear before page 6" not in text


def test_over_length_is_still_enforced() -> None:
    text = _read("engineer/venue-format-preflight.md")

    assert "exceeds the current limit" in text
    assert "reflow content" in text


def test_preflight_routes_expansion_to_the_writing_policy() -> None:
    text = _read("engineer/venue-format-preflight.md")

    assert "principle-led" in text
    assert "toward its writing target" in text


def test_full_paper_length_target_is_distinct_from_official_limit() -> None:
    text = " ".join(_read("research-paper-playbook.md").split())

    for required in (
        "uses nearly all",
        "not an official minimum",
        "Never silently switch tracks",
        "Do not infer body length from total PDF",
        "If the limit is unknown",
        "Actively expand principle-level analysis",
        "Distinguish derivation, proposed explanation, and empirical observation",
        "existing research notes",
    ):
        assert required in text


@pytest.mark.parametrize("role,operation", [
    ("engineer", "mission"),
    ("reviewer", "evaluate"),
])
def test_live_paper_prompts_carry_length_policy(role: str, operation: str) -> None:
    from argus_skill.verticals.research.prompt_policy import render_role_prompt_fragment

    text = render_role_prompt_fragment(
        role=role,
        operation=operation,
        stage="paper" if role == "engineer" else "review",
        scope="" if role == "engineer" else "final_submission",
        project_root=None,
    )
    assert "research-paper-playbook.md" in text
    assert "principle-level analysis" in text
    assert "derivations, and design tradeoffs" in text
    assert "technically complete" not in text
    assert "experiment report" in text
    assert "not total PDF pages" in text
    assert "short-paper and partial-edit requests" in text
    assert "full unused body page" not in text
    assert "shorter-paper exception" not in text


def test_paper_skills_favor_principle_led_expansion() -> None:
    for relative in (
        "research-paper-playbook.md",
        "research-review-playbook.md",
        "engineer/venue-paper-drafting.md",
        "engineer/references/paper-writing-craft.md",
    ):
        text = " ".join(_read(relative).split())
        assert "principle-level analysis" in text
        assert "technically complete" not in text
        assert "full unused body page" not in text
        assert "shorter-paper exception" not in text


def test_compression_has_no_fixed_reduction_target() -> None:
    for relative in (
        "engineer/venue-paper-drafting.md",
        "engineer/references/paper-writing-craft.md",
    ):
        text = " ".join(_read(relative).split())
        assert "a cut of a third is normal" not in text
        assert "target reduction fraction" in text


# -- the layout reviewer's underfill signal ---------------------------------

def _issue_call(code: str) -> dict:
    """Locate the `_issue(...)` call whose first argument is *code*.

    Parsed rather than string-matched: the surrounding comments deliberately
    mention the old behaviour, and neighbouring issues have their own flags.
    """
    import ast

    from argus_skill.verticals.research import paper_layout_review as mod

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "id", None) != "_issue" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == code:
            return {
                "severity": node.args[1].value if len(node.args) > 1 else None,
                "keywords": {kw.arg: kw for kw in node.keywords},
                "source": ast.unparse(node),
            }
    raise AssertionError(f"no _issue call for {code!r}")


def test_underfill_is_advisory_not_a_gate() -> None:
    call = _issue_call("rendered_main_body_underfilled")

    assert call["severity"] == "advisory"
    # hard_gate=True here rejected 6-page complete papers on arithmetic.
    assert "hard_gate" not in call["keywords"]


def test_underfill_message_tells_the_reviewer_what_to_actually_check() -> None:
    source = _issue_call("rendered_main_body_underfilled")["source"]

    assert "ceiling, not a quota" in source
    assert "actually missing" in source


def test_over_length_conclusion_remains_a_major_issue() -> None:
    # Exceeding the venue page limit is a real official constraint and must
    # keep its severity even though underfill was downgraded.
    assert _issue_call("conclusion_after_page_8")["severity"] == "major"
