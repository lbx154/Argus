"""Decompositions: what a route can settle, and the several things it cannot.

The six things PR3 asked for, in the order it asked for them. Three of them —
a route's obligations are an AND, a shared lemma is proved once, an exact
counterexample refutes — were already true of the kernel PR1 shipped, and are
*pinned* here rather than implemented; each of those tests says so in its own
docstring, because "already true" and "newly built" are different claims about
this package and a reader cannot tell them apart from a green suite. What is
new is the OR (a claim now reports the routes aimed at it) and cycle detection
(refused when written, reported when loaded).

Everything below turns on one rule: a route asserts that its obligations imply
its goal, and nothing in this package checks that implication. So a finished
route does not prove the goal, a dead route does not refute it, and both facts
have to be *said* — silence about either one reads as the opposite.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.proof_ledger import (
    ClaimStatus,
    ClaimVersion,
    ContextVersion,
    EvidenceRecord,
    EvidenceTier,
    MathState,
    MathStateError,
    ProofRoute,
    RouteStatus,
    Verdict,
    assess_claim,
    load_state,
    route_cycles,
    save_state,
)

# -- fixtures ---------------------------------------------------------------


def _context() -> ContextVersion:
    return ContextVersion(
        context_id="ctx",
        version=1,
        statement="Bound the number of unit distances among n planar points.",
        definitions={"unit distance": "a pair of points at Euclidean distance 1"},
    )


def _claim(context: ContextVersion, claim_id: str) -> ClaimVersion:
    return ClaimVersion(
        claim_id=claim_id,
        version=1,
        context=context.ref(),
        natural_statement=f"Statement of {claim_id}.",
        formal_statement=f"theorem {claim_id.replace('-', '_')} : ...",
    )


def _proved(claim: ClaimVersion) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"ev-{claim.claim_id}",
        subject=claim.ref(),
        tier=EvidenceTier.MECHANICAL,
        verdict=Verdict.SUPPORTS,
        produced_by="lean_check 4.9.0",
        artifact=f"research/lean/{claim.claim_id}.json",
    )


def _counterexample(claim: ClaimVersion, *, artifact: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"ev-cex-{claim.claim_id}",
        subject=claim.ref(),
        tier=EvidenceTier.COMPUTATIONAL,
        verdict=Verdict.REFUTES,
        produced_by="python 3.11 exhaustive search to n=40",
        artifact=(
            f"research/code/{claim.claim_id}-counterexample.json"
            if artifact is None
            else artifact
        ),
    )


def _project(*claim_ids: str) -> tuple[MathState, dict[str, ClaimVersion]]:
    state = MathState()
    context = state.add_context(_context())
    return state, {
        claim_id: state.add_claim(_claim(context, claim_id)) for claim_id in claim_ids
    }


# -- criterion 1: two alternative routes are an OR --------------------------


def test_two_routes_to_one_goal_are_alternatives_rather_than_more_obligations() -> None:
    """Newly implemented. The records could always express the OR; nothing
    read it. ``assess_claim`` takes evidence and assumptions and never consulted
    a route, so a goal with two decompositions reported exactly what a goal with
    none reported."""
    state, claims = _project("goal", "crossing", "incidence-a", "incidence-b")
    state.add_route(
        ProofRoute(
            route_id="via-crossing",
            goal=claims["goal"].ref(),
            obligations=(claims["crossing"].ref(),),
        )
    )
    state.add_route(
        ProofRoute(
            route_id="via-incidence",
            goal=claims["goal"].ref(),
            obligations=(claims["incidence-a"].ref(), claims["incidence-b"].ref()),
        )
    )
    state.add_evidence(_proved(claims["crossing"]))

    assessment = state.assess("goal")
    assert {route.route_id: route.status for route in assessment.routes} == {
        "via-crossing": RouteStatus.DISCHARGED,
        "via-incidence": RouteStatus.OPEN,
    }
    # The OR, stated as what it costs: finishing one route asks nobody to
    # finish the other, and the other's obligations stay outstanding rather
    # than being absorbed into a single list the goal has to clear.
    outstanding = {route.route_id: route.outstanding for route in assessment.routes}
    assert outstanding == {
        "via-crossing": (),
        "via-incidence": ("incidence-a", "incidence-b"),
    }


def test_a_route_whose_obligations_are_all_established_does_not_by_itself_close_the_claim() -> None:
    """Already true, and the reason the OR is reported instead of propagated.

    Nothing checks that a decomposition's obligations imply its goal. If a
    discharged route promoted the goal, an agent could mint ``closed_kernel``
    by writing a decomposition nobody read — the scheduler's version of a
    compiling Lean proof of a mistranslated statement.

    Asserted through the route-blind surface on purpose: this is the reading
    the claim's own status had before routes became visible to it, and has to
    go on having afterwards.
    """
    state, claims = _project("goal", "lemma")
    state.add_route(
        ProofRoute(
            route_id="via-lemma",
            goal=claims["goal"].ref(),
            obligations=(claims["lemma"].ref(),),
        )
    )
    state.add_evidence(_proved(claims["lemma"]))

    assert state.assess("lemma").status is ClaimStatus.CLOSED_KERNEL
    assert state.assess_routes()[0].status is RouteStatus.DISCHARGED
    assert state.assess("goal").status is ClaimStatus.PROPOSED
    assert state.assess("goal").is_kernel is False


def test_a_discharged_route_names_the_decomposition_step_as_the_unproved_part() -> None:
    """Newly implemented. Withholding the status is only half an answer: a
    reader who sees a finished route and an unmoved claim has to guess whether
    the kernel is being careful or is broken. The note is what makes the
    remaining work nameable — prove that these obligations imply this goal."""
    state, claims = _project("goal", "lemma")
    state.add_route(
        ProofRoute(
            route_id="via-lemma",
            goal=claims["goal"].ref(),
            obligations=(claims["lemma"].ref(),),
        )
    )
    state.add_evidence(_proved(claims["lemma"]))

    assessment = state.assess("goal")
    assert any(
        "decomposition step is itself unproved" in note for note in assessment.notes
    )
    assert any("via-lemma" in note for note in assessment.notes)
    # A defect is something to fix; this is a true report about a healthy
    # state, so it must not arrive as one.
    assert assessment.issues == ()
    assert state.validate() == ()
    assert assessment.as_dict()["routes"][0]["route_id"] == "via-lemma"


def test_attaching_routes_never_changes_the_status_the_evidence_earned() -> None:
    """Newly implemented, and the invariant the whole design rests on: routes
    are a second pass over the first pass's answer, and a second pass that could
    move a status would be propagation wearing a reporting label."""
    state, claims = _project("goal", "done", "dead", "circular")
    state.add_route(
        ProofRoute(
            route_id="finished",
            goal=claims["goal"].ref(),
            obligations=(claims["done"].ref(),),
        )
    )
    state.add_route(
        ProofRoute(
            route_id="doomed",
            goal=claims["goal"].ref(),
            obligations=(claims["dead"].ref(),),
        )
    )
    # Written the way a text editor writes it, because add_route refuses this.
    state.routes.append(
        ProofRoute(
            route_id="loop",
            goal=claims["circular"].ref(),
            obligations=(claims["circular"].ref(),),
        )
    )
    state.add_evidence(_proved(claims["done"]))
    state.add_evidence(_counterexample(claims["dead"]))

    # Every kind of route this package can report is present, so the invariant
    # is asserted over a state where routes have something to say rather than
    # over one where they trivially do not.
    assert {route.route_id: route.status for route in state.assess("goal").routes} == {
        "finished": RouteStatus.DISCHARGED,
        "doomed": RouteStatus.BLOCKED,
    }
    assert state.assess("circular").routes[0].status is RouteStatus.OPEN

    for claim in state.current_claims():
        alone = assess_claim(claim, state.evidence)
        assert state.assess(claim.claim_id).status is alone.status


# -- criterion 2: one route's obligations are an AND ------------------------


def test_an_informally_supported_obligation_does_not_discharge_a_route() -> None:
    """Already true; it is criterion 2's bar rather than criterion 2 itself,
    which ``test_a_route_is_an_AND_and_only_closes_when_all_of_it_does`` in the
    kernel suite already pins. What counts as a discharged obligation is
    ``ESTABLISHED_STATUSES``, which holds only the two kernel states — so a
    chain of individually plausible lemmas cannot add up to a finished route,
    which is the arithmetic that turns plausibility into proof."""
    state, claims = _project("goal", "lemma")
    state.add_route(
        ProofRoute(
            route_id="via-lemma",
            goal=claims["goal"].ref(),
            obligations=(claims["lemma"].ref(),),
        )
    )
    state.add_evidence(
        EvidenceRecord(
            evidence_id="ev-referee",
            subject=claims["lemma"].ref(),
            tier=EvidenceTier.JUDGEMENT,
            verdict=Verdict.SUPPORTS,
            produced_by="referee-1",
        )
    )

    assert state.assess("lemma").status is ClaimStatus.SUPPORTED
    assert state.assess_routes()[0].status is RouteStatus.OPEN
    assert state.assess_routes()[0].outstanding == ("lemma",)


# -- criterion 3: a shared lemma is solved once -----------------------------


def test_a_lemma_two_routes_share_is_proved_once_and_discharges_both() -> None:
    """Already true, and true by identity rather than by bookkeeping.

    Both routes name the lemma by the same ``SubjectRef``, assessments are
    keyed by that reference, and each current claim is assessed exactly once
    per pass — so there is no second copy of the lemma to prove, and no way for
    the two parents to disagree about whether it is proved.
    """
    state, claims = _project("goal-a", "goal-b", "shared")
    state.add_route(
        ProofRoute(
            route_id="a-via-shared",
            goal=claims["goal-a"].ref(),
            obligations=(claims["shared"].ref(),),
        )
    )
    state.add_route(
        ProofRoute(
            route_id="b-via-shared",
            goal=claims["goal-b"].ref(),
            obligations=(claims["shared"].ref(),),
        )
    )

    assert [route.status for route in state.assess_routes()] == [
        RouteStatus.OPEN,
        RouteStatus.OPEN,
    ]

    state.add_evidence(_proved(claims["shared"]))

    assert [route.status for route in state.assess_routes()] == [
        RouteStatus.DISCHARGED,
        RouteStatus.DISCHARGED,
    ]
    assert len([claim for claim in state.current_claims() if claim.claim_id == "shared"]) == 1
    # Two routes meeting at one lemma is a diamond, not a circle. A cycle check
    # that fired here would forbid the single most useful thing decomposition
    # does.
    assert route_cycles(state.routes) == ()
    assert state.validate() == ()


# -- criterion 4: a cyclic decomposition is rejected ------------------------


def test_a_route_that_would_close_a_cycle_is_refused_when_it_is_written() -> None:
    """Newly implemented. The write-time half, which is where the assumption
    ledger already puts its gate: an API that cannot record the defect is worth
    more than a report that the defect was recorded."""
    state, claims = _project("a", "b")
    state.add_route(
        ProofRoute(route_id="a-via-b", goal=claims["a"].ref(), obligations=(claims["b"].ref(),))
    )

    with pytest.raises(MathStateError, match="would close a cycle"):
        state.add_route(
            ProofRoute(
                route_id="b-via-a",
                goal=claims["b"].ref(),
                obligations=(claims["a"].ref(),),
            )
        )

    assert [route.route_id for route in state.routes] == ["a-via-b"]
    assert state.validate() == ()


def test_a_route_may_not_list_its_own_goal_among_its_obligations() -> None:
    """Newly implemented at write time; ``validate`` already reported it.

    A self-loop is the shortest cycle and needs no second route to exist, so a
    check that only looked at pairs would miss the cheapest way to write one.
    """
    state, claims = _project("a")

    with pytest.raises(MathStateError, match="would close a cycle"):
        state.add_route(
            ProofRoute(
                route_id="a-via-a",
                goal=claims["a"].ref(),
                obligations=(claims["a"].ref(),),
            )
        )


def test_a_cycle_that_runs_through_three_claims_is_still_a_cycle() -> None:
    """Newly implemented. Comparing a route against its immediate obligations
    would catch only length one and length two; an agent decomposing steadily
    produces the long ones."""
    state, claims = _project("a", "b", "c")
    state.add_route(
        ProofRoute(route_id="a-via-b", goal=claims["a"].ref(), obligations=(claims["b"].ref(),))
    )
    state.add_route(
        ProofRoute(route_id="b-via-c", goal=claims["b"].ref(), obligations=(claims["c"].ref(),))
    )

    with pytest.raises(MathStateError, match="a-via-b, b-via-c, c-via-a"):
        state.add_route(
            ProofRoute(
                route_id="c-via-a",
                goal=claims["c"].ref(),
                obligations=(claims["a"].ref(),),
            )
        )


def test_a_circular_attempt_may_still_be_recorded_as_a_retired_route() -> None:
    """Newly implemented, and the reason the gate is about live routes only.

    "We tried deriving A from B, and B needs A" is a result. Refusing to let it
    be written down would buy the cycle check with the one thing
    ``retired_because`` exists to prevent — the same dead end tried again.
    """
    state, claims = _project("a", "b")
    state.add_route(
        ProofRoute(route_id="a-via-b", goal=claims["a"].ref(), obligations=(claims["b"].ref(),))
    )
    state.add_route(
        ProofRoute(
            route_id="b-via-a",
            goal=claims["b"].ref(),
            obligations=(claims["a"].ref(),),
            retired_because="B was going to be proved from A; that is the circle we came from.",
        )
    )

    assert [route.status for route in state.assess_routes()] == [
        RouteStatus.OPEN,
        RouteStatus.RETIRED,
    ]
    assert route_cycles(state.routes) == ()
    assert state.validate() == ()


def test_a_hand_edited_cycle_loads_and_is_reported_rather_than_assessed_as_healthy(
    tmp_path: Path,
) -> None:
    """Newly implemented, and the read-time half of the gate.

    The API is not the only writer — this is JSON, and the same reasoning that
    put ``assumption_dropped_silently`` in ``validate`` applies here. The
    dangerous reading is the one this state produces on its own: both claims are
    proved by a kernel, so every obligation of both routes is established, and
    without the check both circular routes would report ``discharged`` — a plan
    called complete that could never have been started.
    """
    state, claims = _project("a", "b")
    state.add_route(
        ProofRoute(route_id="a-via-b", goal=claims["a"].ref(), obligations=(claims["b"].ref(),))
    )
    state.add_evidence(_proved(claims["a"]))
    state.add_evidence(_proved(claims["b"]))
    path = save_state(tmp_path, state)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["routes"].append(
        ProofRoute(
            route_id="b-via-a",
            goal=claims["b"].ref(),
            obligations=(claims["a"].ref(),),
        ).as_dict()
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    reloaded = load_state(tmp_path)
    assert [route.route_id for route in reloaded.routes] == ["a-via-b", "b-via-a"]
    assert [route.status for route in reloaded.assess_routes()] == [
        RouteStatus.OPEN,
        RouteStatus.OPEN,
    ]
    assert all(
        any("depends on itself" in issue for issue in route.issues)
        for route in reloaded.assess_routes()
    )
    assert [issue.code for issue in reloaded.validate()] == ["route_cycle"]
    # The claims themselves are untouched: they were proved by evidence, and a
    # bad plan for proving them is not a reason to disbelieve the proof.
    assert reloaded.assess("a").status is ClaimStatus.CLOSED_KERNEL


def test_a_hand_edited_self_loop_is_reported_once_and_not_twice() -> None:
    """Newly implemented in the assessment, already reported by ``validate``.

    The claim below is proved, so every obligation of "prove A from A" is
    established and the route would otherwise be reported as a discharged plan.
    It is the shortest possible statement of why a cycle has to reach the
    assessment and not only the issue list.

    One code, not two: a self-loop is a cycle, and saying so under
    ``route_circular`` and ``route_cycle`` both would train a reader to skim.
    """
    state, claims = _project("a")
    state.routes.append(
        ProofRoute(
            route_id="a-via-a",
            goal=claims["a"].ref(),
            obligations=(claims["a"].ref(),),
        )
    )
    state.add_evidence(_proved(claims["a"]))

    assert [issue.code for issue in state.validate()] == ["route_circular"]
    assessment = state.assess_routes()[0]
    assert assessment.status is RouteStatus.OPEN
    assert any("lists its own goal" in issue for issue in assessment.issues)


def test_the_cycle_check_stays_linear_on_a_project_of_a_few_hundred_routes() -> None:
    """Newly implemented, and a pin on the algorithm rather than on the answer.

    ``add_route`` runs the check on every write, so anything that enumerated
    paths instead of collapsing the graph into components would make recording a
    decomposition quietly super-linear in the project — and would time out here
    long before the assertion below could fail.
    """
    depth = 300
    state = MathState()
    context = state.add_context(_context())
    chain = [state.add_claim(_claim(context, f"c{index:03d}")) for index in range(depth)]

    started = time.monotonic()
    for index in range(depth - 1):
        state.add_route(
            ProofRoute(
                route_id=f"r{index:03d}",
                goal=chain[index].ref(),
                obligations=(chain[index + 1].ref(),),
            )
        )
    with pytest.raises(MathStateError, match="would close a cycle"):
        state.add_route(
            ProofRoute(
                route_id="r-closing",
                goal=chain[-1].ref(),
                obligations=(chain[0].ref(),),
            )
        )
    elapsed = time.monotonic() - started

    # Four orders of magnitude of headroom on a linear pass, so this fails only
    # for a change of complexity class and never for a busy machine.
    assert elapsed < 5.0
    assert state.validate() == ()


# -- criterion 5: a failed route does not refute the parent -----------------


def test_a_refuted_obligation_does_not_refute_the_claim_the_route_aimed_at() -> None:
    """Already true, and true because ``assess_claim`` reads only evidence bound
    to the claim's own digest. The mathematics agrees: one way of proving a
    theorem failing says nothing about the theorem."""
    state, claims = _project("goal", "lemma")
    state.add_route(
        ProofRoute(
            route_id="via-lemma",
            goal=claims["goal"].ref(),
            obligations=(claims["lemma"].ref(),),
        )
    )
    state.add_evidence(_counterexample(claims["lemma"]))

    assert state.assess("lemma").status is ClaimStatus.REFUTED
    assert state.assess("goal").status is ClaimStatus.PROPOSED


def test_a_route_with_a_refuted_obligation_is_dead_rather_than_merely_unfinished() -> None:
    """Newly implemented. "Not proved yet" and "cannot be proved this way" call
    for opposite actions — keep working, or stop — and before this they were the
    same status with the same outstanding list. The note carries the asymmetry
    the claim's own status deliberately does not encode."""
    state, claims = _project("goal", "dead-lemma", "live-lemma")
    state.add_route(
        ProofRoute(
            route_id="via-dead",
            goal=claims["goal"].ref(),
            obligations=(claims["dead-lemma"].ref(),),
        )
    )
    state.add_route(
        ProofRoute(
            route_id="via-live",
            goal=claims["goal"].ref(),
            obligations=(claims["live-lemma"].ref(),),
        )
    )
    state.add_evidence(_counterexample(claims["dead-lemma"]))

    assessment = state.assess("goal")
    assert {route.route_id: route.status for route in assessment.routes} == {
        "via-dead": RouteStatus.BLOCKED,
        "via-live": RouteStatus.OPEN,
    }
    dead = next(route for route in assessment.routes if route.route_id == "via-dead")
    assert dead.refuted_obligations == ("dead-lemma",)
    # Not outstanding: nobody should be sent to prove it.
    assert dead.outstanding == ()
    assert any(
        "refutes the route, not this claim" in note for note in assessment.notes
    )
    assert assessment.status is ClaimStatus.PROPOSED


# -- criterion 6: an exact counterexample refutes ---------------------------


def test_an_exact_counterexample_refutes_a_claim_with_no_kernel_involved() -> None:
    """Already true. ``REFUTING_TIERS`` is wider than ``KERNEL_TIERS`` on
    purpose: exhibiting one counterexample is a finite checkable act, and
    waiting for someone to formalize the refutation would keep a claim alive
    that is already dead."""
    state, claims = _project("claim")
    state.add_evidence(_counterexample(claims["claim"]))

    assessment = state.assess("claim")
    assert assessment.status is ClaimStatus.REFUTED
    assert assessment.is_kernel is False
    assert state.validate() == ()


def test_a_counterexample_nobody_can_re_run_is_reported_as_uncheckable() -> None:
    """Already true, and what "exact" is doing in the criterion. A refutation
    with no artifact is an assertion that a counterexample exists, which is a
    different and much weaker thing than a counterexample."""
    state, claims = _project("claim")
    state.add_evidence(_counterexample(claims["claim"], artifact=""))

    assert [issue.code for issue in state.validate()] == ["evidence_unauditable"]


def test_a_referee_calling_a_claim_false_does_not_refute_it() -> None:
    """Already true, and the fail-closed edge of criterion 6. The channel whose
    errors correlate with the producer's cannot decide the question in either
    direction, so an LLM's ``refutes`` moves nothing at all."""
    state, claims = _project("claim")
    state.add_evidence(
        EvidenceRecord(
            evidence_id="ev-referee",
            subject=claims["claim"].ref(),
            tier=EvidenceTier.JUDGEMENT,
            verdict=Verdict.REFUTES,
            produced_by="referee-1",
        )
    )

    assert state.assess("claim").status is ClaimStatus.PROPOSED
