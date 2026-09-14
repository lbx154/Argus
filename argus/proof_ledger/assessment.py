"""What the records add up to — and the several things they deliberately do not.

A claim's status is computed here, never stored. Nothing writes
``closed_kernel`` into a file; it is what you get when a mechanical verdict
still binds to the current statement and no external assumption is left
standing. Making it derived rather than declared is what makes the transition
rule unforgeable: there is no field to set.

Three gates decide everything below, and each names the tiers it accepts by
set membership rather than by a threshold on a rank:

``KERNEL_TIERS`` — what makes a claim a kernel claim at all.

``DISCHARGING_TIERS`` — what closes an external assumption.

``REFUTING_TIERS`` — what is allowed to say a claim is false.

They are separate constants because they are separate questions. A finite
counterexample from executed code refutes a universally quantified claim
outright, and the same run establishes nothing about the general case, so
``computational`` belongs in one set and not the other. A referee's opinion
belongs in none of them: the failure the principles document names is a prover
and a critic from the same model family converging on an argument neither can
see through, and any gate an LLM verdict could pass reproduces it exactly.

The consequence is that ``closed_kernel`` is rare and expensive, since it
requires a mechanical discharge of every cited theorem. That is intended. A
``closed_kernel`` that were cheap to reach would carry no information, and the
honest state for most real research-level mathematics is
``conditional_kernel`` — proved, modulo named and citable external results.
Whether ``DISCHARGING_TIERS`` should ever widen is a question for data from a
real run, not for taste; it is one frozenset in one place when that data
arrives.

Routes are assessed here too, and they are the one place where this file
reports something it refuses to act on. A route is an AND over its
obligations; several routes for one goal are an OR. Neither direction moves the
goal's status: see ``ClaimAssessment.with_routes`` for why a completed
decomposition still confers nothing, and ``RouteStatus`` for why a dead one
refutes nothing.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .models import (
    ClaimVersion,
    EvidenceRecord,
    EvidenceTier,
    ExternalAssumption,
    ProofRoute,
    SubjectRef,
    Verdict,
    normalize_text,
)

__all__ = [
    "CITATION_CHECK_TIERS",
    "DISCHARGING_TIERS",
    "ESTABLISHED_STATUSES",
    "KERNEL_TIERS",
    "PRODUCIBLE_TIERS",
    "REFUTING_TIERS",
    "SETTLED_CITATION_STATUSES",
    "CitationAssessment",
    "CitationStatus",
    "ClaimAssessment",
    "ClaimStatus",
    "RouteAssessment",
    "RouteStatus",
    "assess_citation",
    "assess_claim",
    "assess_route",
    "assess_routes",
    "route_cycles",
]

#: An independent implementation whose errors are uncorrelated with the
#: model's. Only a proof kernel qualifies.
KERNEL_TIERS = frozenset({EvidenceTier.MECHANICAL})

#: What discharges a cited theorem. Formalizing it, or nothing — a literature
#: lookup establishes that a paper says something, and an LLM reading
#: establishes that a paper seems to say something, and neither establishes
#: that its hypotheses hold here. That last question is a statement-fidelity
#: question, and the principles document's rule is that the agent doing the
#: work cannot issue its own fidelity certificate.
DISCHARGING_TIERS = frozenset({EvidenceTier.MECHANICAL})

#: What may declare a claim false. Wider than ``KERNEL_TIERS`` on purpose:
#: exhibiting one counterexample is a finite, checkable act, and refusing to
#: hear it until someone formalizes the refutation would keep a claim alive
#: that is already dead.
REFUTING_TIERS = frozenset({EvidenceTier.MECHANICAL, EvidenceTier.COMPUTATIONAL})

#: Tiers a producer in this tree can actually write. Policy above says which
#: tiers *count*; this says which ones can be *reached*, and the two are not
#: the same set.
#:
#: ``computational`` is in ``REFUTING_TIERS`` and has no producer — see the
#: command-surface rule at the top of ``verticals/math/math_state.py``: a tier
#: may only be written by a program that performed a check of that kind, and no
#: such verifier exists here yet. That gap is deliberate and the docstring says
#: so. What was not deliberate is that the context projection rendered the
#: policy set straight into an agent's instructions, so a role reading "how do
#: I refute this" was offered a channel it cannot open. Telling a worker to do
#: something the tree has no way to do costs more than saying nothing: they
#: either look for the command until they give up, or conclude the state is
#: broken. Keep this in step with the producers, not with the policy sets.
PRODUCIBLE_TIERS = frozenset(
    {EvidenceTier.MECHANICAL, EvidenceTier.LITERATURE, EvidenceTier.JUDGEMENT}
)

#: What answers "is the cited proposition really in the cited source". The
#: ``literature`` tier alone, which is that tier's definition rather than a
#: policy choice: it is *an assertion about what a source says*.
#:
#: ``judgement`` is excluded because the reading that needs checking is usually
#: the one the citing agent already did, and a tier whose checker is the agent
#: cannot audit the agent. ``mechanical`` is excluded for the opposite reason:
#: formalizing the cited theorem does not check the citation, it removes the
#: dependency on it, and that stronger fact is already reported by the
#: assumption leaving ``undischarged``. A citation check is not a step towards
#: ``closed_kernel`` and must not be mistaken for one — see ``DISCHARGING_TIERS``,
#: which literature will not join.
CITATION_CHECK_TIERS = frozenset({EvidenceTier.LITERATURE})


class ClaimStatus(str, Enum):
    """Five states, because the fifth and fourth must not be one state.

    The plan this package comes from proposed five orthogonal status
    dimensions. They are collapsed to one here: with no run-time data about
    which distinctions the system actually uses, five independent enums would
    be five schemas to migrate and one to read. The single distinction that is
    not negotiable survives — ``conditional_kernel`` versus ``closed_kernel``,
    which is where every unproved external dependency shows up.

    ``proposed`` — asserted; nothing has checked it.

    ``supported`` — some channel that is not a kernel says yes. This is where
    almost all of a live project sits, and it is not a proof.

    ``refuted`` — a counterexample or a kernel says no. Outranks every support:
    an argument and a counterexample cannot both stand, and preferring the
    argument is how a project keeps working on a dead claim.

    ``conditional_kernel`` — a kernel verdict binding to this exact statement,
    with at least one external assumption still open. Correct *modulo* results
    taken on faith from elsewhere.

    ``closed_kernel`` — the same, with nothing left on faith.
    """

    PROPOSED = "proposed"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    CONDITIONAL_KERNEL = "conditional_kernel"
    CLOSED_KERNEL = "closed_kernel"


#: What a route may treat as an obligation it no longer has to prove. Kernel
#: states only: a route resting on informally supported lemmas has not been
#: discharged, and reporting it as discharged would be the arithmetic that
#: turns a chain of plausible steps into a proof.
ESTABLISHED_STATUSES = frozenset(
    {ClaimStatus.CONDITIONAL_KERNEL, ClaimStatus.CLOSED_KERNEL}
)


class RouteStatus(str, Enum):
    """``retired`` is not a failure state; it is the recorded reason not to retry.

    ``discharged`` and ``blocked`` are the two ways a route stops being work,
    and they are deliberately symmetric in what they do *not* do: neither one
    touches the goal. A discharged route has proved every obligation and has
    still proved nothing about the goal, because nothing has checked that the
    obligations imply it. A blocked route — one of its obligations is refuted —
    is dead as a plan, and the goal may be perfectly true by another route.
    Collapsing either into the goal's status would let a decomposition nobody
    verified decide a mathematical question.
    """

    OPEN = "open"
    DISCHARGED = "discharged"
    BLOCKED = "blocked"
    RETIRED = "retired"


@dataclass(frozen=True)
class ClaimAssessment:
    """Everything the records say about one claim, with nothing summed up.

    ``support`` maps each tier to the *distinct producers* that answered in it,
    rather than to a count. Six records from one referee show up as one tier
    with one producer, which is the honest rendering of what happened; a count
    of six would read like six checks.

    ``stale_evidence`` is reported rather than dropped. Silently discarding
    evidence that no longer binds would hide the most interesting event in the
    system — a statement moved under a finished verification — and
    ``lean_evidence`` records what happens to anything routed somewhere nobody
    reads.

    ``routes`` and ``notes`` are filled in by ``with_routes``, and only by a
    caller that can see the whole project: ``assess_claim`` cannot assess a
    route, because a route's obligations are other claims.
    """

    claim_id: str
    version: int
    status: ClaimStatus
    undischarged: tuple[str, ...] = ()
    support: Mapping[EvidenceTier, tuple[str, ...]] = field(default_factory=dict)
    stale_evidence: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    routes: tuple[RouteAssessment, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_kernel(self) -> bool:
        return self.status in ESTABLISHED_STATUSES

    def with_routes(self, routes: Iterable[RouteAssessment]) -> ClaimAssessment:
        """Attach the decompositions aimed at this claim. Never changes status.

        This is the whole of what "several routes are an OR" means here, and
        the restraint is the point. A route asserts *these obligations imply
        this goal*, and nothing in this package checks that implication — there
        is no ``SubjectKind.ROUTE``, so there is not even a way to record a
        verifier's answer about it. If a discharged route promoted its goal to
        a kernel status, an agent could mint ``closed_kernel`` by writing a
        decomposition nobody read: the same failure as a compiling Lean proof
        of a mistranslated statement, arriving through the scheduler instead of
        through the translator. Promotion stays where it is falsifiable, on
        evidence bound to the claim's own digest.

        So the OR is *reported*: which routes aim here, what each is waiting
        on, and — in ``notes`` — the two states a reader would otherwise have
        to infer, namely a decomposition that is finished except for the step
        nobody can check, and one that is dead without its goal being dead.
        ``notes`` is separate from ``issues`` because neither is a defect in
        the records; filing them under ``issues`` would teach a reader that
        issues are things that need not be fixed.

        When a verifier for the decomposition step exists — a Lean proof of
        ``obligations → goal`` is the obvious one — this is the method that
        changes, and the argument for changing it is that the implication has
        been checked, not that the obligations have.
        """
        attached = tuple(routes)
        notes: list[str] = []
        for route in attached:
            if route.status is RouteStatus.DISCHARGED:
                notes.append(
                    f"every obligation of route {route.route_id!r} is established, "
                    "and nothing has verified that they imply this claim; the "
                    "decomposition step is itself unproved, so it confers no status "
                    "here"
                )
            elif route.status is RouteStatus.BLOCKED:
                notes.append(
                    f"route {route.route_id!r} cannot be completed as written, "
                    "because "
                    + ", ".join(route.refuted_obligations)
                    + " is refuted; that refutes the route, not this claim, which "
                    "another decomposition may still prove"
                )
        return replace(self, routes=attached, notes=tuple(notes))

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "version": self.version,
            "status": self.status.value,
            "undischarged": list(self.undischarged),
            "support": {
                tier.value: list(producers)
                for tier, producers in sorted(
                    self.support.items(), key=lambda item: item[0].value
                )
            },
            "stale_evidence": list(self.stale_evidence),
            "issues": list(self.issues),
            "routes": [route.as_dict() for route in self.routes],
            "notes": list(self.notes),
        }


def _producers_by_tier(
    records: Iterable[EvidenceRecord],
) -> dict[EvidenceTier, tuple[str, ...]]:
    grouped: dict[EvidenceTier, list[str]] = {}
    for record in records:
        producers = grouped.setdefault(record.tier, [])
        name = record.produced_by.strip() or "<unnamed>"
        if name not in producers:
            producers.append(name)
    return {tier: tuple(sorted(names)) for tier, names in grouped.items()}


def _discharged(
    assumption_ref: SubjectRef, evidence: Iterable[EvidenceRecord]
) -> bool:
    return any(
        record.binds_to(assumption_ref)
        and record.verdict is Verdict.SUPPORTS
        and record.tier in DISCHARGING_TIERS
        for record in evidence
    )


def assess_claim(
    claim: ClaimVersion,
    evidence: Iterable[EvidenceRecord],
    *,
    inherited_assumptions: Iterable[ExternalAssumption] = (),
) -> ClaimAssessment:
    """Derive one claim's status from the records that still bind to it.

    Takes the whole evidence collection rather than a pre-filtered list on
    purpose: which records bind and which have gone stale is the answer this
    function exists to compute, and a caller that filtered first would have had
    to make that judgement already.

    ``inherited_assumptions`` are dependencies an earlier version of this claim
    carried and this version dropped without recording why. They count exactly
    as if they were still listed, which is what stops a deletion from buying a
    ``closed_kernel``. Working that out needs the claim's history, which one
    version does not have, so the caller supplies it —
    ``store.MathState.assess`` does; a caller assessing a single record in
    isolation passes nothing and gets the reading that record supports on its
    own.
    """
    records = list(evidence)
    current = claim.ref()
    fresh = [record for record in records if record.binds_to(current)]
    stale = tuple(
        sorted(
            record.evidence_id
            for record in records
            if not record.binds_to(current)
            and record.subject.kind is current.kind
            and record.subject.subject_id == current.subject_id
        )
    )

    issues: list[str] = []
    own_ids = {item.assumption_id for item in claim.external_assumptions}
    inherited = tuple(
        item
        for item in inherited_assumptions
        if item.assumption_id not in own_ids
    )
    standing = tuple(claim.external_assumptions) + inherited
    undischarged = tuple(
        assumption.assumption_id
        for assumption in standing
        if not _discharged(assumption.ref(), records)
    )

    if inherited:
        # Dropping a dependency is a mathematical assertion that the proof did
        # not need it. Unstated, it is not an assertion, so it does not hold.
        issues.append(
            "this version no longer lists "
            + ", ".join(sorted(item.assumption_id for item in inherited))
            + ", and no revision recorded why; the dependency still counts"
        )

    kernel_supports = [
        record
        for record in fresh
        if record.tier in KERNEL_TIERS and record.verdict is Verdict.SUPPORTS
    ]
    refutations = [
        record
        for record in fresh
        if record.tier in REFUTING_TIERS and record.verdict is Verdict.REFUTES
    ]
    supports = [record for record in fresh if record.verdict is Verdict.SUPPORTS]

    if kernel_supports and any(
        record.tier in KERNEL_TIERS for record in refutations
    ):
        # Two kernels cannot both be right about the same statement. Reporting
        # a winner here would bury the only fact worth acting on.
        issues.append(
            "a proof kernel both supports and refutes this exact statement; one "
            "of the two records is not about the mathematics it names"
        )

    if kernel_supports and not claim.formal_statement.strip():
        # Somebody recorded a kernel verdict about a claim that has no
        # formalization to have been checked. Withholding kernel status is the
        # only safe reading.
        issues.append(
            "kernel evidence is recorded for a claim with no formal statement, "
            "so there is nothing the kernel could have checked"
        )

    if refutations:
        status = ClaimStatus.REFUTED
    elif kernel_supports and claim.formal_statement.strip():
        status = (
            ClaimStatus.CONDITIONAL_KERNEL
            if undischarged
            else ClaimStatus.CLOSED_KERNEL
        )
    elif supports:
        status = ClaimStatus.SUPPORTED
    else:
        status = ClaimStatus.PROPOSED

    return ClaimAssessment(
        claim_id=claim.claim_id,
        version=claim.version,
        status=status,
        undischarged=undischarged,
        support=_producers_by_tier(supports),
        stale_evidence=stale,
        issues=tuple(issues),
    )


# -- citations ---------------------------------------------------------------

class CitationStatus(str, Enum):
    """Whether anyone has been to the source, and what they found when they did.

    ``unchecked`` and ``disputed`` are separate states for the same reason
    ``Verdict`` keeps ``inconclusive``: a citation nobody has looked up and a
    citation somebody looked up and could not find are different facts about the
    world, and a project that collapses them ships the second one believing it
    is the first. ``inconclusive`` is the third: the checker ran, reached the
    source or failed to reach it, and could not settle the question. It is not a
    pass, and it is not a failure of the citation.

    ``uncited`` is not a grade at all. It says the assumption names its source in
    prose and names no proposition inside it, so there is nothing a checker could
    be sent to look at. That is a legitimate state — an unpublished result or a
    private communication has no locator — and calling it ``unchecked`` would
    describe work that is not merely undone but undoable, which is how a queue
    fills with tasks nobody can close.

    Nothing here is a claim status. A confirmed citation says the source really
    contains the proposition; it says nothing about whether the proposition's
    hypotheses hold in this setting, which is the third question and a fidelity
    one. The assumption stays undischarged either way, and the claim stays at
    ``conditional_kernel``.

    ``self_checked`` is the one state that is about the checker rather than the
    source. It says every supporting answer came from the party that filed the
    assumption, which is the reading under review restating itself. It is a
    separate state and not simply ``unchecked`` because the work list they
    generate is different: ``unchecked`` asks for a reader, and this asks for a
    *different* reader, and someone told the first thing does the obvious wrong
    thing, which is to check it again.
    """

    UNCITED = "uncited"
    UNCHECKED = "unchecked"
    SELF_CHECKED = "self_checked"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class CitationAssessment:
    """What the records say about one assumption's citation.

    ``checked_by`` names the producers rather than counting them, for the reason
    ``ClaimAssessment.support`` does: three answers from one checker are one
    check. ``artifacts`` carries where each answer can be re-read, because a
    literature verdict with nothing to re-inspect is exactly the unfalsifiable
    record ``ARTIFACT_REQUIRED_TIERS`` exists to reject, and a reviewer asked to
    trust "confirmed" needs the excerpt it was confirmed from.
    """

    assumption_id: str
    status: CitationStatus
    cited_proposition: str = ""
    checked_by: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()

    @property
    def is_settled(self) -> bool:
        """Whether this citation still owes anyone an answer.

        ``disputed`` counts as settled: the checker went, looked, and reported.
        What to do about it is a mathematical decision — correct the locator,
        drop the dependency, prove it instead — and none of those are "check it
        again". A gate that treated a refutation as unfinished work would ask
        for the one thing already done.
        """
        return self.status in SETTLED_CITATION_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "assumption_id": self.assumption_id,
            "status": self.status.value,
            "cited_proposition": self.cited_proposition,
            "checked_by": list(self.checked_by),
            "artifacts": list(self.artifacts),
        }


#: Citation states that owe nobody further retrieval. ``uncited`` is here
#: because there is nothing to retrieve, not because it is satisfactory.
SETTLED_CITATION_STATUSES = frozenset(
    {CitationStatus.UNCITED, CitationStatus.CONFIRMED, CitationStatus.DISPUTED}
)


def assess_citation(
    assumption: ExternalAssumption, evidence: Iterable[EvidenceRecord]
) -> CitationAssessment:
    """Derive one citation's state from the checks that still bind to it.

    Binding is by ``assumption.ref()``, so correcting the locator, the source,
    or the statement drops every check obtained about the old one — the same
    rule that governs claims, and the reason ``source_id`` and ``locator`` are
    in the assumption's digest at all. A check of ``Theorem 3.1`` does not
    quietly become a check of ``Theorem 3.2``.

    A refutation outranks a confirmation. Two checkers disagreeing about whether
    a paper contains a proposition is not a tie to be broken by counting: "it is
    not there" is a finite observation about a specific document, and preferring
    the agreeing checker is how a wrong citation reaches a reader. Both
    producers are reported, so the disagreement is visible rather than resolved
    in silence.

    A confirmation from the assumption's own filer is not one. The party who
    wrote "Theorem 3.2 of [K]" is the party whose reading is in question, so
    their own answer that [K] does contain a Theorem 3.2 is that reading
    restated — the assertion under review, not a check of it. So supports are
    counted only from producers other than ``filed_by``, and a citation whose
    every supporter is the filer reads ``self_checked``: still open, and open
    for a reason that says what would close it.

    This applies to supports only. A filer who *refutes* their own citation is
    reporting against interest, which is the one direction self-checking cannot
    manufacture, and discarding it would let a worker bury a citation they had
    already found to be wrong. Inconclusive is treated the same way, for the
    same reason: it concedes rather than claims.

    An assumption with no recorded filer — one written before the field existed,
    or through the kernel API rather than the CLI — cannot be measured against
    this rule, and is not retroactively downgraded. That is a real gap and the
    honest place for it: the check reports what it knows, and the CLI requires
    ``--by`` so that nothing recorded from here on lands in that gap.
    """
    subject = assumption.ref()
    if not assumption.cited_proposition:
        return CitationAssessment(assumption.assumption_id, CitationStatus.UNCITED)

    checks = [
        record
        for record in evidence
        if record.binds_to(subject) and record.tier in CITATION_CHECK_TIERS
    ]
    verdicts = {record.verdict for record in checks}
    filer = normalize_text(assumption.filed_by)
    independent_support = any(
        record.verdict == Verdict.SUPPORTS
        and (not filer or normalize_text(record.produced_by) != filer)
        for record in checks
    )
    if Verdict.REFUTES in verdicts:
        status = CitationStatus.DISPUTED
    elif independent_support:
        status = CitationStatus.CONFIRMED
    elif Verdict.SUPPORTS in verdicts:
        status = CitationStatus.SELF_CHECKED
    elif Verdict.INCONCLUSIVE in verdicts:
        status = CitationStatus.INCONCLUSIVE
    else:
        status = CitationStatus.UNCHECKED

    return CitationAssessment(
        assumption_id=assumption.assumption_id,
        status=status,
        cited_proposition=assumption.cited_proposition,
        checked_by=tuple(
            sorted({record.produced_by.strip() or "<unnamed>" for record in checks})
        ),
        artifacts=tuple(
            sorted({record.artifact for record in checks if record.artifact})
        ),
    )


# -- routes ------------------------------------------------------------------

@dataclass(frozen=True)
class RouteAssessment:
    """What this route still needs, and whether it is still about this problem."""

    route_id: str
    status: RouteStatus
    outstanding: tuple[str, ...] = ()
    refuted_obligations: tuple[str, ...] = ()
    stale_obligations: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "status": self.status.value,
            "outstanding": list(self.outstanding),
            "refuted_obligations": list(self.refuted_obligations),
            "stale_obligations": list(self.stale_obligations),
            "issues": list(self.issues),
        }


def assess_route(
    route: ProofRoute,
    assessments: Mapping[SubjectRef, ClaimAssessment],
    *,
    in_cycle: Sequence[str] = (),
) -> RouteAssessment:
    """Which obligations are left, keyed by reference so a moved statement shows.

    ``assessments`` is keyed by ``SubjectRef``, not by claim id, which is what
    makes the second question answerable: an obligation whose id is present but
    whose digest is not means the lemma this route was built on has been
    restated. That route may still be a good idea, but it is no longer a plan
    for the claims it names, and a status of ``open`` alone would not say so.

    ``in_cycle`` names the mutually dependent group this route belongs to, if
    any. One route cannot see that by itself — a cycle is a property of the
    whole set — so a caller with only one route in hand passes nothing and gets
    the reading that route supports alone. ``assess_routes`` is the caller that
    knows.
    """
    outstanding: list[str] = []
    refuted: list[str] = []
    stale: list[str] = []
    issues: list[str] = []

    if route.goal not in assessments:
        issues.append(
            "this route's goal is not a current claim, so it aims at a statement "
            "that has been restated or removed"
        )

    for obligation in route.obligations:
        assessment = assessments.get(obligation)
        if assessment is None:
            stale.append(obligation.subject_id)
        elif assessment.status is ClaimStatus.REFUTED:
            refuted.append(obligation.subject_id)
        elif assessment.status not in ESTABLISHED_STATUSES:
            outstanding.append(obligation.subject_id)

    if in_cycle:
        # Reported as an issue, which costs the route ``discharged`` below even
        # when every obligation is established. That combination is exactly the
        # state a cycle produces on a hand-edited file, and calling it
        # discharged would be calling a plan complete that can never be
        # started.
        others = sorted(set(in_cycle) - {route.route_id})
        if others:
            issues.append(
                "this route depends on itself through "
                + ", ".join(others)
                + ", so no work on it can ever begin"
            )
        else:
            issues.append(
                "this route lists its own goal among its obligations, so no work "
                "on it can ever begin"
            )

    if route.retired_because.strip():
        status = RouteStatus.RETIRED
    elif refuted:
        # A refuted obligation kills the route however much of the rest is
        # done, and it says nothing about the goal: that asymmetry is the whole
        # reason routes and claims have separate status enums.
        status = RouteStatus.BLOCKED
    elif outstanding or stale or issues:
        status = RouteStatus.OPEN
    elif not route.obligations:
        # A route with no obligations asserts the goal follows from nothing.
        issues.append(
            "this route lists no obligations, so it records no plan; a route "
            "that needs nothing proved is not a route"
        )
        status = RouteStatus.OPEN
    else:
        status = RouteStatus.DISCHARGED

    return RouteAssessment(
        route_id=route.route_id,
        status=status,
        outstanding=tuple(sorted(dict.fromkeys(outstanding))),
        refuted_obligations=tuple(sorted(dict.fromkeys(refuted))),
        stale_obligations=tuple(sorted(dict.fromkeys(stale))),
        issues=tuple(issues),
    )


def _route_edges(routes: Sequence[ProofRoute]) -> dict[str, tuple[str, ...]]:
    """``route -> the routes that would prove its obligations``.

    Retired routes are left out of the graph entirely, as sources and as
    targets. A route nobody will execute cannot make a project loop, and
    recording a circular attempt together with the reason it was abandoned is
    precisely what ``retired_because`` is for; a check that refused to let a
    dead end be written down would be paid for in the same repeated attempt it
    is meant to prevent.

    Two routes sharing an id — impossible through ``add_route``, reachable by
    editing the file — have their edges merged rather than one overwriting the
    other. Merging can only invent a cycle, never hide one, and inventing one
    costs a false report while hiding one costs the check.
    """
    live = [route for route in routes if not route.retired_because.strip()]
    by_goal: dict[SubjectRef, list[str]] = {}
    for route in live:
        by_goal.setdefault(route.goal, []).append(route.route_id)

    targets: dict[str, list[str]] = {route.route_id: [] for route in live}
    for route in live:
        for obligation in route.obligations:
            targets[route.route_id].extend(by_goal.get(obligation, ()))
    return {
        route_id: tuple(sorted(dict.fromkeys(reached)))
        for route_id, reached in targets.items()
    }


def route_cycles(routes: Iterable[ProofRoute]) -> tuple[tuple[str, ...], ...]:
    """Every group of routes that depends, eventually, on itself.

    A group rather than a path, and that is the difference between a check and
    a sampler. A graph can hold exponentially many distinct cycles, so any
    function that returned cycles as paths would either enumerate them (and
    blow up) or return a few witnesses (and leave routes that are genuinely
    circular unreported — a route can sit in a mutually dependent group without
    lying on any one cycle a depth-first search happens to close). Strongly
    connected components are complete and linear in the graph, so this reports
    each group once, sorted, with every member named.

    A group is cyclic when it has more than one member, or one member that
    reaches itself — a route listing its own goal.

    Linear in routes plus obligations. That matters because the write-time
    check in ``store.MathState.add_route`` calls this on every route added: a
    per-call cost that grew with the square of the project would make recording
    a decomposition quietly expensive exactly as the project got interesting.
    """
    edges = _route_edges(list(routes))
    groups: list[tuple[str, ...]] = []
    for component in _strongly_connected(edges):
        if len(component) > 1 or component[0] in edges.get(component[0], ()):
            groups.append(tuple(sorted(component)))
    return tuple(sorted(groups))


def _strongly_connected(edges: Mapping[str, tuple[str, ...]]) -> list[list[str]]:
    """Tarjan's algorithm, iterative.

    Iterative rather than recursive because the depth is the length of a
    decomposition chain, which is data — a hand-written or agent-written state
    file can nest deeper than the interpreter's stack, and a check that raises
    ``RecursionError`` on a large project is a check that stops running when it
    is most needed.
    """
    order: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for root in sorted(edges):
        if root in order:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        order[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, position = work[-1]
            children = edges.get(node, ())
            descended = False
            while position < len(children):
                child = children[position]
                position += 1
                if child not in order:
                    work[-1] = (node, position)
                    order[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, 0))
                    descended = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], order[child])
            if descended:
                continue
            work[-1] = (node, position)
            work.pop()
            if low[node] == order[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return components


def assess_routes(
    routes: Iterable[ProofRoute],
    assessments: Mapping[SubjectRef, ClaimAssessment],
) -> tuple[RouteAssessment, ...]:
    """Assess routes as a set, because being cyclic is a property of the set.

    Returned in the order given, so a caller that holds the routes can pair
    each assessment with the route it came from — which is how
    ``store.MathState`` groups them under the goals they aim at.
    """
    ordered = list(routes)
    membership: dict[str, tuple[str, ...]] = {}
    for group in route_cycles(ordered):
        for route_id in group:
            membership[route_id] = group
    return tuple(
        assess_route(route, assessments, in_cycle=membership.get(route.route_id, ()))
        for route in ordered
    )
