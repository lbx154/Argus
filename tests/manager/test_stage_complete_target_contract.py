"""Regression test: a correct COMPLETE must not be lost to a wording slip.

``parse_stage_decision`` used to accept ``complete`` only when ``target_stage``
was empty or exactly the current stage; anything else became a HOLD carrying
the diagnostic ``illegal_complete_target``. The policy bullet in the Manager
prompt does say to "COMPLETE at the current stage", but that reads as guidance
about *when* to complete — the format contract at the end of the prompt pinned
TARGET_STAGE for HOLD alone.

So a Manager that reasoned correctly still filled the field wrong. Testbed runs
11 (``s-b1a3757f``), 12 (``s-44cb57c7``) and 13 (``s-d9ea298f``) each emitted::

    ACTION=complete
    TARGET_STAGE=review
    REASON=Reviewer-certified final submission satisfies the scoped problem...

against ``current_stage=scope``. By that point run 13 had produced the search
program, a both-directions proof of the characterization, and a Lean 4 build
with no ``sorry`` and no ``axiom``, all reviewer-certified.

Two changes, one prompt and one parser, because a prompt-only fix leaves a hard
gate keyed on a probabilistic output:

* the format contract now pins the field for COMPLETE as well as HOLD;
* a COMPLETE naming a *later* stage becomes a one-step ADVANCE.

The second bullet is deliberately not "normalize to ``complete`` at the current
stage", which is what this file asserted on its first pass. Completion is only
legal at the *final* stage: ``final_stage_completion_decision`` returns ``None``
for any earlier one outside ``direct`` workflow mode, and the caller turns that
into ``manager_completion_rejected``. Normalizing therefore traded one HOLD for
another, and run 13 demonstrated the cost live — it sat at ``scope`` with the
problem solved while its Planner invented gate-metadata busywork to explain a
refusal it could not see.

ADVANCE is what the model meant and what the machine can execute. One step, not
a jump to the named target: ``advance_stage`` validates the stage being *left*,
so hopping ``scope -> review`` would skip ``solve``'s gate. Stepping converges
in as many ticks as there are stages with every gate still enforced.

An *earlier* or unknown target stays fail-closed: that is a model confusing
completion with a rollback, not a wording slip.

Third pass. Pinning the field in the prompt worked — and that is how run 15
(``s-f0dbba19``) failed. It emitted ``ACTION=complete`` / ``TARGET_STAGE=scope``
at ``current_stage=scope``, exactly as instructed, and was refused for
completing from a non-final stage. That shape is still not rewritten *here*:
naming a later stage says "the work through X is done", which is a request to
move and can be settled on shape alone, while naming the current stage says
"close the project", which the completion contract exists to answer. The rescue
for it therefore lives with the blockers, in
``test_stage_complete_nonfinal_deadlock.py``.
"""

from __future__ import annotations

import pytest

from argus.manager.stage_decider import parse_stage_decision

STAGES = ["scope", "solve", "review", "report"]


def _verdict(action: str, target: str, *, current: str = "scope"):
    return parse_stage_decision(
        f"ACTION={action}\nTARGET_STAGE={target}\nREASON=because",
        current_stage=current,
        stage_order=STAGES,
    )


def test_complete_at_the_current_stage_is_unchanged() -> None:
    """Left for the completion contract to answer, not settled here.

    Whether this one becomes a step forward depends on why completion is
    refused, which the parser cannot see — see
    ``test_stage_complete_nonfinal_deadlock.py``.
    """
    decision = _verdict("complete", "scope")

    assert decision.action == "complete"
    assert decision.diagnostic == "valid_complete"


def test_complete_with_no_target_is_unchanged() -> None:
    decision = parse_stage_decision(
        "ACTION=complete\nREASON=because",
        current_stage="scope",
        stage_order=STAGES,
    )

    assert decision.action == "complete"
    assert decision.diagnostic == "valid_complete"


@pytest.mark.parametrize("target", ["solve", "review", "report"])
def test_a_later_target_advances_one_step(target: str) -> None:
    """Runs 11, 12 and 13's exact verdict. ``review`` is the one they emitted."""
    decision = _verdict("complete", target)

    assert decision.action == "advance"
    assert decision.target_stage == "solve"
    assert decision.diagnostic == "complete_target_advanced"


def test_a_later_target_never_skips_an_intervening_gate() -> None:
    """``advance_stage`` only validates the stage being left.

    Jumping straight to the named target would carry ``solve`` past its own
    completion validator without ever running it.
    """
    decision = _verdict("complete", "report")

    assert decision.target_stage == STAGES[1]
    assert decision.target_stage != "report"


def test_the_deviation_is_still_named_in_the_trace() -> None:
    """Rewriting the action must not make the slip invisible to an operator."""
    assert _verdict("complete", "review").diagnostic != _verdict(
        "complete", "scope"
    ).diagnostic


def test_the_reason_survives_the_rewrite() -> None:
    """The Manager's justification is the only account of why it moved."""
    assert _verdict("complete", "review").reason == "because"


def test_an_earlier_target_stays_fail_closed() -> None:
    decision = _verdict("complete", "solve", current="review")

    assert decision.action == "hold"
    assert decision.diagnostic == "illegal_complete_target"


def test_an_unknown_target_stays_fail_closed() -> None:
    decision = _verdict("complete", "publication")

    assert decision.action == "hold"
    assert decision.diagnostic == "illegal_complete_target"


@pytest.mark.parametrize("action", ["HOLD", "COMPLETE"])
def test_the_prompt_pins_target_stage_for_both_actions(action: str) -> None:
    """The format contract must name both actions where the field is defined.

    The parser is forgiving now, but a verdict that needs rewriting is still a
    verdict the operator has to read past.
    """
    from argus.roles.prompts import manager as manager_prompts

    with open(manager_prompts.__file__, encoding="utf-8") as handle:
        text = handle.read()

    marker = "set TARGET_STAGE to the current stage"
    assert marker in text
    line = next(ln for ln in text.splitlines() if marker in ln)
    assert action in line.upper(), (
        f"the TARGET_STAGE format rule does not mention {action}; a Manager "
        "filling the field for that action has nothing to go on"
    )
