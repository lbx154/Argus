"""One mission, one claim's neighbourhood, and nothing else in the prompt.

A mathematics project's recorded state is the wrong shape to hand an agent.
It grows without bound, most of it is about claims this task is not working
on, and the part that matters — what is still taken on faith, what evidence
binds to the statement *as it now reads*, which routes are already dead — is
buried in it. Pasting the file in is unreadable; pasting a summary of "the
project so far" is unreadable and still does not say which claim this task is
about.

The projection exists to answer that second question first, and the tests
below are organised around the four properties that make it safe to put its
output into an Engineer's prompt:

* **Targeting.** The claim is found in the mission's own text, most-decisive
  field first. Naming nothing is fine and yields nothing; naming two claims is
  refused rather than guessed at, because a fragment aimed at the wrong
  theorem is internally consistent and therefore invisible.
* **Boundedness.** What appears is a function of the claim, not of the
  project. A project that grew by forty claims renders the same bytes.
* **Determinism.** Same store, same mission, same bytes — no timestamps, no
  host paths, no dict-order dependence — so an unchanged digest really does
  mean nothing moved.
* **Degrading honestly.** No recorded mathematics means no fragment. A store
  that cannot be read means a fragment that *says so*, because silence there
  would tell the Engineer that a project holding a hundred proofs believes
  nothing.

The adversarial cases are cheap to build and would be expensive to notice: a
claim id that is a prefix of another id, a lemma named only in the non-goals,
evidence left over from a previous version of the statement.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from argus.life.memory import BacklogItem
from argus.proof_ledger import (
    ClaimVersion,
    ContextVersion,
    EvidenceRecord,
    EvidenceTier,
    ExternalAssumption,
    MathState,
    ProofRoute,
    Verdict,
    load_state,
    save_state,
    state_path,
)
from argus.verticals._base import load_vertical_contract
from argus.verticals.math import context_projection
from argus.verticals.math.context_projection import (
    MISSION_TARGET_FIELDS,
    project_mission_context,
    resolve_target,
)

# -- fixtures ---------------------------------------------------------------

SZEMEREDI = ExternalAssumption(
    assumption_id="szemeredi-trotter",
    statement="Point-line incidences in the plane are O((mn)^(2/3) + m + n).",
    source="Szemeredi-Trotter 1983",
)

#: The same shape with a locator attached, which is what makes it checkable:
#: ``source`` says where a human would look, ``source_id``/``locator`` name a
#: proposition a checker can be sent to. Kept apart from ``SZEMEREDI`` so the
#: uncited case above stays exercised by every test that uses the main fixture.
RH_ERROR = ExternalAssumption(
    assumption_id="rh-error-term",
    statement=(
        "Under RH the prime number theorem error term is O(x^(1/2+eps))."
    ),
    source="Iwaniec-Kowalski, Analytic Number Theory",
    source_id="doi:10.1090/coll/053",
    locator="Theorem 5.15",
)


def _context(state: MathState, context_id: str, statement: str, **definitions: str):
    return state.add_context(
        ContextVersion(
            context_id=context_id,
            version=1,
            statement=statement,
            definitions=dict(definitions),
        )
    )


def _claim(
    state: MathState,
    claim_id: str,
    context: ContextVersion,
    natural: str,
    *,
    formal: str = "",
    assumptions: tuple[ExternalAssumption, ...] = (),
) -> ClaimVersion:
    return state.add_claim(
        ClaimVersion(
            claim_id=claim_id,
            version=1,
            context=context.ref(),
            natural_statement=natural,
            formal_statement=formal,
            external_assumptions=assumptions,
        )
    )


def _lean(
    claim: ClaimVersion,
    evidence_id: str,
    *,
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


def _literature(
    assumption: ExternalAssumption,
    evidence_id: str,
    *,
    verdict: Verdict = Verdict.SUPPORTS,
    produced_by: str = "citation_check reader",
    artifact: str = "research/literature/rh-error-term.json",
) -> EvidenceRecord:
    """One reader's answer about one citation.

    Addressed to ``assumption.ref()``, which is the whole reason this layer is
    invisible to the evidence section: a literature record never binds to the
    claim, and must not, because "the paper says it" is not "the theorem holds
    here".
    """
    return EvidenceRecord(
        evidence_id=evidence_id,
        subject=assumption.ref(),
        tier=EvidenceTier.LITERATURE,
        verdict=verdict,
        produced_by=produced_by,
        artifact=artifact,
    )


def _cited(tmp_path: Path) -> MathState:
    """One claim standing on one *checkable* import, which is the subject here.

    Deliberately not ``_seed``: that store's assumption names its source in
    prose alone, so it is permanently ``uncited`` and could never show a
    checker's answer changing anything.
    """
    state = MathState()
    context = _context(
        state,
        "ctx-primes",
        "Error terms in the prime number theorem.",
        **{"error term": "the difference pi(x) - li(x)"},
    )
    _claim(
        state,
        "pnt-error",
        context,
        "The prime number theorem error term is O(x^(1/2+eps)).",
        formal="theorem pnt_error : ...",
        assumptions=(RH_ERROR,),
    )
    save_state(tmp_path, state)
    return state


def _seed(tmp_path: Path) -> MathState:
    """Two unrelated claims, each with its own context, routes and evidence.

    Two is the minimum that can show a leak: every anti-leak test below asserts
    something about `erdos-sum` while projecting `udist-main`, and would pass
    vacuously against a store holding one claim.
    """
    state = MathState()

    plane = _context(
        state,
        "ctx-plane",
        "Bound the number of unit distances among n points in the plane.",
        **{
            "unit distance": "a pair of points at Euclidean distance exactly 1",
            "incidence": "a point lying on a line",
        },
    )
    main = _claim(
        state,
        "udist-main",
        plane,
        "The number of unit distances among n planar points is O(n^(4/3)).",
        formal="theorem unit_distance_bound (n : Nat) : ...",
        assumptions=(SZEMEREDI,),
    )
    lemma = _claim(
        state,
        "lemma-crossing",
        plane,
        "A graph with e >= 4v edges has crossing number at least e^3 / (64 v^2).",
    )
    deep = _claim(
        state,
        "lemma-euler",
        plane,
        "A simple planar graph on v >= 3 vertices has at most 3v - 6 edges.",
    )
    state.add_evidence(_lean(main, "ev-lean-main"))
    state.add_route(
        ProofRoute(
            route_id="via-crossing",
            goal=main.ref(),
            obligations=(lemma.ref(),),
            retired_because="",
        )
    )
    state.add_route(
        ProofRoute(
            route_id="via-incidence",
            goal=main.ref(),
            obligations=(lemma.ref(),),
            retired_because="the incidence bound it needs is the theorem itself",
        )
    )
    # The second hop. If the projection ever follows dependencies transitively,
    # `lemma-euler` shows up in a mission about `udist-main`.
    state.add_route(
        ProofRoute(
            route_id="crossing-via-euler",
            goal=lemma.ref(),
            obligations=(deep.ref(),),
            retired_because="",
        )
    )

    sums = _context(
        state,
        "ctx-sums",
        "Distinct sums of a finite set of reals.",
        **{"sumset": "the set of pairwise sums of a set with itself"},
    )
    other = _claim(
        state,
        "erdos-sum",
        sums,
        "A set of n reals has at least n^(2-o(1)) distinct sums or products.",
    )
    state.add_evidence(_lean(other, "ev-lean-sum"))
    state.add_route(
        ProofRoute(
            route_id="sum-via-elekes",
            goal=other.ref(),
            obligations=(),
            retired_because="",
        )
    )

    save_state(tmp_path, state)
    return state


def _mission(**fields: object) -> BacklogItem:
    """A backlog item shaped the way the Planner actually delivers one."""
    return BacklogItem.new(
        title=str(fields.pop("title", "Advance the current bound")),
        objective=str(fields.pop("objective", "Make progress on the open case")),
        **fields,  # type: ignore[arg-type]
    )


def _project(tmp_path: Path, **fields: object) -> str:
    return project_mission_context(project_root=tmp_path, mission=_mission(**fields))


def _digest_of(fragment: str) -> str:
    match = re.search(r"fragment digest `([0-9a-f]+)`", fragment)
    return match.group(1) if match else ""


# -- targeting --------------------------------------------------------------

def test_two_missions_about_different_claims_get_different_context(
    tmp_path: Path,
) -> None:
    """The point of the hook: per mission, not per stage.

    Before this existed, every task in a stage received the same block, which
    is the same as writing it once in the role banner. If these two fragments
    were ever equal the whole mechanism would be decoration.
    """
    _seed(tmp_path)

    unit = _project(tmp_path, acceptance_check="udist-main is proved or refuted.")
    sums = _project(tmp_path, acceptance_check="erdos-sum is proved or refuted.")

    assert unit and sums
    assert unit != sums
    assert "udist-main" in unit and "erdos-sum" not in unit
    assert "erdos-sum" in sums and "udist-main" not in sums


def test_the_acceptance_check_decides_the_target_before_the_title(
    tmp_path: Path,
) -> None:
    """One field has to win, and it should be the one that says what is owed.

    A title is a label an agent wrote to be readable; the acceptance check is
    the field whose declared job is to state what this round must make true.
    When they disagree, believing the title would aim the fragment at whatever
    the task was filed under rather than at what it has to move.
    """
    _seed(tmp_path)

    fragment = _project(
        tmp_path,
        title="Follow-up work on erdos-sum",
        acceptance_check="A recorded verdict for udist-main.",
    )

    assert "claim `udist-main`" in fragment
    assert "erdos-sum" not in fragment
    assert MISSION_TARGET_FIELDS[0] == "acceptance_check"


def test_a_claim_named_only_in_the_non_goals_is_not_the_target(
    tmp_path: Path,
) -> None:
    """Naming a claim to exclude it must not select it.

    ``non_goals`` is the Planner's field for "not this round". Scanning it for
    ids would invert its meaning and aim the entire fragment at the one
    statement the mission was told to leave alone -- and the Engineer, reading
    a coherent block about a real claim, has no way to notice.
    """
    _seed(tmp_path)

    fragment = _project(
        tmp_path,
        acceptance_check="A recorded verdict for udist-main.",
        non_goals=["Do not touch erdos-sum this round."],
    )

    assert "claim `udist-main`" in fragment
    assert "erdos-sum" not in fragment
    assert "non_goals" not in MISSION_TARGET_FIELDS


def test_a_claim_id_is_not_matched_inside_a_longer_id(tmp_path: Path) -> None:
    """Ids in this schema share prefixes as a matter of course.

    ``lemma-crossing`` contains no separate claim here, but a project that
    holds both ``lemma`` and ``lemma-crossing`` is entirely normal, and a
    substring match would make the shorter id a permanent false positive that
    also makes every mission about the longer one ambiguous.
    """
    state = MathState()
    context = _context(state, "ctx", "Crossing numbers.")
    _claim(state, "lemma", context, "The short one.")
    _claim(state, "lemma-crossing", context, "The long one.")
    save_state(tmp_path, state)

    target = resolve_target(
        _mission(acceptance_check="Prove lemma-crossing."),
        ("lemma", "lemma-crossing"),
    )

    assert target.claim_id == "lemma-crossing"
    assert not target.ambiguous


def test_a_mission_that_names_two_claims_is_refused_rather_than_guessed(
    tmp_path: Path,
) -> None:
    """Ambiguity is reported, not broken by a tiebreak.

    Picking the first, or the longest, or falling through to the title would
    produce a fragment that is correct-looking prose about a real claim the
    mission may not be about. The Engineer cannot detect that. A block that
    says "I could not tell which" can be acted on.
    """
    _seed(tmp_path)

    fragment = _project(
        tmp_path,
        acceptance_check="Both udist-main and erdos-sum have recorded verdicts.",
    )

    assert fragment.startswith("## Mathematical state not projected")
    assert "`udist-main`" in fragment and "`erdos-sum`" in fragment
    # Nothing about either claim's actual state: that is the guess it refused.
    assert "Taken on faith" not in fragment
    assert "szemeredi-trotter" not in fragment


def test_a_mission_that_names_no_recorded_claim_contributes_nothing(
    tmp_path: Path,
) -> None:
    """Most math tasks name no claim id, and must pay nothing for the feature.

    The fallback is silence rather than "here is the whole project", because
    the only thing worse than no context is a page of context about claims
    chosen by proximity.
    """
    _seed(tmp_path)

    assert _project(
        tmp_path,
        title="Read the literature on distance problems",
        objective="Find out what is known about incidence bounds.",
    ) == ""


# -- what the fragment says -------------------------------------------------

def test_the_fragment_states_the_claim_its_faith_its_evidence_and_its_next_steps(
    tmp_path: Path,
) -> None:
    """The five things a mission cannot reconstruct by reading the repository.

    Each of these is a decision already made and recorded: the exact statement
    and version in force, what it is still standing on, what has actually
    checked it, what the live plan owes, and which branch is already dead.
    An Engineer without them re-derives the state from the files and gets it
    wrong in the expensive direction -- by retrying a retired route or by
    treating an assumption as proved.
    """
    _seed(tmp_path)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert "claim `udist-main` v1" in fragment
    assert "unit distances" in fragment          # the statement itself
    # The definition *body*, not just its name. An earlier version of this
    # assertion looked for "`unit distance`" alone, which was also satisfied by
    # a line listing the term as withheld -- so it passed while the fragment
    # shipped no definitions at all and asserted the claim did not name this
    # one. Pin the text an Engineer would actually have to work from.
    assert "a pair of points at Euclidean distance exactly 1" in fragment
    assert "szemeredi-trotter" in fragment       # still taken on faith
    assert "`ev-lean-main`" in fragment          # what checked this statement
    assert "`via-crossing`" in fragment          # the live route
    assert "`lemma-crossing`" in fragment        # what that route owes
    assert "`via-incidence`" in fragment         # already retired
    assert "the incidence bound it needs is the theorem itself" in fragment
    assert "closed_kernel" in fragment           # what would move it


def test_every_definition_of_the_claims_context_is_shipped(tmp_path: Path) -> None:
    """No lexical filter decides which definitions the mission may see.

    This module used to ship only definitions whose names appeared in the
    claim's statement. It got the very first case wrong -- the claim says "unit
    distances", the definition is named "unit distance" -- and then asserted,
    in the fragment, that the claim did not name it. The filter is a regex
    standing in for a semantic question, and it fails on plurals, possessives
    and every paraphrase.

    The asymmetry is what settles it: a withheld definition is a term the
    Engineer then interprets on its own understanding, which is exactly what
    the "context not recorded" branch forbids in bold, and can end in a
    confidently wrong proof. A shipped unneeded one costs about forty tokens.
    These definitions belong to *this claim's own* context version, so nothing
    about project growth is at stake here; ``_MAX_ROWS`` is what bounds the
    section, and the test below pins that.

    Written as a claim about `incidence` specifically -- a definition the
    target claim never mentions in any form -- so that reinstating any filter,
    however cleverly matched, turns this red.
    """
    _seed(tmp_path)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert "`incidence`: a point lying on a line" in fragment
    assert "`unit distance`: a pair of points at Euclidean distance exactly 1" in fragment
    # And it must not editorialise about which ones the claim needed.
    assert "not named by the claim" not in fragment


def test_only_the_first_hop_of_the_proof_tree_is_shown(tmp_path: Path) -> None:
    """Depth 1, because depth 2 is the whole tree.

    ``lemma-euler`` is what ``lemma-crossing`` needs, not what this claim
    needs. Following dependencies transitively in a project with a real proof
    tree reproduces the state file, which is the thing this module exists not
    to send -- and none of those statements is something this round could move
    anyway.
    """
    _seed(tmp_path)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert "`lemma-crossing`" in fragment
    assert "lemma-euler" not in fragment
    assert "crossing-via-euler" not in fragment


def test_evidence_for_an_earlier_version_is_shown_as_not_evidence(
    tmp_path: Path,
) -> None:
    """A restated claim keeps its history and loses its certificate.

    This is the single most dangerous thing an agent can get wrong here:
    ``ev-lean-main`` is a real mechanical pass, it is still in the file, and it
    is about a sentence the project no longer asserts. Dropping it silently
    would hide that the proof exists for something; listing it as evidence
    would launder it into support for the new statement. It is named, under a
    heading that says it is not evidence for this version.
    """
    state = _seed(tmp_path)
    state.revise_claim(
        "udist-main",
        natural_statement="The number of unit distances is O(n^(1+eps)).",
    )
    save_state(tmp_path, state)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert "claim `udist-main` v2" in fragment
    assert "not evidence for it" in fragment
    assert "`ev-lean-main`" in fragment
    assert "none. Nothing has checked this statement as it now stands." in fragment


# -- the literature layer ---------------------------------------------------

def test_a_checked_citation_changes_what_the_mission_is_handed(
    tmp_path: Path,
) -> None:
    """The regression this section exists for: it used to change nothing.

    A reader opened the source, quoted the proposition, archived the excerpt
    and recorded ``confirmed`` -- and the fragment came out byte-identical,
    digest included. So every worker sent at this claim re-retrieved the same
    paper to learn what one of them had already written down, and the fragment
    said the same "taken on faith" line each time, which is true and is not the
    whole truth.

    The artifact path is the load-bearing part of the assertion. "Someone
    checked it" is a status word a worker has to trust; a path is a thing they
    can open, and only the second one makes "do not retrieve this again" a safe
    instruction rather than a request for faith in a faith-tracking system.
    """
    state = _cited(tmp_path)
    before = _project(tmp_path, acceptance_check="Record a verdict for pnt-error.")

    state.add_evidence(_literature(RH_ERROR, "ev-lit-rh"))
    save_state(tmp_path, state)

    after = _project(tmp_path, acceptance_check="Record a verdict for pnt-error.")

    assert after != before
    assert "citation confirmed" in after
    assert "research/literature/rh-error-term.json" in after
    assert "citation_check reader" in after
    assert "doi:10.1090/coll/053 Theorem 5.15" in after
    # The digest is what a reader consults to decide whether anything moved.
    # If it were stable across this change it would say "nothing moved" about
    # the round in which the literature actually got read.
    assert _digest_of(after) != _digest_of(before)
    # And it arrives as citation state, not as support for the theorem. A
    # literature record binds to the assumption, so the claim is still unchecked
    # as a statement -- the section that says so must keep saying so, or a
    # lookup has been laundered into a proof.
    assert "none. Nothing has checked this statement as it now stands." in after
    assert "(proposed)" in after


def test_an_unchecked_citation_says_nobody_has_been(tmp_path: Path) -> None:
    """The default state has to be legible, or the confirmed one means nothing.

    A row that showed the locator and stayed silent about whether anyone had
    used it reads, to a worker deciding what to spend a retrieval on, exactly
    like a row that was checked. "Nobody has opened the source yet" is the
    sentence that turns the citation into an assignable piece of work.
    """
    _cited(tmp_path)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for pnt-error.")

    assert "citation unchecked" in fragment
    assert "Nobody has opened the source yet." in fragment
    assert "doi:10.1090/coll/053 Theorem 5.15" in fragment
    # Nothing to re-open, so nothing may be offered as if there were.
    assert "read at" not in fragment


def test_an_inconclusive_citation_is_not_reported_as_a_confirmation(
    tmp_path: Path,
) -> None:
    """Reached the document, did not settle the proposition -- a third state.

    This is the one a reader is most likely to round to "checked": a DOI that
    resolves feels like an answer. It says the paper exists, which nobody
    doubted, and says nothing about whether Theorem 5.15 is in it or says this.
    The row keeps the artifact, because the next checker should start from what
    the last one saw rather than from the search bar.
    """
    state = _cited(tmp_path)
    state.add_evidence(_literature(RH_ERROR, "ev-lit-rh", verdict=Verdict.INCONCLUSIVE))
    save_state(tmp_path, state)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for pnt-error.")

    assert "citation inconclusive" in fragment
    assert "citation confirmed" not in fragment
    assert "still unverified" in fragment
    assert "research/literature/rh-error-term.json" in fragment


def test_an_assumption_with_no_locator_is_not_reported_as_unchecked(
    tmp_path: Path,
) -> None:
    """An import nobody can be sent to look up is not an open task.

    ``szemeredi-trotter`` names its source in prose and names no proposition
    inside it -- an ordinary shape for a classical result, an unpublished note
    or a private communication. Rendering that as ``unchecked`` would put a
    permanent, unclosable retrieval into every mission about this claim.
    """
    _seed(tmp_path)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert "citation uncited" in fragment
    assert "nothing to retrieve" in fragment
    assert "citation unchecked" not in fragment


def test_a_much_checked_citation_cannot_flood_its_row(tmp_path: Path) -> None:
    """The new row is a list like every other one here, and gets the same cap.

    Checkers and excerpts accumulate: a contested citation collects a reading
    per worker who doubted it, and every one of them is legitimately part of
    this claim's neighbourhood, so the depth-1 rule admits all of them. An
    uncapped row would reintroduce, inside a bullet, exactly the unbounded
    growth ``_MAX_ROWS`` exists to stop.

    Asserted without naming the cap: whatever the row withheld, it has to say
    how much. A test that pinned the constant would go green on a change that
    silently truncated.
    """
    state = _cited(tmp_path)
    artifacts = [f"research/literature/check-{index:02d}.json" for index in range(20)]
    for index, artifact in enumerate(artifacts):
        state.add_evidence(
            _literature(
                RH_ERROR,
                f"ev-lit-{index:02d}",
                produced_by=f"reader-{index:02d}",
                artifact=artifact,
            )
        )
    save_state(tmp_path, state)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for pnt-error.")

    assert "citation confirmed" in fragment
    assert artifacts[0] in fragment
    withheld = [artifact for artifact in artifacts if artifact not in fragment]
    assert withheld, "the row shipped every excerpt it had, so it is uncapped"
    assert f"and {len(withheld)} more" in fragment


# -- boundedness ------------------------------------------------------------

def test_the_fragment_does_not_grow_with_the_project(tmp_path: Path) -> None:
    """Adding claims elsewhere in the store changes nothing here.

    This is one of the two size properties and it is the narrower one: it is
    about the *store*, not about the claim. Anything that scaled with the store
    -- a project summary, an index of open claims, a "recent activity" list --
    would eventually cost more context than the mission itself and get skimmed,
    at which point the parts that matter get skimmed too.

    It says nothing about how large one claim's own neighbourhood may get; that
    is a separate property with its own test below, because for a while it went
    undefended and was not actually true.
    """
    state = _seed(tmp_path)
    before = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    context = state.latest_context("ctx-sums")
    assert context is not None
    for index in range(40):
        filler = _claim(
            state,
            f"filler-{index:03d}",
            context,
            f"Auxiliary estimate number {index}.",
        )
        state.add_evidence(_lean(filler, f"ev-filler-{index:03d}"))
        state.add_route(
            ProofRoute(
                route_id=f"route-filler-{index:03d}",
                goal=filler.ref(),
                obligations=(),
                retired_because="",
            )
        )
    save_state(tmp_path, state)

    after = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert after == before
    assert "filler-" not in after


def test_one_overloaded_claim_cannot_flood_the_prelude(tmp_path: Path) -> None:
    """The size property the store-growth test above does not cover.

    "Bounded by the claim" is not the same as "bounded". A single claim can
    carry sixty definitions and a hundred and twenty cited theorems -- that is
    an ordinary shape for a survey-grade result, not a pathological one -- and
    every one of those rows is legitimately part of *this* claim's
    neighbourhood, so the depth-1 rule admits all of them.

    Before ``_MAX_ROWS`` was applied to these four lists, this exact fixture
    rendered a 59,575-character fragment: roughly fifteen thousand tokens of
    prelude in front of a mission, from one claim, with no other claim in the
    project. Open assumptions were the worst of it at two 220-character clips
    apiece and no cap at all.

    The cap is asserted through the rendered size rather than through
    ``_MAX_ROWS`` directly, because what matters is the property, not the
    constant -- and a future list added to the payload without a cap makes this
    red without anyone having to remember to update it.
    """
    state = MathState()
    context = state.add_context(
        ContextVersion(
            context_id="ctx-big",
            version=1,
            statement="A problem with a long vocabulary.",
            definitions={
                f"term-{index:03d}": f"The meaning of term {index}. " * 12
                for index in range(60)
            },
        )
    )
    _claim(
        state,
        "survey-main",
        context,
        "The main estimate holds.",
        formal="theorem survey_main : ...",
        assumptions=tuple(
            ExternalAssumption(
                assumption_id=f"cited-{index:03d}",
                statement=f"Cited theorem {index} says something at length. " * 8,
                source=f"Author {index}, Journal of Long Citations, 19{index:02d}. " * 4,
            )
            for index in range(120)
        ),
    )
    save_state(tmp_path, state)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for survey-main.")

    assert "claim `survey-main`" in fragment
    assert len(fragment) < 12_000, len(fragment)
    # Truncation must announce itself. A capped list that said nothing would
    # tell the Engineer this claim rests on twelve cited theorems when it rests
    # on a hundred and twenty -- which is worse than the flood it replaced.
    assert "more open assumption(s), not listed here" in fragment
    assert "further definition(s) in this context, not listed here" in fragment
    # The same sentence, applied to the one line that states a number. The
    # heading counted the rows that survived the cap, so this fixture rendered
    # "### Taken on faith (12 open)" twelve rows above "and 108 more" -- the
    # fragment told the truth and contradicted it, with the false version
    # first and in the position a skimming reader anchors on. A count in a
    # heading is a claim about the claim, not about the rendering.
    assert "### Taken on faith (120 open)" in fragment


def test_the_digest_is_computed_from_the_fragments_own_content(
    tmp_path: Path,
) -> None:
    """The digest is advertised as load-bearing, so it needs its own test.

    The module docstring tells the reader that seeing the same digest twice
    means nothing moved. That promise is only worth something if the digest is
    a function of the content -- a constant, or a hash of something adjacent,
    would read exactly the same in a prompt and be silently meaningless. The
    byte-identity tests elsewhere pass happily against a constant digest.
    """
    _seed(tmp_path)

    unit = _digest_of(_project(tmp_path, acceptance_check="Prove udist-main."))
    sums = _digest_of(_project(tmp_path, acceptance_check="Prove erdos-sum."))

    assert unit and sums
    assert unit != sums
    assert unit == _digest_of(_project(tmp_path, acceptance_check="Prove udist-main."))


def test_work_on_an_unrelated_claim_leaves_this_mission_byte_identical(
    tmp_path: Path,
) -> None:
    """Anti-leak, stated as a diff rather than as an absence.

    Asserting only that ``erdos-sum`` does not appear would still pass if some
    aggregate over the whole store -- a count, a status tally, a digest of
    everything -- crept into the text. Byte equality across a real change
    elsewhere in the file is the stronger claim, and it is the one that makes
    the fragment's own digest mean "nothing about this claim moved".
    """
    state = _seed(tmp_path)
    before = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    other = state.revise_claim(
        "erdos-sum",
        natural_statement="A set of n reals has at least n^(2-o(1)) distinct sums.",
        external_assumptions=(SZEMEREDI,),
    )
    state.add_evidence(_lean(other, "ev-sum-second", verdict=Verdict.REFUTES))
    save_state(tmp_path, state)

    assert _project(tmp_path, acceptance_check="Record a verdict for udist-main.") == before


def test_a_change_to_the_target_claim_changes_what_the_mission_sees(
    tmp_path: Path,
) -> None:
    """The other side of the byte-identity claim.

    A fragment that were stable under changes to its *own* claim would be
    stable for the wrong reason -- and the digest it carries would say "nothing
    moved" while the claim moved. Discharging the assumption is the change with
    the largest consequence in the kernel: it takes the claim from conditional
    to closed.
    """
    state = _seed(tmp_path)
    before = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")
    assert "szemeredi-trotter" in before

    state.add_evidence(
        EvidenceRecord(
            evidence_id="ev-szt",
            subject=SZEMEREDI.ref(),
            tier=EvidenceTier.MECHANICAL,
            verdict=Verdict.SUPPORTS,
            produced_by="lean_check 4.9.0",
            artifact="research/lean/szemeredi_trotter.json",
        )
    )
    save_state(tmp_path, state)

    after = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert after != before
    assert "(closed_kernel)" in after
    # Still named, because a discharged assumption is a covered dependency
    # rather than an absent one, and a reader who cannot see it has no way to
    # ask whether the discharge is any good.
    assert "szemeredi-trotter" in after


# -- determinism ------------------------------------------------------------

def test_the_same_mission_on_an_unchanged_store_renders_the_same_bytes(
    tmp_path: Path,
) -> None:
    """Byte-identical, or the digest it prints is worth nothing.

    Two runs of the same mission that differ by a timestamp, an iteration
    order, or a host path would make every comparison between fragments read
    as "something changed", which trains the reader to ignore the one time it
    did. The reload in the middle is deliberate: it re-parses the JSON, so a
    dependence on in-memory insertion order would show up here.
    """
    _seed(tmp_path)
    check = "Record a verdict for udist-main."

    first = _project(tmp_path, acceptance_check=check)
    reloaded = load_state(tmp_path)
    save_state(tmp_path, reloaded)
    second = _project(tmp_path, acceptance_check=check)

    assert first == second
    assert first.count("fragment digest") == 1
    # A host path would differ between machines and between worktrees, and
    # would put the operator's directory layout into a model's prompt.
    assert str(tmp_path) not in first


# -- degrading honestly -----------------------------------------------------

def test_a_project_with_no_recorded_mathematics_contributes_nothing(
    tmp_path: Path,
) -> None:
    """Almost every math project is this one, and it must pay nothing.

    Both the never-written case and the written-but-empty case return the same
    thing, because both mean the same thing: this project records no claims.
    Neither may raise -- the hook runs inside mission setup, where an exception
    is a failed mission.
    """
    assert _project(tmp_path, acceptance_check="Prove udist-main.") == ""

    save_state(tmp_path, MathState())

    assert _project(tmp_path, acceptance_check="Prove udist-main.") == ""


def test_an_unreadable_state_says_so_instead_of_looking_empty(
    tmp_path: Path,
) -> None:
    """A broken file and an absent one mean opposite things.

    Returning ``""`` for a corrupt store would tell a project with a hundred
    recorded proofs that it believes nothing -- and the Engineer's rational
    next move is to start recording claims into the file that is already
    holding them, over the top of proofs it cannot read. The block names the
    file and refuses to characterise the state.
    """
    _seed(tmp_path)
    state_path(tmp_path).write_text("{not json", encoding="utf-8")

    fragment = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert fragment.startswith("## Mathematical state unavailable")
    assert "research/MATH_STATE.json" in fragment
    assert "not as empty" in fragment
    assert str(tmp_path) not in fragment


def test_a_state_file_with_the_wrong_shape_is_unreadable_not_empty(
    tmp_path: Path,
) -> None:
    """Valid JSON is not a valid state, and the difference is not visible.

    A hand-edit or a half-finished write leaves a file that parses. The kernel
    rejects it; this asserts the rejection reaches the prompt as a stated
    failure rather than as silence, which is the same argument as the corrupt
    case but a much likelier accident.
    """
    _seed(tmp_path)
    state_path(tmp_path).write_text(json.dumps(["claims"]), encoding="utf-8")

    fragment = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert fragment.startswith("## Mathematical state unavailable")
    assert str(tmp_path) not in fragment


def test_a_bug_in_the_projector_does_not_send_anyone_to_repair_the_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A defect here is reported as a defect here, not as a broken file.

    The never-raise guarantee means every failure inside this module lands in
    one handler, and it is tempting to give them all the same text. It must not
    be the unreadable-file text. That text says "repair or report the file",
    and everything reachable from this handler has already parsed -- so the
    instruction would dispatch an autonomous Engineer to edit a perfectly
    healthy ``MATH_STATE.json``, the one file whose corruption the other branch
    correctly calls unrecoverable, on the strength of a bug in the code that
    summarises it.

    Still no exception escapes: the mission runs, with a fragment that says
    what actually happened.
    """
    _seed(tmp_path)

    def _explode(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ZeroDivisionError("projector arithmetic went wrong")

    monkeypatch.setattr(context_projection, "_payload", _explode)

    fragment = _project(tmp_path, acceptance_check="Record a verdict for udist-main.")

    assert fragment.startswith("## Mathematical state not projected")
    assert "read successfully" in fragment
    assert "ZeroDivisionError" in fragment
    assert "is intact and is NOT the fault" in fragment
    # The instruction that belongs to the other branch, and only to it.
    assert "Repair or report the file" not in fragment
    assert str(tmp_path) not in fragment


def test_an_ambiguous_target_does_not_silently_drop_candidates(
    tmp_path: Path,
) -> None:
    """The refusal block is a list like any other, and gets the same cap.

    Naming twelve of fifteen candidates and stopping would misreport the very
    thing the block exists to report -- how ambiguous the mission's own text
    is. A reader who counts twelve has no way to know there were more.
    """
    state = MathState()
    context = _context(state, "ctx", "Many small lemmas.")
    for index in range(15):
        _claim(state, f"lemma-{index:02d}", context, f"Lemma {index}.")
    save_state(tmp_path, state)

    fragment = _project(
        tmp_path,
        acceptance_check=" ".join(f"lemma-{index:02d}" for index in range(15)),
    )

    assert fragment.startswith("## Mathematical state not projected")
    assert "and 3 more" in fragment


# -- wiring -----------------------------------------------------------------

def test_the_math_vertical_serves_this_projection_as_its_mission_prelude(
    tmp_path: Path,
) -> None:
    """The module is only worth anything if the contract actually calls it.

    Everything above tests the projector directly and would keep passing if
    ``stages.prepare_mission`` were deleted or never wired up. This is the one
    test that fails when the hook is disconnected -- it goes through the same
    contract object the supervisor builds, with the same keyword arguments.
    """
    _seed(tmp_path)
    item = _mission(acceptance_check="Record a verdict for udist-main.")
    contract = load_vertical_contract("math", project_root=tmp_path)

    block = contract.prepare_mission(
        stage="solve",
        project_root=tmp_path,
        state_root=tmp_path / "runtime",
        mission=item,
    )

    assert block == project_mission_context(project_root=tmp_path, mission=item)
    assert "claim `udist-main`" in block
