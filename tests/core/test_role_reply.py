"""A role states its decision in prose; the harness reads the lines it needs.

Operator directive (2026-07-26): no role is forced to emit a JSON Schema. A
model told to reply with "ONE JSON object and NOTHING else" spends its answer
satisfying a serialiser, cannot explain itself, and fails the entire decision
when it adds one sentence of context.

The replacement is the convention the Planner has always used —
``PROJECT_DONE=`` / ``REASON=`` on their own lines — generalised. These tests
are about the property that makes it work: the reader tolerates everything a
model naturally does around those lines.
"""

from __future__ import annotations

from argus_skill.core.role_reply import (
    legacy_json_object,
    read_bool,
    read_float,
    read_key_values,
    read_list,
    read_optional,
)

_KEYS = ("VERTICAL", "WORKFLOW_MODE", "CONFIDENCE", "RATIONALE", "TARGET_VENUE")


def test_prose_around_the_decision_costs_nothing() -> None:
    """The point of the change: the role may think out loud and still be read."""
    reply = """
I looked at the repo. There is a CUDA kernel under src/ and a bench harness,
so this is kernel work rather than a paper.

VERTICAL=kernel_engineering
WORKFLOW_MODE=staged
CONFIDENCE=0.82

I chose staged because the speedup bar needs repeated profile/measure cycles.
"""

    values = read_key_values(reply, _KEYS)

    assert values["VERTICAL"] == "kernel_engineering"
    assert values["WORKFLOW_MODE"] == "staged"
    assert read_float(values, "CONFIDENCE") == 0.82


def test_the_shapes_a_model_actually_writes_are_all_accepted() -> None:
    reply = """
- VERTICAL: kernel_engineering
**WORKFLOW_MODE**= staged
`ARGUS_CONFIDENCE` = 0.9
"""

    values = read_key_values(reply, _KEYS)

    assert values["VERTICAL"] == "kernel_engineering"
    assert values["WORKFLOW_MODE"] == "staged"
    assert read_float(values, "CONFIDENCE") == 0.9


def test_a_code_fence_around_the_answer_does_not_break_it() -> None:
    reply = "```\nVERTICAL=research\nWORKFLOW_MODE=staged\n```"

    values = read_key_values(reply, _KEYS)

    assert values["VERTICAL"] == "research"


def test_a_restated_conclusion_wins() -> None:
    """Models often revise mid-answer; a human reads the last word as final."""
    reply = "VERTICAL=research\n\nOn reflection that is wrong.\n\nVERTICAL=kernelbench"

    assert read_key_values(reply, _KEYS)["VERTICAL"] == "kernelbench"


def test_an_unanswered_key_is_absent_not_empty() -> None:
    """A caller must be able to tell "did not answer" from "answered nothing"."""
    values = read_key_values("VERTICAL=research", _KEYS)

    assert "TARGET_VENUE" not in values
    assert values.get("TARGET_VENUE") is None


def test_a_declined_value_reads_as_empty() -> None:
    values = read_key_values("TARGET_VENUE=none", _KEYS)

    assert "TARGET_VENUE" in values
    assert read_optional(values, "TARGET_VENUE") == ""


def test_a_list_splits_on_semicolons_not_commas() -> None:
    """A requirement contains commas far more often than semicolons.

    Splitting on commas would cut "at least 1.5x, measured over 10 runs" in
    half and turn one requirement into two false ones.
    """
    values = read_key_values(
        "CONSTRAINTS=at least 1.5x, measured over 10 runs; must fit in 40GB",
        ("CONSTRAINTS",),
    )

    assert read_list(values, "CONSTRAINTS") == (
        "at least 1.5x, measured over 10 runs",
        "must fit in 40GB",
    )


def test_a_value_containing_an_equals_sign_survives() -> None:
    values = read_key_values("RATIONALE=chose staged because n=10 runs are needed", _KEYS)

    assert values["RATIONALE"] == "chose staged because n=10 runs are needed"


def test_bools_read_the_words_models_use() -> None:
    values = read_key_values("A=yes\nB=No\nC=maybe", ("A", "B", "C"))

    assert read_bool(values, "A") is True
    assert read_bool(values, "B") is False
    assert read_bool(values, "C", default=True) is True


def test_a_key_that_is_a_prefix_of_another_is_not_confused() -> None:
    """`TASK` must not swallow `TASK_TITLE`."""
    values = read_key_values("TASK_TITLE=make it faster", ("TASK", "TASK_TITLE"))

    assert values.get("TASK_TITLE") == "make it faster"
    assert "TASK" not in values


# -- the legacy door, deliberately still open --------------------------------


def test_a_volunteered_json_object_still_parses() -> None:
    """Not required, but a daemon mid-flight on an older prompt must not break."""
    assert legacy_json_object('{"vertical": "research"}') == {"vertical": "research"}
    assert legacy_json_object('```json\n{"vertical": "research"}\n```') == {
        "vertical": "research"
    }
    assert legacy_json_object("here you go: {\"vertical\": \"research\"} ok") == {
        "vertical": "research"
    }


def test_prose_with_no_json_is_not_forced_into_an_object() -> None:
    assert legacy_json_object("I think it is kernel work.") is None


# -- the real thing: a verbatim live-model reply -----------------------------


def test_a_verbatim_live_model_reply_routes() -> None:
    """Captured from copilot against the converted prompt on 2026-07-26.

    A hand-written fixture proves the parser; only a real reply proves the
    prompt. This is the actual text the model produced, unedited.
    """
    from argus_skill.manager.domain_author import parse_fast_vertical_decision
    from argus_skill.skills import vertical_select

    reply = (
        "CHOICE=existing\n"
        "VERTICAL=kernel_engineering\n"
        "DOMAIN=none\n"
        "WORKFLOW_MODE=direct\n"
        "CONFIDENCE=0.88\n"
        "RESEARCH_TARGET_LEVEL=none\n"
        "TARGET_VENUE=none\n"
        "RATIONALE=Explicit GPU kernel optimization request (attention kernel, "
        "named hardware target B200, quantitative speedup bar vs PyTorch "
        "baseline) maps directly to the built-in kernel_engineering vertical."
    )

    route = parse_fast_vertical_decision(
        reply, known_verticals=vertical_select.VERTICALS
    )

    assert route is not None
    assert route.needs_grounding is False
    assert route.vertical == "kernel_engineering"
    assert route.workflow_mode == "direct"
    assert route.confidence == 0.88
    assert route.research_target_level == ""


def test_a_daemon_still_answering_in_json_is_not_broken() -> None:
    """Sixteen daemons are mid-flight on the older prompt.

    JSON is no longer asked for, but refusing it would have made this change a
    breaking one for every run already in progress.
    """
    from argus_skill.manager.domain_author import parse_fast_vertical_decision
    from argus_skill.skills import vertical_select

    route = parse_fast_vertical_decision(
        '{"choice":"existing","vertical":"kernel_engineering",'
        '"workflow_mode":"direct","confidence":0.9,"rationale":"x"}',
        known_verticals=vertical_select.VERTICALS,
    )

    assert route is not None and route.vertical == "kernel_engineering"


def test_the_routing_prompt_no_longer_demands_json() -> None:
    from argus_skill.roles.prompts.manager import (
        build_fast_vertical_decision_prompt,
        build_vertical_decision_prompt,
    )

    fast = build_fast_vertical_decision_prompt(
        task="make it faster",
        verticals_with_purpose={"software": ""},
        domains_with_purpose={},
    )
    grounded = build_vertical_decision_prompt(
        "make it faster",
        verticals_with_purpose={"software": ""},
        domains_with_purpose={},
    )

    assert "JSON" not in fast
    assert "JSON" not in grounded
    assert "CHOICE=existing" in fast and "CHOICE=existing" in grounded


# -- values that are genuinely prose -----------------------------------------

_VERDICT = ("STATUS", "REASON", "NEXT_ACTION", "OPERATOR_QUESTION")


def test_a_multi_paragraph_reason_is_kept_whole() -> None:
    """A Reviewer writing several paragraphs is writing well, not wrongly."""
    from argus_skill.core.role_reply import read_block

    reply = """STATUS=continue
REASON=The kernel is 1.2x, not the 1.5x the operator asked for.

I re-ran the benchmark ten times; the spread is 1.17-1.24x, so this is not
noise. The fused epilogue is the bottleneck.
NEXT_ACTION=Fuse the epilogue and re-measure.
OPERATOR_QUESTION=none
"""

    reason = read_block(reply, "REASON", _VERDICT)

    assert reason.startswith("The kernel is 1.2x")
    assert "spread is 1.17-1.24x" in reason
    assert "NEXT_ACTION" not in reason
    assert read_key_values(reply, _VERDICT)["NEXT_ACTION"] == (
        "Fuse the epilogue and re-measure."
    )


def test_a_block_stops_at_the_next_key_not_at_the_end() -> None:
    from argus_skill.core.role_reply import read_block

    reply = "REASON=first\nstill first\nSTATUS=done\nnot the reason"

    assert read_block(reply, "REASON", _VERDICT) == "first\nstill first"


def test_a_missing_block_is_empty_not_the_whole_reply() -> None:
    from argus_skill.core.role_reply import read_block

    assert read_block("STATUS=done", "REASON", _VERDICT) == ""
