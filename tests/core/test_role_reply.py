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

from argus.core.role_reply import (
    read_bool,
    read_key_values,
)


def test_bools_read_the_words_models_use() -> None:
    values = read_key_values("A=yes\nB=No\nC=maybe", ("A", "B", "C"))

    assert read_bool(values, "A") is True
    assert read_bool(values, "B") is False
    assert read_bool(values, "C", default=True) is True


# -- values that are genuinely prose -----------------------------------------


# -- the stage decision, converted off forced JSON ---------------------------


def test_a_verbatim_live_rewrite_parses_with_its_questions() -> None:
    """Captured from copilot on 2026-07-26 against the converted prompt.

    Worth keeping whole because of *what* it does: it turns "faster" into a
    measurable outcome without inventing a number, says so explicitly, and puts
    the target speed-up in QUESTIONS as a proposal for the operator. That is the
    standing instruction — propose a metric constraint by asking, never by
    assuming — and this fixture is the evidence it survives the format change.
    """
    from argus.manager.prompt_rewrite import parse_rewrite_text

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


# -- the operator-answer ruling ----------------------------------------------


def test_skill_placements_keep_their_shape_and_their_fallback() -> None:
    from argus.manager.skill_review import _named_placements

    named = _named_placements(
        "CANDIDATE_ID=sk-1\nPLACEMENT=stay\nVERTICAL=\nWHY=too specific\n"
    )

    assert named is not None
    assert named["placements"][0]["candidate_id"] == "sk-1"
    assert _named_placements("no records at all") is None, (
        "returning None is what keeps the JSON reader reachable"
    )


def test_a_single_placement_uses_the_batch_contract(monkeypatch) -> None:
    from argus.manager import skill_review

    calls: list[dict] = []

    class _Result:
        last_agent_message = (
            "CANDIDATE_ID=single\n"
            "PLACEMENT=global\n"
            "VERTICAL=\n"
            "WHY=no assumptions\n"
        )

    def _run(_runner, **kwargs):
        calls.append(kwargs)
        return _Result()

    monkeypatch.setattr(skill_review, "gateway_run_exec", _run)
    verdict = skill_review.classify_skill_placement(
        content="Reusable method",
        task="Ship a result",
        candidate_verticals=["software"],
        runner=object(),
    )

    assert verdict == skill_review.PlacementVerdict("global", "", "no assumptions")
    assert calls[0]["run_label"] == "manager.skill_placement_batch"
    assert '"candidate_id": "single"' in calls[0]["prompt"]
