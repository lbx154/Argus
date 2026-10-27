"""The prompt-rewrite preview reads its named lines; it never enters the handoff chain."""

from __future__ import annotations


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
