"""The research-math state kernel: what it refuses to let a project claim.

Four things are load-bearing and each has its own section below: records
survive a round trip through JSON unchanged; a claim reaches ``closed_kernel``
only when every external assumption has been discharged; evidence recorded
against one statement cannot certify a different one; and the package imports
nothing that would stop it being lifted into its own repository.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from argus.proof_ledger import (
    CitationStatus,
    ClaimStatus,
    ClaimVersion,
    ContextVersion,
    EvidenceRecord,
    EvidenceTier,
    ExternalAssumption,
    MathState,
    MathStateError,
    ProofRoute,
    RetiredAssumption,
    RouteStatus,
    SubjectKind,
    SubjectRef,
    Verdict,
    assess_citation,
    assess_claim,
    assess_route,
    content_digest,
    load_state,
    normalize_text,
    save_state,
)

# -- fixtures ---------------------------------------------------------------

RIEMANN = ExternalAssumption(
    assumption_id="rh",
    statement="Every non-trivial zero of zeta has real part 1/2.",
    source="Riemann 1859, as stated in Titchmarsh Theorem 1.1",
)


def _context() -> ContextVersion:
    return ContextVersion(
        context_id="ctx",
        version=1,
        statement="Bound the number of unit distances among n planar points.",
        definitions={"unit distance": "a pair of points at Euclidean distance 1"},
    )


def _claim(
    context: ContextVersion,
    *,
    version: int = 1,
    statement: str = "u(n) = O(n**(4/3)).",
    formal: str = "theorem unit_distance_bound : ...",
    assumptions: tuple[ExternalAssumption, ...] = (),
) -> ClaimVersion:
    return ClaimVersion(
        claim_id="c1",
        version=version,
        context=context.ref(),
        natural_statement=statement,
        formal_statement=formal,
        external_assumptions=assumptions,
    )


def _lean(
    claim: ClaimVersion,
    *,
    evidence_id: str = "ev-lean",
    verdict: Verdict = Verdict.SUPPORTS,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        subject=claim.ref(),
        tier=EvidenceTier.MECHANICAL,
        verdict=verdict,
        produced_by="lean_check 4.9.0",
        artifact="research/lean/lean_check.json",
    )


def _seeded_state() -> tuple[MathState, ContextVersion, ClaimVersion]:
    state = MathState()
    context = state.add_context(_context())
    claim = state.add_claim(_claim(context))
    return state, context, claim


# -- round trip -------------------------------------------------------------

def test_every_record_survives_a_round_trip_through_json(tmp_path: Path) -> None:
    state, context, _ = _seeded_state()
    claim = state.revise_claim("c1", external_assumptions=(RIEMANN,))
    state.add_evidence(_lean(claim))
    state.add_route(
        ProofRoute(
            route_id="r1",
            goal=claim.ref(),
            obligations=(SubjectRef(SubjectKind.CLAIM, "lemma-a", "0" * 64),),
            retired_because="",
        )
    )
    state.revise_claim(
        "c1",
        external_assumptions=(),
        retire_assumptions={"rh": "the final draft avoids it entirely"},
    )

    save_state(tmp_path, state)
    reloaded = load_state(tmp_path)

    assert reloaded.contexts == state.contexts
    assert reloaded.claims == state.claims
    assert reloaded.evidence == state.evidence
    assert reloaded.routes == state.routes
    # The digest is derived, so a round trip that changed any hashed field
    # would silently invalidate every piece of evidence in the file.
    assert reloaded.latest_claim("c1").content_hash == claim.content_hash
    assert reloaded.latest_context("ctx").content_hash == context.content_hash


def test_a_missing_state_file_is_empty_but_a_broken_one_is_loud(tmp_path: Path) -> None:
    # Most projects have no mathematical state and must pay nothing for that.
    assert load_state(tmp_path).claims == []

    path = save_state(tmp_path, MathState())
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MathStateError, match="not valid JSON"):
        load_state(tmp_path)


def test_a_future_schema_version_is_refused_rather_than_half_read() -> None:
    with pytest.raises(MathStateError, match="cannot read"):
        MathState.from_dict({"schema_version": 99, "claims": []})


def test_cosmetic_reflow_does_not_invalidate_a_proof() -> None:
    context = _context()
    plain = _claim(context)
    reflowed = _claim(context, statement=f"  {plain.natural_statement}  \n\n")

    assert reflowed.content_hash == plain.content_hash
    assert _lean(plain).binds_to(reflowed.ref())


# -- conditional_kernel vs closed_kernel ------------------------------------

def test_kernel_evidence_alone_only_reaches_conditional_kernel() -> None:
    context = _context()
    claim = _claim(context, assumptions=(RIEMANN,))
    lean = _lean(claim)

    assessment = assess_claim(claim, [lean])

    assert assessment.status is ClaimStatus.CONDITIONAL_KERNEL
    assert assessment.undischarged == ("rh",)


def test_closing_the_kernel_requires_discharging_every_assumption() -> None:
    context = _context()
    other = ExternalAssumption(
        assumption_id="hall",
        statement="Hall's theorem in the form used in step 4.",
        source="Hall 1935, Theorem 1",
    )
    claim = _claim(context, assumptions=(RIEMANN, other))
    lean = _lean(claim)

    def discharge(assumption: ExternalAssumption) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=f"ev-{assumption.assumption_id}",
            subject=assumption.ref(),
            tier=EvidenceTier.MECHANICAL,
            verdict=Verdict.SUPPORTS,
            produced_by="lean_check 4.9.0",
            artifact=f"research/lean/{assumption.assumption_id}.json",
        )

    partial = assess_claim(claim, [lean, discharge(RIEMANN)])
    assert partial.status is ClaimStatus.CONDITIONAL_KERNEL
    assert partial.undischarged == ("hall",)

    full = assess_claim(claim, [lean, discharge(RIEMANN), discharge(other)])
    assert full.status is ClaimStatus.CLOSED_KERNEL
    assert full.undischarged == ()


def test_discharging_an_assumption_does_not_invalidate_the_proof_above_it() -> None:
    """The transition has to be reachable at all.

    If assumptions were inside the claim's digest, recording a discharge would
    change the claim and orphan the Lean run it was meant to complete, so
    ``closed_kernel`` could never be reached.
    """
    context = _context()
    conditional = _claim(context, assumptions=(RIEMANN,))
    lean = _lean(conditional)
    without = _claim(context, assumptions=())

    assert conditional.content_hash == without.content_hash
    assert lean.binds_to(without.ref())


def test_no_number_of_referees_reaches_kernel_status() -> None:
    """Ten similar judges are not ten independent verifiers."""
    context = _context()
    claim = _claim(context)
    referees = [
        EvidenceRecord(
            evidence_id=f"ev-referee-{index}",
            subject=claim.ref(),
            tier=EvidenceTier.JUDGEMENT,
            verdict=Verdict.SUPPORTS,
            produced_by=f"referee-{index}",
        )
        for index in range(10)
    ]

    assessment = assess_claim(claim, referees)

    assert assessment.status is ClaimStatus.SUPPORTED
    # One channel answered, and the report says so by naming producers rather
    # than by handing back a count that reads like ten checks.
    assert set(assessment.support) == {EvidenceTier.JUDGEMENT}
    assert len(assessment.support[EvidenceTier.JUDGEMENT]) == 10


def test_a_referee_cannot_discharge_an_external_assumption() -> None:
    context = _context()
    claim = _claim(context, assumptions=(RIEMANN,))
    opinion = EvidenceRecord(
        evidence_id="ev-opinion",
        subject=RIEMANN.ref(),
        tier=EvidenceTier.JUDGEMENT,
        verdict=Verdict.SUPPORTS,
        produced_by="referee-1",
    )
    lookup = EvidenceRecord(
        evidence_id="ev-lookup",
        subject=RIEMANN.ref(),
        tier=EvidenceTier.LITERATURE,
        verdict=Verdict.SUPPORTS,
        produced_by="literature-agent",
        artifact="research/sources/titchmarsh.json",
    )

    assessment = assess_claim(claim, [_lean(claim), opinion, lookup])

    assert assessment.status is ClaimStatus.CONDITIONAL_KERNEL
    assert assessment.undischarged == ("rh",)


def test_a_counterexample_outranks_a_kernel_support() -> None:
    context = _context()
    claim = _claim(context)
    counterexample = EvidenceRecord(
        evidence_id="ev-code",
        subject=claim.ref(),
        tier=EvidenceTier.COMPUTATIONAL,
        verdict=Verdict.REFUTES,
        produced_by="python 3.11 search",
        artifact="research/code/search_output.json",
    )

    assessment = assess_claim(claim, [_lean(claim), counterexample])

    assert assessment.status is ClaimStatus.REFUTED


def test_kernel_evidence_for_a_claim_with_no_formalization_is_reported() -> None:
    context = _context()
    claim = _claim(context, formal="")
    assessment = assess_claim(claim, [_lean(claim)])

    assert assessment.status is not ClaimStatus.CLOSED_KERNEL
    assert any("no formal statement" in issue for issue in assessment.issues)


def test_an_undischarged_assumption_is_a_query_not_a_paragraph() -> None:
    state, context, _ = _seeded_state()
    claim = state.revise_claim("c1", external_assumptions=(RIEMANN,))
    state.add_evidence(_lean(claim))

    assert state.undischarged_assumptions("c1") == (RIEMANN,)
    assert state.open_assumptions() == {"c1": (RIEMANN,)}


# -- deleting an assumption -------------------------------------------------
#
# Assumptions sit outside the claim digest so that discharging one cannot
# orphan the proof it completes. The cost of that choice is that *removing* one
# is invisible to the digest too, and removal is the direction with a motive:
# closed_kernel is expensive, and deleting the line that withholds it would
# otherwise be free. Everything below is that hole, from every direction it can
# be approached.

def _conditional_state() -> tuple[MathState, ClaimVersion]:
    state, context, _ = _seeded_state()
    claim = state.revise_claim("c1", external_assumptions=(RIEMANN,))
    state.add_evidence(_lean(claim))
    assert state.assess("c1").status is ClaimStatus.CONDITIONAL_KERNEL
    return state, claim


def test_deleting_an_undischarged_assumption_is_refused() -> None:
    state, _ = _conditional_state()

    with pytest.raises(MathStateError, match="stop standing on"):
        state.revise_claim("c1", external_assumptions=())

    assert state.assess("c1").status is ClaimStatus.CONDITIONAL_KERNEL


def test_a_deletion_that_bypassed_the_api_still_does_not_close_the_kernel() -> None:
    """The API is not the only writer: this file is JSON and editors exist."""
    state, claim = _conditional_state()
    state.add_claim(
        ClaimVersion(
            claim_id="c1",
            version=claim.version + 1,
            context=claim.context,
            natural_statement=claim.natural_statement,
            formal_statement=claim.formal_statement,
        )
    )

    assessment = state.assess("c1")
    assert assessment.status is ClaimStatus.CONDITIONAL_KERNEL
    assert assessment.undischarged == ("rh",)
    assert any("no revision recorded why" in issue for issue in assessment.issues)
    assert [issue.code for issue in state.validate()] == ["assumption_dropped_silently"]
    # The dependency is still answerable as a query, not merely as an issue.
    assert state.undischarged_assumptions("c1") == (RIEMANN,)
    assert MathState.from_dict(state.as_dict()).assess("c1").status is (
        ClaimStatus.CONDITIONAL_KERNEL
    )
    # A damaged ledger is repaired at the next revision rather than inherited
    # forever: the reason is owed even though an earlier version dropped it.
    with pytest.raises(MathStateError, match="stop standing on"):
        state.revise_claim("c1", natural_statement="unrelated edit")
    state.revise_claim(
        "c1",
        natural_statement="unrelated edit",
        retire_assumptions={"rh": "reconstructed: step 4 never used it"},
    )
    assert state.validate() == ()


def test_two_revisions_do_not_launder_a_deletion() -> None:
    """Pairwise comparison would miss this: by version 3 the predecessor has
    nothing to be missing."""
    state, claim = _conditional_state()
    for version in (claim.version + 1, claim.version + 2):
        state.add_claim(
            ClaimVersion(
                claim_id="c1",
                version=version,
                context=claim.context,
                natural_statement=claim.natural_statement,
                formal_statement=claim.formal_statement,
            )
        )

    assert state.assess("c1").status is ClaimStatus.CONDITIONAL_KERNEL
    assert state.effective_assumptions("c1") == (RIEMANN,)


def test_a_removal_with_a_recorded_reason_closes_the_kernel() -> None:
    """The legitimate case still works, and is the only one that does."""
    state, _ = _conditional_state()
    revised = state.revise_claim(
        "c1",
        external_assumptions=(),
        retire_assumptions={"rh": "step 4 was replaced by an unconditional bound"},
    )

    assert state.assess("c1").status is ClaimStatus.CLOSED_KERNEL
    assert state.validate() == ()
    assert state.effective_assumptions("c1") == ()
    assert revised.retired_assumptions[0].content_hash == RIEMANN.content_hash


def test_a_retirement_written_about_another_statement_authorizes_nothing() -> None:
    state, claim = _conditional_state()
    state.add_claim(
        ClaimVersion(
            claim_id="c1",
            version=claim.version + 1,
            context=claim.context,
            natural_statement=claim.natural_statement,
            formal_statement=claim.formal_statement,
            retired_assumptions=(
                RetiredAssumption("rh", "0" * 64, "we no longer need this"),
            ),
        )
    )

    assert state.assess("c1").status is ClaimStatus.CONDITIONAL_KERNEL
    assert [issue.code for issue in state.validate()] == [
        "assumption_dropped_silently",
        "assumption_retirement_unmatched",
    ]


def test_a_retirement_must_be_about_something_that_is_actually_going() -> None:
    state, _ = _conditional_state()

    with pytest.raises(MathStateError, match="still listed"):
        state.revise_claim("c1", retire_assumptions={"rh": "we do not need it"})
    with pytest.raises(MathStateError, match="nothing to retire"):
        state.revise_claim("c1", retire_assumptions={"hall": "never used it"})
    with pytest.raises(MathStateError, match="needs a reason"):
        state.revise_claim("c1", external_assumptions=(), retire_assumptions={"rh": "  "})


def test_a_discharged_assumption_is_kept_rather_than_deleted() -> None:
    """Deleting a discharged assumption loses the dependency and its proof in
    one edit, so it is refused too — a discharge is recorded on top of the
    assumption, never in place of it."""
    state, claim = _conditional_state()
    state.add_evidence(
        EvidenceRecord(
            evidence_id="ev-rh",
            subject=RIEMANN.ref(),
            tier=EvidenceTier.MECHANICAL,
            verdict=Verdict.SUPPORTS,
            produced_by="lean_check 4.9.0",
            artifact="research/lean/rh.json",
        )
    )
    assert state.assess("c1").status is ClaimStatus.CLOSED_KERNEL

    with pytest.raises(MathStateError, match="stop standing on"):
        state.revise_claim("c1", external_assumptions=())
    assert state.latest_claim("c1").external_assumptions == (RIEMANN,)


def test_correcting_an_assumption_is_not_deleting_it() -> None:
    """Rewording a dependency must stay possible without a retirement, or the
    gate would punish the one edit that keeps the ledger honest."""
    state, _ = _conditional_state()
    corrected = ExternalAssumption(
        assumption_id="rh",
        statement=RIEMANN.statement,
        source="Riemann 1859, as stated in Titchmarsh Theorem 1.2",
    )
    state.revise_claim("c1", external_assumptions=(corrected,))

    assert state.effective_assumptions("c1") == (corrected,)
    assert state.validate() == ()


# -- version binding --------------------------------------------------------

def test_evidence_for_an_old_statement_cannot_certify_the_new_one() -> None:
    state, _, first = _seeded_state()
    state.add_evidence(_lean(first))
    assert state.assess("c1").status is ClaimStatus.CLOSED_KERNEL

    second = state.revise_claim("c1", natural_statement="u(n) = O(n log n).")

    assessment = state.assess("c1")
    assert assessment.version == second.version
    assert assessment.status is ClaimStatus.PROPOSED
    # Reported, not dropped: a statement moving under a finished verification
    # is the most interesting event this schema can observe.
    assert assessment.stale_evidence == ("ev-lean",)


def test_changing_a_definition_leaves_the_claims_below_it_visibly_unre_examined() -> None:
    state, _, claim = _seeded_state()
    state.add_evidence(_lean(claim))
    assert state.assess("c1").status is ClaimStatus.CLOSED_KERNEL
    assert state.validate() == ()

    state.revise_context("ctx", definitions={"unit distance": "distance at most 1"})

    # The proof is still a proof of what it proved; what nobody has decided is
    # whether it survives the new definition. That has to be visible, or the
    # silence answers "yes".
    assert state.assess("c1").status is ClaimStatus.CLOSED_KERNEL
    assert {issue.code for issue in state.validate()} == {"claim_context_outdated"}

    # Restating it against the new problem is what costs the certificate.
    restated = state.revise_claim("c1", context=state.latest_context("ctx").ref())
    assert restated.content_hash != claim.content_hash
    assert state.assess("c1").status is ClaimStatus.PROPOSED
    assert state.validate() == ()


def test_correcting_a_citation_undoes_its_discharge() -> None:
    misattributed = ExternalAssumption(
        assumption_id="rh",
        statement=RIEMANN.statement,
        source="Titchmarsh Theorem 1.2",
    )
    context = _context()
    claim = _claim(context, assumptions=(misattributed,))
    discharge = EvidenceRecord(
        evidence_id="ev-formalized",
        subject=misattributed.ref(),
        tier=EvidenceTier.MECHANICAL,
        verdict=Verdict.SUPPORTS,
        produced_by="lean_check 4.9.0",
        artifact="research/lean/rh.json",
    )

    assert assess_claim(claim, [_lean(claim), discharge]).status is (
        ClaimStatus.CLOSED_KERNEL
    )

    corrected = _claim(context, assumptions=(RIEMANN,))
    assert assess_claim(corrected, [_lean(corrected), discharge]).undischarged == ("rh",)


def test_history_is_never_overwritten() -> None:
    state, context, first = _seeded_state()
    state.revise_claim("c1", natural_statement="restated")
    state.add_evidence(_lean(first))

    with pytest.raises(MathStateError, match="already exists"):
        state.add_claim(_claim(context, version=1, statement="a third thing"))
    with pytest.raises(MathStateError, match="already exists"):
        state.add_evidence(_lean(first, verdict=Verdict.REFUTES))

    assert [item.version for item in state.claim_history("c1")] == [1, 2]
    assert state.claim_history("c1")[0].natural_statement == first.natural_statement


# -- citations --------------------------------------------------------------

CITED = ExternalAssumption(
    assumption_id="rh",
    statement=RIEMANN.statement,
    source="Titchmarsh, The Theory of the Riemann Zeta-function",
    source_id="doi:10.1093/oso/9780198533696.001.0001",
    locator="Theorem 14.2",
)


def _lookup(
    assumption: ExternalAssumption,
    *,
    evidence_id: str = "ev-cite",
    verdict: Verdict = Verdict.SUPPORTS,
    produced_by: str = "citation_check",
    artifact: str = "research/literature/titchmarsh-14-2.txt",
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        subject=assumption.ref(),
        tier=EvidenceTier.LITERATURE,
        verdict=verdict,
        produced_by=produced_by,
        artifact=artifact,
    )


def test_a_citation_names_a_proposition_and_half_of_one_names_nothing() -> None:
    assert CITED.cited_proposition == (
        "doi:10.1093/oso/9780198533696.001.0001 Theorem 14.2"
    )
    assert RIEMANN.cited_proposition == ""

    state, _, _ = _seeded_state()
    state.revise_claim(
        "c1",
        external_assumptions=(
            ExternalAssumption(
                assumption_id="half",
                statement=RIEMANN.statement,
                source="Titchmarsh",
                source_id="doi:10.1093/oso/9780198533696.001.0001",
            ),
        ),
    )

    codes = {issue.code for issue in state.validate()}
    assert "citation_incomplete" in codes


def test_a_citation_nobody_looked_up_is_not_one_that_was_looked_up_and_missing(
) -> None:
    """Three states, because the middle one is where a project ships a lie."""
    unchecked = assess_citation(CITED, [])
    assert unchecked.status is CitationStatus.UNCHECKED
    assert not unchecked.is_settled

    confirmed = assess_citation(CITED, [_lookup(CITED)])
    assert confirmed.status is CitationStatus.CONFIRMED
    assert confirmed.checked_by == ("citation_check",)
    assert confirmed.artifacts == ("research/literature/titchmarsh-14-2.txt",)

    disputed = assess_citation(CITED, [_lookup(CITED, verdict=Verdict.REFUTES)])
    assert disputed.status is CitationStatus.DISPUTED

    unreached = assess_citation(
        CITED, [_lookup(CITED, verdict=Verdict.INCONCLUSIVE)]
    )
    assert unreached.status is CitationStatus.INCONCLUSIVE
    assert not unreached.is_settled


def test_a_source_with_no_proposition_is_uncited_and_owes_nobody_a_lookup() -> None:
    """An unpublished result cannot be checked, and is not a task.

    ``unchecked`` would put it in a queue nothing could ever take it out of,
    which is how a gate that means something becomes a gate everybody learns to
    override.
    """
    assessment = assess_citation(RIEMANN, [])
    assert assessment.status is CitationStatus.UNCITED
    assert assessment.is_settled
    assert assessment.cited_proposition == ""


def test_two_checkers_disagreeing_is_not_a_vote() -> None:
    both = assess_citation(
        CITED,
        [
            _lookup(CITED, evidence_id="ev-a", produced_by="citation_check"),
            _lookup(
                CITED,
                evidence_id="ev-b",
                verdict=Verdict.REFUTES,
                produced_by="citation_check:rerun",
            ),
        ],
    )
    assert both.status is CitationStatus.DISPUTED
    assert both.checked_by == ("citation_check", "citation_check:rerun")


def test_a_referee_reading_a_paper_does_not_check_a_citation() -> None:
    """The tier whose checker is the agent cannot audit the agent's reading."""
    opinion = EvidenceRecord(
        evidence_id="ev-opinion",
        subject=CITED.ref(),
        tier=EvidenceTier.JUDGEMENT,
        verdict=Verdict.SUPPORTS,
        produced_by="reviewer:alice",
    )

    assert assess_citation(CITED, [opinion]).status is CitationStatus.UNCHECKED


def test_correcting_the_theorem_number_drops_the_lookup_obtained_against_it(
) -> None:
    misnumbered = ExternalAssumption(
        assumption_id=CITED.assumption_id,
        statement=CITED.statement,
        source=CITED.source,
        source_id=CITED.source_id,
        locator="Theorem 14.1",
    )
    lookup = _lookup(misnumbered)

    assert assess_citation(misnumbered, [lookup]).status is CitationStatus.CONFIRMED
    assert assess_citation(CITED, [lookup]).status is CitationStatus.UNCHECKED


def test_a_confirmed_citation_discharges_nothing() -> None:
    """What the source says and whether its hypotheses hold here are two questions."""
    context = _context()
    claim = _claim(context, assumptions=(CITED,))
    records = [_lean(claim), _lookup(CITED)]

    assessment = assess_claim(claim, records)
    assert assessment.status is ClaimStatus.CONDITIONAL_KERNEL
    assert assessment.undischarged == ("rh",)


def test_the_project_wide_question_lists_only_what_still_owes_a_lookup() -> None:
    state, context, _ = _seeded_state()
    unpublished = ExternalAssumption(
        assumption_id="folklore",
        statement="the standard averaging bound",
        source="folklore; stated without proof in seminar notes",
    )
    state.revise_claim("c1", external_assumptions=(CITED, unpublished))
    state.add_claim(
        ClaimVersion(
            claim_id="c2",
            version=1,
            context=context.ref(),
            natural_statement="a second statement",
            external_assumptions=(
                ExternalAssumption(
                    assumption_id="kkl",
                    statement="the KKL inequality",
                    source="Kahn, Kalai, Linial 1988",
                    source_id="doi:10.1109/SFCS.1988.21923",
                    locator="Theorem 3.1",
                ),
            ),
        )
    )
    state.add_evidence(_lookup(CITED))

    assert {
        item.assumption_id: item.status for item in state.citations("c1")
    } == {
        "folklore": CitationStatus.UNCITED,
        "rh": CitationStatus.CONFIRMED,
    }
    assert {
        claim_id: tuple(item.assumption_id for item in items)
        for claim_id, items in state.open_citations().items()
    } == {"c2": ("kkl",)}


def test_an_assumption_that_never_had_a_citation_hashes_as_it_always_did() -> None:
    """Adding the fields must not restate every assumption already recorded.

    The digest is what evidence binds to, so a schema change that moved it would
    orphan every discharge in every live project on upgrade — the failure
    ``content_digest`` refuses to cause by hashing a schema version. Absent
    fields are absent from the payload rather than present and empty.
    """
    assert RIEMANN.content_hash == content_digest(
        {
            "assumption_id": "rh",
            "statement": normalize_text(RIEMANN.statement),
            "source": normalize_text(RIEMANN.source),
        }
    )
    assert CITED.content_hash != content_digest(
        {
            "assumption_id": "rh",
            "statement": normalize_text(CITED.statement),
            "source": normalize_text(CITED.source),
        }
    )


# -- structural validation --------------------------------------------------

def test_an_unsourced_assumption_is_a_gap_not_a_citation() -> None:
    state, context, _ = _seeded_state()
    state.revise_claim(
        "c1",
        external_assumptions=(
            ExternalAssumption(assumption_id="x", statement="something", source=""),
        ),
    )

    codes = {issue.code for issue in state.validate()}
    assert "assumption_unsourced" in codes


def test_mechanical_evidence_must_name_something_re_inspectable() -> None:
    state, _, claim = _seeded_state()
    state.add_evidence(
        EvidenceRecord(
            evidence_id="ev-bare",
            subject=claim.ref(),
            tier=EvidenceTier.MECHANICAL,
            verdict=Verdict.SUPPORTS,
            produced_by="lean_check 4.9.0",
        )
    )

    codes = {issue.code for issue in state.validate()}
    assert "evidence_unauditable" in codes


def test_one_assumption_id_may_not_name_two_statements() -> None:
    state, context, _ = _seeded_state()
    state.add_claim(
        ClaimVersion(
            claim_id="c2",
            version=1,
            context=context.ref(),
            natural_statement="another claim",
            external_assumptions=(
                ExternalAssumption(
                    assumption_id="rh", statement="something else", source="elsewhere"
                ),
            ),
        )
    )
    state.revise_claim("c1", external_assumptions=(RIEMANN,))

    codes = {issue.code for issue in state.validate()}
    assert "assumption_id_collision" in codes


def test_a_reference_may_only_point_at_the_right_kind_of_thing() -> None:
    context = _context()
    with pytest.raises(ValueError, match="must reference a context"):
        ClaimVersion(
            claim_id="c1",
            version=1,
            context=SubjectRef(SubjectKind.CLAIM, "c0", "0" * 64),
            natural_statement="x",
        )
    with pytest.raises(ValueError, match="must reference a claim"):
        ProofRoute(route_id="r", goal=context.ref())


# -- routes -----------------------------------------------------------------

def test_a_route_is_an_AND_and_only_closes_when_all_of_it_does() -> None:
    state, context, goal = _seeded_state()
    lemma_a = state.add_claim(
        ClaimVersion(
            claim_id="lemma-a",
            version=1,
            context=context.ref(),
            natural_statement="Lemma A.",
            formal_statement="theorem lemma_a : ...",
        )
    )
    lemma_b = state.add_claim(
        ClaimVersion(
            claim_id="lemma-b",
            version=1,
            context=context.ref(),
            natural_statement="Lemma B.",
            formal_statement="theorem lemma_b : ...",
        )
    )
    route = state.add_route(
        ProofRoute(
            route_id="r1",
            goal=goal.ref(),
            obligations=(lemma_a.ref(), lemma_b.ref()),
        )
    )

    state.add_evidence(_lean(lemma_a, evidence_id="ev-a"))
    assert state.assess_routes()[0].status is RouteStatus.OPEN
    assert state.assess_routes()[0].outstanding == ("lemma-b",)

    state.add_evidence(_lean(lemma_b, evidence_id="ev-b"))
    assert state.assess_routes()[0].status is RouteStatus.DISCHARGED

    retired = assess_route(
        ProofRoute(
            route_id=route.route_id,
            goal=route.goal,
            obligations=route.obligations,
            retired_because="Lemma B is theorem-strength; the route is circular.",
        ),
        state.assess_all(),
    )
    assert retired.status is RouteStatus.RETIRED


def test_a_route_over_a_restated_lemma_says_so() -> None:
    state, context, goal = _seeded_state()
    lemma = state.add_claim(
        ClaimVersion(
            claim_id="lemma-a",
            version=1,
            context=context.ref(),
            natural_statement="Lemma A.",
            formal_statement="theorem lemma_a : ...",
        )
    )
    state.add_route(
        ProofRoute(route_id="r1", goal=goal.ref(), obligations=(lemma.ref(),))
    )
    state.revise_claim("lemma-a", natural_statement="Lemma A, weakened.")

    assessment = state.assess_routes()[0]
    assert assessment.status is RouteStatus.OPEN
    assert assessment.stale_obligations == ("lemma-a",)


def test_a_route_that_needs_nothing_proved_is_not_a_route() -> None:
    state, _, goal = _seeded_state()
    state.add_route(ProofRoute(route_id="r1", goal=goal.ref()))

    assessment = state.assess_routes()[0]
    assert assessment.status is RouteStatus.OPEN
    assert any("no obligations" in issue for issue in assessment.issues)


# -- the boundary that lets this package leave ------------------------------

_PACKAGE = Path(__file__).parents[2] / "argus" / "proof_ledger"


def _sources() -> list[Path]:
    files = sorted(_PACKAGE.rglob("*.py"))
    assert files, "the AST sweep found no sources, so it proves nothing"
    return files


def test_proof_ledger_imports_nothing_from_argus() -> None:
    """The package has to be liftable into its own repository unchanged.

    Any import of Manager, Planner, Engineer, Reviewer, the backlog or the
    supervisor would tie the kernel to this host's orchestration, which is the
    part a different host replaces. Rather than blacklist those six, this
    forbids every intra-repository import, absolute or relative, so a new
    sibling package cannot be reached for either.
    """
    offenders: list[str] = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                # level > 0 is a relative import; only siblings inside this
                # package are reachable that way, and those are level 1 with a
                # module that resolves within it.
                if node.level > 1 or module.startswith("argus"):
                    offenders.append(f"{path.name}:{node.lineno} {'.' * node.level}{module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "argus":
                        offenders.append(f"{path.name}:{node.lineno} {alias.name}")
    assert offenders == []


def test_proof_ledger_imports_nothing_outside_the_standard_library() -> None:
    """A third-party dependency is the other way a package fails to travel."""
    offenders: list[str] = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                names.append(str(node.module or ""))
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                root = name.split(".")[0]
                if root and root not in sys.stdlib_module_names:
                    offenders.append(f"{path.name}:{node.lineno} {name}")
    assert offenders == []


def test_the_state_file_is_json_because_nothing_here_uses_a_database() -> None:
    """SQLite would be this repository's first, for a few hundred records.

    ``sqlite3`` is in the standard library, so the import sweep above would let
    it through; this names it.
    """
    offenders: list[str] = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names.append(str(node.module or ""))
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            if any(name.split(".")[0] == "sqlite3" for name in names):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []

    state, _, claim = _seeded_state()
    state.add_evidence(_lean(claim))
    payload = json.loads(json.dumps(state.as_dict()))
    assert payload["schema_version"] == 1
    assert payload["claims"][0]["content_hash"] == claim.content_hash


# -- retired routes are history (lbx154/Argus#127) --------------------------

def _retired(goal: SubjectRef, obligations: tuple[SubjectRef, ...], reason: str = "Replaced by a stronger claim.") -> ProofRoute:
    return ProofRoute(route_id="r-old", goal=goal, obligations=obligations, retired_because=reason)


def test_a_retired_route_may_aim_at_a_superseded_claim_version() -> None:
    """Retire a route, revise its goal, open a replacement: the check passes.

    The retired route is immutable evidence of an attempt made against the
    claim as it then stood. The active replacement still has to target the
    current statement, and both versions of the claim stay in the ledger.
    """
    state, context, goal = _seeded_state()
    lemma = state.add_claim(
        ClaimVersion(
            claim_id="lemma-a", version=1, context=context.ref(),
            natural_statement="Lemma A.", formal_statement="theorem lemma_a : ...",
        )
    )
    old_goal = goal.ref()
    state.add_route(_retired(old_goal, (lemma.ref(),)))
    revised = state.revise_claim("c1", natural_statement="A sharper statement.")
    assert revised.ref() != old_goal
    state.add_route(ProofRoute(route_id="r-new", goal=revised.ref(), obligations=(lemma.ref(),), retired_because=""))

    codes = [issue.code for issue in state.validate()]
    assert "route_goal_stale" not in codes and "route_goal_unknown" not in codes
    assert {item.content_hash for item in state.claims if item.claim_id == "c1"} == {old_goal.content_hash, revised.content_hash}


def test_an_active_route_over_a_superseded_goal_is_still_stale() -> None:
    state, context, goal = _seeded_state()
    lemma = state.add_claim(
        ClaimVersion(
            claim_id="lemma-a", version=1, context=context.ref(),
            natural_statement="Lemma A.", formal_statement="theorem lemma_a : ...",
        )
    )
    state.add_route(ProofRoute(route_id="r1", goal=goal.ref(), obligations=(lemma.ref(),), retired_because=""))
    state.revise_claim("c1", natural_statement="A sharper statement.")
    assert [issue.code for issue in state.validate()] == ["route_goal_stale"]


def test_a_blank_retirement_reason_does_not_make_a_route_historical() -> None:
    state, context, goal = _seeded_state()
    lemma = state.add_claim(
        ClaimVersion(
            claim_id="lemma-a", version=1, context=context.ref(),
            natural_statement="Lemma A.", formal_statement="theorem lemma_a : ...",
        )
    )
    state.add_route(_retired(goal.ref(), (lemma.ref(),), reason="   "))
    state.revise_claim("c1", natural_statement="A sharper statement.")
    assert [issue.code for issue in state.validate()] == ["route_goal_stale"]


@pytest.mark.parametrize(
    "goal",
    [
        SubjectRef(SubjectKind.CLAIM, "never-recorded", "0" * 64),
        None,  # filled in below: right id, wrong hash
    ],
)
def test_a_retired_route_must_still_name_a_recorded_claim_version(goal: SubjectRef | None) -> None:
    state, context, real = _seeded_state()
    lemma = state.add_claim(
        ClaimVersion(
            claim_id="lemma-a", version=1, context=context.ref(),
            natural_statement="Lemma A.", formal_statement="theorem lemma_a : ...",
        )
    )
    target = goal or SubjectRef(SubjectKind.CLAIM, real.claim_id, "f" * 64)
    state.add_route(_retired(target, (lemma.ref(),)))
    issues = state.validate()
    assert [issue.code for issue in issues] == ["route_goal_unknown"]
    assert issues[0].path == "$.routes[0].goal"


def test_a_retired_self_dependent_route_is_kept_but_an_active_one_is_reported() -> None:
    state, _, goal = _seeded_state()
    state.add_route(_retired(goal.ref(), (goal.ref(),), reason="B needed A; that is the circle."))
    assert list(state.validate()) == []
    state.routes.append(ProofRoute(route_id="r-live", goal=goal.ref(), obligations=(goal.ref(),), retired_because=""))
    assert "route_circular" in [issue.code for issue in state.validate()]
