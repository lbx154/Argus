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


def test_a_leading_status_emoji_does_not_hide_the_decision() -> None:
    values = read_key_values(
        "📢 CHOICE=existing\nVERTICAL=software",
        ("CHOICE", "VERTICAL"),
    )

    assert values == {
        "CHOICE": "existing",
        "VERTICAL": "software",
    }


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


# -- the stage decision, converted off forced JSON ---------------------------


def test_a_verbatim_live_stage_verdict_parses() -> None:
    """Captured from copilot against the converted stage prompt on 2026-07-26.

    Kept verbatim, prose and all, because the point of the change is that the
    Manager may reason out loud around its verdict — a fixture that omitted the
    prose would not be testing the thing that changed.
    """
    from argus_skill.manager.stage_decider import parse_stage_decision

    reply = (
        "ADVANCE is both illegal (no next stage) and unsupported. HOLD is the "
        "only legal and honest move.\n"
        "\n"
        "ACTION=hold\n"
        "TARGET_STAGE=delivery\n"
        "REASON=All three delivery checklist items are unmet with zero "
        "supporting evidence.\n"
        "LIVE_VIEW_PATHS=notes.md; CHECKPOINT.md\n"
        "LIVE_VIEW_TITLE=Delivery held: no implementation artifacts exist\n"
        "LIVE_VIEW_REASON=The working directory is empty.\n"
    )

    decision = parse_stage_decision(
        reply, current_stage="delivery", stage_order=["delivery"]
    )

    assert decision.action == "hold"
    assert decision.target_stage == "delivery"
    assert "checklist items are unmet" in decision.reason


def test_the_same_reply_carries_the_live_view_choice() -> None:
    from argus_skill.manager.live_view import parse_live_view_response

    decided, view = parse_live_view_response(
        "ACTION=hold\n"
        "LIVE_VIEW_PATHS=notes.md; CHECKPOINT.md\n"
        "LIVE_VIEW_TITLE=Delivery held\n"
        "LIVE_VIEW_REASON=nothing was built\n"
    )

    assert decided is True
    assert view is not None
    assert view.title == "Delivery held"
    assert len(view.paths) == 2


def test_an_empty_live_view_line_clears_the_panel() -> None:
    """Distinct from never mentioning it, which must leave the panel alone."""
    from argus_skill.manager.live_view import parse_live_view_response

    cleared, view = parse_live_view_response("ACTION=hold\nLIVE_VIEW_PATHS=\n")
    untouched, _ = parse_live_view_response("ACTION=hold\n")

    assert cleared is True and view is None
    assert untouched is False


def test_a_stage_verdict_still_parses_from_volunteered_json() -> None:
    from argus_skill.manager.stage_decider import parse_stage_decision

    decision = parse_stage_decision(
        '{"action":"hold","target_stage":"delivery","reason":"not yet"}',
        current_stage="delivery",
        stage_order=["delivery"],
    )

    assert decision.action == "hold" and decision.reason == "not yet"


def test_the_stage_prompt_no_longer_demands_json() -> None:
    from types import SimpleNamespace

    from argus_skill.roles.prompts.manager import build_stage_decision_prompt

    review = SimpleNamespace(
        status="done", reason="r", next_action="", operator_question="", checklist=[]
    )
    prompt = build_stage_decision_prompt(
        current_stage="delivery",
        next_stage="",
        earlier_stages=[],
        checklist_md="- x",
        review=review,
        planner_verdict=None,
        rendering_block="",
        open_ended=True,
        continuous_objective="obj",
    )

    assert "JSON" not in prompt
    assert "ACTION=advance|hold|rollback" in prompt


# -- prompt rewrite ----------------------------------------------------------


def test_a_verbatim_live_rewrite_parses_with_its_questions() -> None:
    """Captured from copilot on 2026-07-26 against the converted prompt.

    Worth keeping whole because of *what* it does: it turns "faster" into a
    measurable outcome without inventing a number, says so explicitly, and puts
    the target speed-up in QUESTIONS as a proposal for the operator. That is the
    standing instruction — propose a metric constraint by asking, never by
    assuming — and this fixture is the evidence it survives the format change.
    """
    from argus_skill.manager.prompt_rewrite import parse_rewrite_text

    reply = (
        "REWRITTEN=Optimise \"the kernel\" so it runs faster than it does today. "
        "Deliverable: (a) the modified kernel source, (b) a before/after timing "
        "comparison on the same workload/hardware, and (c) confirmation that the "
        "kernel still passes its existing tests.\n"
        "\n"
        "CHANGES=Turned \"faster\" into a measurable outcome since \"faster\" is "
        "only meaningful against a baseline; Left the target speed-up out of the "
        "rewrite because the operator never specified it\n"
        "QUESTIONS=Which kernel do you mean — a GPU/CUDA compute kernel, an OS "
        "kernel, or a numerical kernel?; What speed-up counts as success? I "
        "propose targeting >=2x on the profiled hot path — acceptable?\n"
    )

    rewrite = parse_rewrite_text(reply)

    assert rewrite.rewritten.startswith("Optimise")
    assert "before/after timing" in rewrite.rewritten
    assert len(rewrite.changes) == 2
    assert len(rewrite.questions) == 2
    assert "REWRITTEN" not in rewrite.rewritten
    assert "CHANGES" not in rewrite.rewritten, (
        "the block must stop at the next named key, not swallow the rest"
    )


def test_a_plain_prose_reply_is_still_used_as_the_rewrite() -> None:
    """Models sometimes just answer; throwing that away is worse than using it."""
    from argus_skill.manager.prompt_rewrite import parse_rewrite_text

    rewrite = parse_rewrite_text("Make the attention kernel at least 1.5x faster.")

    assert rewrite.rewritten == "Make the attention kernel at least 1.5x faster."


def test_a_volunteered_json_rewrite_still_parses() -> None:
    from argus_skill.manager.prompt_rewrite import parse_rewrite_text

    rewrite = parse_rewrite_text(
        '{"rewritten":"do the thing","changes":["a"],"questions":["b"]}'
    )

    assert rewrite.rewritten == "do the thing"
    assert rewrite.changes == ["a"] and rewrite.questions == ["b"]


# -- the operator-answer ruling ----------------------------------------------


def _rule(text: str):
    from argus_skill.webapi.manager_pending_question import (
        _parse_pending_question_decision,
    )

    return _parse_pending_question_decision(text)


def test_an_operator_answer_ruling_reads_from_named_lines() -> None:
    ruling = _rule(
        "The operator has told us no GPU exists, which supersedes the inherited\n"
        "requirement to measure on hardware.\n"
        "\n"
        "IS_ANSWER=true\n"
        "RESOLVED=true\n"
        "DECISION=Do not wait for a GPU. Implement the kernel logic and a CPU\n"
        "correctness harness, and state that TFLOPS could not be measured.\n"
        "REPLY=\n"
    )

    assert ruling is not None
    assert ruling["is_answer"] is True and ruling["resolved"] is True
    assert "CPU\ncorrectness harness" in ruling["decision"]


def test_an_unreadable_ruling_is_none_not_a_confident_no() -> None:
    """The failure that would hurt an operator most.

    Defaulting a missing boolean to False turns "I could not read our own
    Manager's reply" into "your message was not an answer" — the operator is
    told they were ignored because of our parsing, not their words.
    """
    assert _rule("I think the operator means we should proceed on CPU.") is None
    assert _rule("IS_ANSWER=true\n") is None, "a missing RESOLVED is not a False"
    assert _rule("IS_ANSWER=maybe\nRESOLVED=true\n") is None


def test_a_resolved_ruling_without_an_instruction_is_refused() -> None:
    """Resolving with nothing for the team to do is not a resolution."""
    assert _rule("IS_ANSWER=true\nRESOLVED=true\nDECISION=\n") is None


def test_a_volunteered_json_ruling_still_parses() -> None:
    ruling = _rule(
        '{"is_answer": true, "resolved": false, "decision": "", '
        '"reply": "which kernel?"}'
    )

    assert ruling is not None and ruling["reply"] == "which kernel?"


# -- the live-view panel and its authored content ----------------------------

_PANEL = (
    "Delivery is held; here is what the operator should see.\n"
    "\n"
    "LIVE_VIEW_PATHS=.argus/live/status.md; README.md\n"
    "LIVE_VIEW_TITLE=Delivery held\n"
    "LIVE_VIEW_REASON=no artifacts exist yet\n"
    "\n"
    "PRESENTATION=.argus/live/status.md\n"
    "```\n"
    "# Delivery status\n"
    "\n"
    "Nothing has been built yet.\n"
    "\n"
    "- checklist: 0/3\n"
    "```\n"
)


def test_authored_panel_content_survives_its_own_blank_lines() -> None:
    """File content is the one field that genuinely needs a delimiter.

    It is multi-line and may contain anything, so a flat `KEY=value` cannot
    carry it. A fenced block is what a model writes for file content anyway.
    """
    from argus_skill.manager.live_view import parse_manager_presentations

    presentations = parse_manager_presentations(_PANEL)

    assert len(presentations) == 1
    assert presentations[0].path == ".argus/live/status.md"
    assert presentations[0].content.count("\n") == 4
    assert "checklist: 0/3" in presentations[0].content


def test_the_same_reply_also_carries_the_panel_selection() -> None:
    from argus_skill.manager.live_view import parse_live_view_response

    decided, view = parse_live_view_response(_PANEL)

    assert decided is True
    assert view is not None and view.title == "Delivery held"
    assert len(view.paths) == 2


def test_a_path_with_no_content_block_is_dropped_not_guessed() -> None:
    """The caller replaces a missing presentation with a status page.

    Inventing content would put Manager-attributed prose in front of the
    operator that the Manager never wrote.
    """
    from argus_skill.manager.live_view import parse_manager_presentations

    assert parse_manager_presentations("PRESENTATION=.argus/live/a.md\n") == ()


def test_a_path_outside_the_managed_directory_is_refused() -> None:
    from argus_skill.manager.live_view import parse_manager_presentations

    assert parse_manager_presentations(
        "PRESENTATION=/etc/passwd\n```\nx\n```\n"
    ) == ()


def test_volunteered_json_presentations_still_parse() -> None:
    from argus_skill.manager.live_view import parse_manager_presentations

    presentations = parse_manager_presentations(
        '{"presentations":[{"path":".argus/live/a.md","content":"x"}]}'
    )

    assert len(presentations) == 1


# -- repeated records --------------------------------------------------------


def test_several_verdicts_are_read_as_separate_records() -> None:
    """`read_key_values` keeps the last occurrence, which is wrong for a list."""
    from argus_skill.core.role_reply import read_records

    reply = (
        "Here is how I would file them.\n"
        "\n"
        "CANDIDATE_ID=sk-1\n"
        "PLACEMENT=global\n"
        "VERTICAL=\n"
        "WHY=nothing domain specific in it\n"
        "\n"
        "CANDIDATE_ID=sk-2\n"
        "PLACEMENT=vertical\n"
        "VERTICAL=kernelbench\n"
        "WHY=assumes a CUDA toolchain\n"
    )

    records = read_records(
        reply, ("CANDIDATE_ID", "PLACEMENT", "VERTICAL", "WHY"), start_key="CANDIDATE_ID"
    )

    assert [r["CANDIDATE_ID"] for r in records] == ["sk-1", "sk-2"]
    assert records[1]["VERTICAL"] == "kernelbench"
    assert records[0]["VERTICAL"] == ""


def test_a_reply_with_no_records_reads_as_none_of_them() -> None:
    from argus_skill.core.role_reply import read_records

    assert read_records("nothing here", ("A", "B"), start_key="A") == []


def test_skill_placements_keep_their_shape_and_their_fallback() -> None:
    from argus_skill.manager.skill_review import _named_placements

    named = _named_placements(
        "CANDIDATE_ID=sk-1\nPLACEMENT=stay\nVERTICAL=\nWHY=too specific\n"
    )

    assert named is not None
    assert named["placements"][0]["candidate_id"] == "sk-1"
    assert _named_placements("no records at all") is None, (
        "returning None is what keeps the JSON reader reachable"
    )


def test_a_single_placement_verdict_reads_from_named_lines() -> None:
    from argus_skill.manager.skill_review import _named_placement

    verdict = _named_placement(
        "This one is reusable anywhere.\n\nPLACEMENT=global\nVERTICAL=\nWHY=no assumptions\n"
    )

    assert verdict == {"placement": "global", "vertical": "", "why": "no assumptions"}
    assert _named_placement("just prose") is None
