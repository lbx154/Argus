"""What one mission needs to know about one claim, and nothing else.

A mathematics project accumulates state that no agent can be handed whole: a
few hundred claim versions, every verdict ever recorded against them, and the
routes that were tried and abandoned. Handing an Engineer the state file, or
the event log, or a summary of "the project so far", fails in both directions
at once — it is too long to read and it still does not say which of those
claims this particular task is about.

So this projects. Given the backlog item the supervisor just claimed, it finds
the claim that item is about and renders that claim's *neighbourhood*: the
statement and its version, the definitions it is stated against, the external
results it is still taking on faith and whether anyone has yet been to the
sources they cite, the evidence that binds to this exact statement, the
immediate proof obligations, the routes already retired, and what would have to
happen for its status to move. Nothing transitive, and nothing about any other
claim.

Three properties are load-bearing, and each has a test that fails when it stops
holding:

**Bounded by the claim, not by the project.** Only depth-1 dependencies appear.
A second hop would pull in the obligations' obligations, and in a project with a
real proof tree that is the whole tree — which is the thing this module exists
not to send. One hop is also what a single mission can act on: it is the set of
statements whose status could change as a result of this round. That rule alone
is not a bound, though — one claim can carry a hundred cited theorems — so
every list rendered below is additionally capped by ``_MAX_ROWS`` and says how
many rows it withheld.

**Deterministic.** The same store and the same mission render byte-identical
text. No timestamps, no host paths, no reliance on ``dict`` order. The fragment
carries a digest of its own content so two of them can be told apart without
diffing prose — and so a reader who sees the same digest twice knows nothing
moved, rather than assuming it.

**It never raises into a mission.** A missing store means "this project does no
recorded mathematics" and produces nothing at all; a store that will not load
produces a block that says so; a defect in *this code* produces a block that
says that instead, and says the recorded state is intact. The three are kept
apart because they need opposite responses, and the wrong one is expensive: an
empty fragment from a broken file would tell the Engineer that a project with a
hundred recorded proofs believes nothing, and a "repair the file" instruction
aimed at a projector bug points an autonomous agent at a healthy
``MATH_STATE.json``.

This module is an adapter and lives on the vertical side deliberately.
``argus_skill/proof_ledger/`` imports nothing from Argus so it can be lifted
out; a projector that reads ``BacklogItem`` is exactly the Argus-shaped code
that would stop it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...proof_ledger import (
    DISCHARGING_TIERS,
    KERNEL_TIERS,
    REFUTING_TIERS,
    STATE_RELPATH,
    CitationAssessment,
    CitationStatus,
    ClaimStatus,
    ClaimVersion,
    ContextVersion,
    MathState,
    MathStateError,
    assess_route,
    content_digest,
    load_state,
)

__all__ = [
    "MISSION_TARGET_FIELDS",
    "MissionTarget",
    "project_mission_context",
    "resolve_target",
]

#: Where the state lives, as the mission should refer to it: project-relative.
#: An absolute path would differ per host and per worktree, which would break
#: the determinism this module promises and put a machine-specific string into
#: a prompt that gets compared across runs.
_STATE_REF = "/".join(STATE_RELPATH)

#: The mission fields consulted for the target claim, most decisive first.
#:
#: These are the three model-authored prose fields that exist on *both* intake
#: paths. The flat Planner path fills ``TASK_TITLE``, ``TASK_OBJECTIVE``,
#: ``TASK_ACCEPTANCE_CHECK`` and ``TASK_NON_GOALS`` and says outright that the
#: host owns everything else. The Manager's bounded-DAG path carries more than
#: that: ``manager/dispatch.py`` builds a ``BacklogItem`` straight from a
#: model-authored ``BoundedDagNode``, so ``node_key``, ``context_refs`` (which
#: do survive on that path), ``plan_hypothesis``, ``goal_contribution`` and
#: ``expected_regressions`` are model-authored there too.
#:
#: This tuple is therefore a choice, not an exhaustive inventory of what a
#: model can write -- do not extend it on the belief that nothing else could
#: carry a claim id, and do not assume adding a field to ``BoundedDagNode``
#: leaves the set complete. ``plan_hypothesis`` is the closest call and is left
#: out deliberately: it is prose about *why* a step should work, which tends to
#: name every claim in the neighbourhood, so scanning it would widen the
#: ambiguity surface (see ``resolve_target``) for very little reach.
#:
#: ``non_goals`` is absent for a different reason: a claim named there is the
#: one the mission was told *not* to work on, and projecting it would aim the
#: whole fragment at the excluded statement.
MISSION_TARGET_FIELDS = ("acceptance_check", "title", "objective")

#: Characters that unambiguously continue an identifier. Used as a boundary so
#: that a claim called ``lemma-a`` is not found inside ``lemma-a-prime``: ids in
#: this schema are chosen by whoever recorded the claim, and prefix
#: relationships between them are the normal case rather than a strange one.
_ID_CORE = r"[A-Za-z0-9_\-]"

#: ``.`` is the hard case and needs its own rule. It is a legitimate id
#: character (``thm.3.1``), so treating it as a boundary would find ``thm.3``
#: inside ``thm.3.1``. But it is also how English ends a sentence, and an
#: acceptance check that reads "Prove udist-main." is the ordinary way a
#: mission names its claim -- treating it as an id character there makes the
#: single most common phrasing invisible. So a dot continues an id only when
#: something else follows it that continues an id too.
_LEFT = rf"(?<!{_ID_CORE})(?<!{_ID_CORE}\.)"
_RIGHT = rf"(?!{_ID_CORE})(?!\.{_ID_CORE})"

#: One statement, clipped. A claim statement is written by an agent and is
#: claim-sized by nature; this is a backstop against a pasted proof, not a
#: summarization policy, so it is generous and marked when it fires.
_MAX_TEXT = 600

#: A shorter clip for text that appears once per neighbour rather than once per
#: fragment.
_MAX_NEIGHBOUR_TEXT = 220

#: Rows per list. Every list rendered below goes through ``_rows``: the
#: neighbourhood rule bounds most of them by construction, but "bounded by the
#: claim" is not the same as "bounded", and a single claim carrying 120 cited
#: theorems is exactly the pathology this is here for. A list that is capped
#: reports what it withheld rather than truncating in silence.
_MAX_ROWS = 12


@dataclass(frozen=True)
class MissionTarget:
    """Which claim a mission is about, or why that could not be decided."""

    claim_id: str = ""
    field: str = ""
    candidates: tuple[str, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return not self.claim_id and bool(self.candidates)


def _mentions(text: str, name: str) -> bool:
    """Does ``text`` name this identifier, as an identifier rather than a substring?

    Case-sensitive, and there is no option to make it otherwise: the only
    caller matches claim ids, which are identifiers. This once had a
    ``fold_case`` switch for matching definition names, which are prose; that
    caller is gone (see ``_definitions``) and the switch went with it rather
    than staying as a configurable that nothing configures.
    """
    if not name.strip():
        return False
    return re.search(rf"{_LEFT}{re.escape(name)}{_RIGHT}", text) is not None


def resolve_target(mission: Any, claim_ids: tuple[str, ...]) -> MissionTarget:
    """Find the claim this mission is about by the ids its own text names.

    A claim id is matched literally and case-sensitively: ids are identifiers,
    and folding case would let a mission that says "the main result" target a
    claim someone named ``main``.

    Fields are consulted most-decisive first, and the first field that names any
    claim decides. ``acceptance_check`` leads because it is the one field whose
    declared job is to say what this mission has to make true. If that field
    names two claims the search *stops* rather than falling through to the
    title: letting a vaguer field break a tie the decisive one could not is
    guessing, and a fragment aimed at the wrong theorem is worse than no
    fragment — the Engineer cannot tell it is wrong, because it is internally
    consistent and about a real claim.
    """
    for field_name in MISSION_TARGET_FIELDS:
        text = str(getattr(mission, field_name, "") or "")
        if not text.strip():
            continue
        named = tuple(
            sorted({claim_id for claim_id in claim_ids if _mentions(text, claim_id)})
        )
        if len(named) == 1:
            return MissionTarget(claim_id=named[0], field=field_name)
        if named:
            return MissionTarget(field=field_name, candidates=named)
    return MissionTarget()


def project_mission_context(*, project_root: Path | str, mission: Any) -> str:
    """Render this mission's claim neighbourhood, or nothing.

    Returns ``""`` for the overwhelmingly common case: a project with no
    recorded mathematical state, or a mission that names no claim in it. A math
    mission must run normally in a project that has never written this file.
    """
    try:
        state = load_state(project_root)
    except MathStateError as exc:
        return _unreadable(exc, project_root)
    except Exception as exc:  # noqa: BLE001 - never raise into a mission
        return _unreadable(exc, project_root)

    try:
        claim_ids = tuple(claim.claim_id for claim in state.current_claims())
        if not claim_ids:
            return ""
        target = resolve_target(mission, claim_ids)
        if target.ambiguous:
            return _ambiguous(target)
        if not target.claim_id:
            return ""
        return _render(_payload(state, target))
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        # A defect in this module must not take the mission down with it, and
        # must not look like "the project believes nothing" either. It also
        # must not look like a broken file: everything reachable from here has
        # already parsed, so the fault is in this code.
        return _defective(exc, project_root)


# -- degraded readings -------------------------------------------------------

def _scrub(text: str, project_root: Path | str) -> str:
    """Strip the host's absolute paths out of a message bound for a prompt.

    ``proof_ledger`` formats its errors with the real path, which differs by
    host and by worktree. Leaving it in would make two identical failures render
    differently and would leak the operator's directory layout into the prompt.
    """
    root = str(Path(str(project_root)))
    return str(text).replace(f"{root}/", "").replace(root, ".")


def _unreadable(exc: BaseException, project_root: Path | str) -> str:
    """The file is there and the kernel would not load it.

    This is the only branch entitled to send anyone at the file, and it is the
    branch where the file really is the problem.
    """
    return (
        "## Mathematical state unavailable\n"
        f"- `{_STATE_REF}` exists but could not be read: "
        f"{_scrub(exc, project_root) or type(exc).__name__}\n"
        "- Treat the recorded claim/evidence state as unknown, not as empty. "
        "Repair or report the file before recording anything into it; writing "
        "over an unreadable state loses every proof it held."
    )


def _defective(exc: BaseException, project_root: Path | str) -> str:
    """The file loaded and *this module* failed while projecting it.

    Kept distinct from ``_unreadable`` because the two need opposite advice and
    only one of them is about the file. Reporting a projector bug as an
    unreadable state would dispatch an autonomous Engineer to "repair" a
    perfectly healthy ``MATH_STATE.json`` -- the one file in the project whose
    corruption the other branch correctly calls unrecoverable, and which no
    amount of editing here could improve. So this one says what is actually
    true: the state is intact, the code that summarises it is not, and the
    remedy is a bug report rather than a file edit.
    """
    return (
        "## Mathematical state not projected\n"
        f"- `{_STATE_REF}` was read successfully, but building this summary of "
        f"it failed inside the projector ({type(exc).__name__}: "
        f"{_scrub(exc, project_root) or 'no message'}).\n"
        "- The recorded state itself is intact and is NOT the fault. Do not "
        "repair, rewrite, or re-record it on the strength of this message. "
        "Read it directly if this mission needs it, and report the failure "
        "above as a defect in the math vertical."
    )


def _ambiguous(target: MissionTarget) -> str:
    shown, further = _rows(list(target.candidates))
    listed = ", ".join(f"`{claim_id}`" for claim_id in shown)
    if further:
        listed += f", and {further} more"
    return (
        "## Mathematical state not projected\n"
        f"- This mission's {target.field} names several recorded claims: {listed}.\n"
        "- No claim state is shown, because picking one of them would aim this "
        "context at a statement the mission may not be about, and the mistake "
        f"would be invisible. Read `{_STATE_REF}` directly, or restate that "
        "field so it names the single claim this round must move."
    )


# -- the projection ----------------------------------------------------------

def _clip(text: object, limit: int = _MAX_TEXT) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + " […clipped]"


def _rows(items: list[Any]) -> tuple[list[Any], int]:
    return items[:_MAX_ROWS], max(0, len(items) - _MAX_ROWS)


def _tiers(names: frozenset) -> str:
    return " or ".join(sorted(tier.value for tier in names))


def _reachable_tiers(names: frozenset) -> str:
    """Render only the tiers of ``names`` something in this tree can write.

    These lines are read as instructions — "to refuted: <tiers> evidence may
    say this is false" is the answer a role gets when it asks how to kill a
    claim. ``REFUTING_TIERS`` includes ``computational``, which has no producer
    here, so the honest rendering names the channel that can actually be opened
    and says plainly that the other one is not wired up rather than omitting it
    (a role that knows a counterexample refutes will otherwise go looking for
    the command that records one).
    """
    from ...proof_ledger import PRODUCIBLE_TIERS

    reachable = names & PRODUCIBLE_TIERS
    unreachable = names - PRODUCIBLE_TIERS
    if not reachable:
        return (
            f"no channel in this tree ({_tiers(names)} would count, but nothing "
            "here produces it)"
        )
    if not unreachable:
        return _tiers(reachable)
    return (
        f"{_tiers(reachable)} ({_tiers(unreachable)} would also count, but this "
        "tree has no producer for it yet)"
    )


def _definitions(context: ContextVersion | None) -> tuple[list[list[str]], int]:
    """Every definition of the claim's own context version, capped.

    This used to filter: ship only the definitions whose names appear in the
    claim's statement, and list the rest as "defined here, not named by the
    claim". ``ContextVersion``'s own docstring motivates that -- the mapping
    exists "so that a later context projection can ship a claim only the
    definitions it names" -- so the reason it is *not* done at this call site
    needs to be written down rather than left as a silent contradiction.

    The filter was a lexical ``re.search`` standing in for a semantic question,
    and it got the answer wrong on the very first claim it was pointed at: a
    claim reading "the number of unit distances among n planar points" does not
    literally contain "unit distance", so the definition was withheld and the
    fragment asserted, in the same voice it uses for facts the Engineer must
    act on, that the claim did not name it. Inflection, plural, possessive and
    any paraphrase all break it the same way.

    What made that worth abandoning rather than repairing is the asymmetry.
    A withheld definition is a term the Engineer then interprets on its own
    understanding -- which is the exact thing the "context not recorded" branch
    below forbids in bold -- and can produce a confidently wrong proof. A
    shipped unneeded definition costs about forty tokens. Nor did the filter
    buy boundedness: these definitions come from *this claim's* context
    version, so they are already claim-scoped, and the growth this module
    exists to prevent is growth with the project. ``_rows`` is what bounds the
    section, and it says how many it withheld.

    Listing the withheld names was the fallback, and it is not actionable: it
    means "open MATH_STATE.json and find the key", which nothing in the mission
    path makes convenient.
    """
    if context is None:
        return [], 0
    return _rows(
        [
            [name, _clip(body, _MAX_NEIGHBOUR_TEXT)]
            for name, body in sorted(context.definitions.items())
        ]
    )


def _citation(citation: CitationAssessment | None) -> dict[str, Any]:
    """Whether anyone has already been to the source, and where what they read is.

    This is derived from ``state.citations`` rather than from the evidence list
    below, and the separation is the point. A literature check is addressed to
    ``assumption.ref()``, so it does not bind to the claim's reference and must
    not be made to: evidence that a paper contains a theorem is evidence about
    the import, never about the statement importing it, and admitting it to
    "evidence bound to this exact statement" would launder a lookup into
    support for the theorem.

    Kept apart, it is still the missing half of what a worker needs. Before
    this, a citation somebody had opened, read, quoted and archived changed
    this fragment by zero bytes, so every worker on the claim paid the same
    retrieval to learn what one of them already knew. ``artifacts`` is the
    operative field: "someone checked this" is only actionable when what they
    saw can be re-opened, and it is what makes not re-retrieving the paper a
    safe instruction rather than a request to trust a status word.

    ``checked_by`` names producers rather than counting them, for the reason
    ``CitationAssessment`` does: three answers from one reader are one check.
    Both lists go through ``_rows``, because a much-cited assumption is exactly
    where a per-row list stops being small.
    """
    if citation is None:
        # Cannot happen from the one call site -- ``citations`` is derived from
        # the same ``effective_assumptions`` the rows are -- and is answered
        # with silence rather than a status, because inventing "unchecked" here
        # would assert nobody has looked on the strength of a lookup failure.
        return {
            "status": "",
            "proposition": "",
            "checked_by": [],
            "further_checked_by": 0,
            "artifacts": [],
            "further_artifacts": 0,
        }
    checkers, further_checkers = _rows(list(citation.checked_by))
    artifacts, further_artifacts = _rows(list(citation.artifacts))
    return {
        "status": citation.status.value,
        "proposition": _clip(citation.cited_proposition, _MAX_NEIGHBOUR_TEXT),
        "checked_by": [_clip(name, _MAX_NEIGHBOUR_TEXT) for name in checkers],
        "further_checked_by": further_checkers,
        "artifacts": [_clip(path, _MAX_NEIGHBOUR_TEXT) for path in artifacts],
        "further_artifacts": further_artifacts,
    }


def _payload(state: MathState, target: MissionTarget) -> dict[str, Any]:
    """Everything the fragment says, as plain data, so the digest covers it all.

    The rendering below reads only this mapping. That is what makes the digest
    honest: it cannot go stale against text that was assembled somewhere else,
    and ``content_digest`` sorts keys, so it cannot depend on insertion order.
    """
    claim_id = target.claim_id
    # No ``assert claim is not None`` here. It would be inert three times over:
    # the id came from ``current_claims()``, ``AssertionError`` is caught by
    # the handler in ``project_mission_context`` like anything else, and under
    # ``-O`` the assert vanishes and the next line raises ``AttributeError``
    # into the same handler. It would read as a guarantee while guaranteeing
    # nothing.
    claim = state.latest_claim(claim_id)
    assessment = state.assess(claim_id)
    current = claim.ref()

    routes = [route for route in state.routes if route.goal.subject_id == claim_id]
    # ``assess_all`` is O(claims x evidence) over the entire store -- the one
    # genuinely expensive call in this module, and the only project-wide one.
    # A claim with no recorded route needs none of it, and that is the common
    # case in a young project, so it is computed only when a route is going to
    # be appraised against it.
    everything: dict[Any, Any] = state.assess_all() if routes else {}

    context = next(
        (item for item in state.contexts if item.ref() == claim.context), None
    )
    latest_context = state.latest_context(claim.context.subject_id)
    definitions, further_definitions = _definitions(context)

    open_ids = set(assessment.undischarged)
    standing = state.effective_assumptions(claim_id)
    # Only the open ones are given their citation state: those are the imports
    # this mission may actually act on, and the retrieval it might repeat.
    citations = {item.assumption_id: item for item in state.citations(claim_id)}
    open_assumptions, further_open = _rows(
        [
            {
                "assumption_id": item.assumption_id,
                "statement": _clip(item.statement, _MAX_NEIGHBOUR_TEXT),
                "source": _clip(item.source, _MAX_NEIGHBOUR_TEXT),
                "citation": _citation(citations.get(item.assumption_id)),
            }
            for item in standing
            if item.assumption_id in open_ids
        ]
    )
    # Discharged assumptions are named but not restated: they are settled, and
    # what the mission needs from them is that they exist and are covered. That
    # is also why they carry no citation state -- a discharged assumption has
    # been established here, so where its source was looked up is history
    # rather than work anyone is about to repeat.
    discharged, further_discharged = _rows(
        [item.assumption_id for item in standing if item.assumption_id not in open_ids]
    )

    bound_evidence = [
        {
            "evidence_id": record.evidence_id,
            "tier": record.tier.value,
            "verdict": record.verdict.value,
            "produced_by": record.produced_by,
            "artifact": record.artifact,
        }
        for record in sorted(state.evidence, key=lambda item: item.evidence_id)
        if record.binds_to(current)
    ]

    live_routes: list[dict[str, Any]] = []
    retired_routes: list[dict[str, Any]] = []
    for route in sorted(routes, key=lambda item: item.route_id):
        appraisal = assess_route(route, everything)
        if route.retired_because.strip():
            retired_routes.append(
                {
                    "route_id": route.route_id,
                    "reason": _clip(route.retired_because, _MAX_NEIGHBOUR_TEXT),
                }
            )
            continue
        obligations, more_obligations = _rows(
            [
                _obligation_row(state, obligation, everything)
                for obligation in route.obligations
            ]
        )
        live_routes.append(
            {
                "route_id": route.route_id,
                "status": appraisal.status.value,
                "aims_at_current_statement": route.goal == current,
                "obligations": obligations,
                "further_obligations": more_obligations,
                "issues": list(appraisal.issues),
            }
        )

    live_rows, more_routes = _rows(live_routes)
    retired_rows, more_retired = _rows(retired_routes)
    evidence_rows, more_evidence = _rows(bound_evidence)
    stale_rows, more_stale = _rows(list(assessment.stale_evidence))
    issue_rows, more_issues = _rows(list(assessment.issues))
    # Every list below is a ``_rows`` pair. Nothing may be written into this
    # mapping that the renderer does not read: an unrendered key still feeds
    # ``content_digest``, so it could move the digest without changing a byte
    # of the text, and the digest's whole job is to mean "this fragment".
    return {
        "claim_id": claim_id,
        "version": claim.version,
        "status": assessment.status.value,
        "targeted_by": target.field,
        "natural_statement": _clip(claim.natural_statement),
        "formal_statement": _clip(claim.formal_statement),
        "context": {
            "context_id": claim.context.subject_id,
            "version": context.version if context is not None else 0,
            "recorded": context is not None,
            "current": (
                latest_context is not None and latest_context.ref() == claim.context
            ),
            "statement": _clip(context.statement) if context is not None else "",
            "definitions": definitions,
            "further_definitions": further_definitions,
        },
        "open_assumptions": open_assumptions,
        "further_open_assumptions": further_open,
        "discharged_assumptions": discharged,
        "further_discharged_assumptions": further_discharged,
        "evidence": evidence_rows,
        "further_evidence": more_evidence,
        "stale_evidence": stale_rows,
        "further_stale_evidence": more_stale,
        "issues": issue_rows,
        "further_issues": more_issues,
        "routes": live_rows,
        "further_routes": more_routes,
        "retired_routes": retired_rows,
        "further_retired_routes": more_retired,
        "transitions": _transitions(assessment.status, claim),
    }


def _obligation_row(
    state: MathState, obligation: Any, everything: dict
) -> dict[str, Any]:
    """One dependency, as it stands now — depth 1 and no further.

    The statement shown is the *current* version's, not the one the route was
    built on, because that is the lemma anyone would go and work on. When the
    two differ the row says so: the route is still an idea, but it is no longer
    a plan for the claims it names.
    """
    latest = state.latest_claim(obligation.subject_id)
    appraisal = everything.get(obligation)
    return {
        "claim_id": obligation.subject_id,
        "status": (
            appraisal.status.value
            if appraisal is not None
            else "restated or removed since this route was recorded"
        ),
        "statement": (
            _clip(latest.natural_statement, _MAX_NEIGHBOUR_TEXT)
            if latest is not None
            else ""
        ),
    }


def _transitions(status: ClaimStatus, claim: ClaimVersion) -> list[str]:
    """What could move this claim, phrased from the kernel's own gate sets.

    The tier names are read out of ``KERNEL_TIERS`` / ``DISCHARGING_TIERS`` /
    ``REFUTING_TIERS`` rather than written out here, so this text cannot drift
    away from the rule the assessment actually applies. If those sets ever
    widen, the sentence widens with them.

    Takes the derived status rather than a separate "has open assumptions"
    flag: the status already encodes that distinction (``conditional_kernel``
    versus ``closed_kernel`` is exactly it), and a second parameter saying the
    same thing is a second thing that can disagree with the first.
    """
    lines: list[str] = []
    if status is ClaimStatus.REFUTED:
        lines.append(
            "This claim is refuted. A refutation binds to this exact statement: "
            "restating the claim mints a new version and the refutation does not "
            "follow it, so a revision must be a real change of mathematics, not "
            "a way to get out from under the counterexample."
        )
        return lines
    if status is ClaimStatus.CLOSED_KERNEL:
        lines.append(
            "This claim is a closed kernel: nothing is left on faith. Any edit "
            "to its statement or formalization mints a new version with no "
            "evidence, so do not touch either without intending to re-prove it."
        )
        return lines
    if status in (ClaimStatus.PROPOSED, ClaimStatus.SUPPORTED):
        if not claim.formal_statement.strip():
            lines.append(
                "kernel status is unreachable while this claim has no formal "
                f"statement: {_tiers(KERNEL_TIERS)} evidence would have nothing "
                "to have checked, and is refused rather than counted."
            )
        lines.append(
            f"to conditional_kernel: record {_tiers(KERNEL_TIERS)} evidence that "
            "supports this exact statement, citing a file that can be re-run."
        )
        lines.append(
            "to closed_kernel: the same, with every external assumption in "
            f"\"taken on faith\" discharged by {_tiers(DISCHARGING_TIERS)} "
            "evidence."
        )
    if status is ClaimStatus.CONDITIONAL_KERNEL:
        lines.append(
            "to closed_kernel: discharge every open assumption below with "
            f"{_tiers(DISCHARGING_TIERS)} evidence addressed to that assumption. "
            "Deleting the assumption instead does not work and is refused: an "
            "assumption a claim has ever carried is carried until a revision "
            "records in writing why the proof does not need it."
        )
    lines.append(
        f"to refuted: {_reachable_tiers(REFUTING_TIERS)} evidence may say this "
        "is false, and outranks any amount of support. A referee's opinion may "
        "not."
    )
    return lines


# -- rendering ---------------------------------------------------------------

#: What each citation state means for the next thing a worker does, keyed by
#: the enum's own values so a renamed state loses its sentence loudly instead
#: of rendering an out-of-date one. The distinctions are the ones
#: ``CitationStatus`` insists on: a source nobody has opened, a source that was
#: opened, and a source that was reached without settling what is inside it.
_CITATION_ADVICE = {
    CitationStatus.CONFIRMED.value: (
        "The source has been opened and contains it; re-read the archived "
        "excerpt instead of retrieving it again."
    ),
    CitationStatus.UNCHECKED.value: "Nobody has opened the source yet.",
    CitationStatus.INCONCLUSIVE.value: (
        "The document was reached and the proposition in it is still "
        "unverified: a locator that resolves proves a paper exists, not that "
        "the theorem is in it."
    ),
    CitationStatus.DISPUTED.value: (
        "A checker looked and reports the source does not say this. Checking "
        "again answers nothing; fix the citation or the proof."
    ),
    CitationStatus.UNCITED.value: (
        "No source_id and locator recorded, so there is nothing to retrieve."
    ),
}


def _citation_line(citation: dict[str, Any]) -> str:
    """One line per open import: the state, who settled it, and where to re-read.

    Rendered under the assumption rather than in a section of its own so that
    the status sits against the statement it is about; a separate list would
    have to name every assumption twice to say the same thing.
    """
    status = citation["status"]
    if not status:
        return ""
    parts = [f"citation {status}"]
    if citation["proposition"]:
        parts.append(f" of {citation['proposition']}")
    if citation["checked_by"]:
        names = ", ".join(f"`{name}`" for name in citation["checked_by"])
        if citation["further_checked_by"]:
            names += f", and {citation['further_checked_by']} more"
        parts.append(f", checked by {names}")
    if citation["artifacts"]:
        paths = ", ".join(citation["artifacts"])
        if citation["further_artifacts"]:
            paths += f", and {citation['further_artifacts']} more"
        parts.append(f", read at {paths}")
    advice = _CITATION_ADVICE.get(status, "")
    return f"{''.join(parts)}. {advice}".rstrip()


def _render(payload: dict[str, Any]) -> str:
    digest = content_digest(payload)
    context = payload["context"]
    lines = [
        f"## Mathematical state — claim `{payload['claim_id']}` "
        f"v{payload['version']} ({payload['status']})",
        "",
        f"Projected from `{_STATE_REF}`; fragment digest `{digest[:16]}`. This is "
        "one claim's neighbourhood, not the project: the definitions it is "
        "stated against, what it still takes on faith, the judgements recorded "
        "against this exact statement, and its immediate obligations. Other "
        "claims and retired branches are deliberately absent — do not infer "
        "from their absence that they do not exist.",
        "",
        f"- targeted by this mission's {payload['targeted_by']}",
        f"- statement: {payload['natural_statement'] or '(none recorded)'}",
    ]
    if payload["formal_statement"]:
        lines.append(f"- formalized as: {payload['formal_statement']}")
    else:
        lines.append("- formalized as: (nothing recorded)")
    for issue in payload["issues"]:
        lines.append(f"- ISSUE: {issue}")
    if payload["further_issues"]:
        lines.append(f"- and {payload['further_issues']} further ISSUE(s), not listed.")

    lines.append("")
    if not context["recorded"]:
        lines.append(
            f"### Context `{context['context_id']}` — not in this state\n"
            "The version this claim is stated against is not recorded, so what "
            "its terms mean is written down nowhere. Do not guess the "
            "definitions."
        )
    else:
        freshness = "current" if context["current"] else "SUPERSEDED"
        lines.append(
            f"### Context `{context['context_id']}` v{context['version']} "
            f"({freshness})"
        )
        if not context["current"]:
            lines.append(
                "This claim is stated against definitions the project has since "
                "revised. It is not wrong; it is undecided whether it survives "
                "the new ones."
            )
        lines.append(f"- problem: {context['statement'] or '(none recorded)'}")
        for name, body in context["definitions"]:
            lines.append(f"- `{name}`: {body}")
        if context["further_definitions"]:
            # Not "withheld because the claim does not need them" -- nobody
            # here knows that. Only "there were too many to send".
            lines.append(
                f"- and {context['further_definitions']} further definition(s) "
                f"in this context, not listed here. Read `{_STATE_REF}` rather "
                "than guessing at a term that is defined and not shown."
            )

    lines.append("")
    if payload["open_assumptions"]:
        # The total, not the number of rows that survived the cap. Counting
        # the survivors would put a false number in the one place a reader
        # trusts without reading on: "12 open" when 120 are open is the
        # difference between a kernel nearly closed and one nowhere near it.
        open_total = (
            len(payload["open_assumptions"]) + payload["further_open_assumptions"]
        )
        lines.append(f"### Taken on faith ({open_total} open)")
        for item in payload["open_assumptions"]:
            lines.append(
                f"- `{item['assumption_id']}`: {item['statement']} "
                f"[source: {item['source']}]"
            )
            citation = _citation_line(item["citation"])
            if citation:
                lines.append(f"  - {citation}")
        if payload["further_open_assumptions"]:
            lines.append(
                f"- and {payload['further_open_assumptions']} more open "
                "assumption(s), not listed here. Every one of them has to be "
                "discharged before this claim can be a closed kernel."
            )
    else:
        lines.append("### Taken on faith: nothing open")
    if payload["discharged_assumptions"]:
        tail = (
            f", and {payload['further_discharged_assumptions']} more"
            if payload["further_discharged_assumptions"]
            else ""
        )
        lines.append(
            "- discharged (still listed, still covered): "
            + ", ".join(f"`{item}`" for item in payload["discharged_assumptions"])
            + tail
        )

    lines.append("")
    lines.append("### Evidence bound to this exact statement")
    if payload["evidence"]:
        for record in payload["evidence"]:
            artifact = record["artifact"] or "no file recorded"
            lines.append(
                f"- `{record['evidence_id']}` {record['tier']}/{record['verdict']} "
                f"by `{record['produced_by']}` — {artifact}"
            )
    else:
        lines.append("- none. Nothing has checked this statement as it now stands.")
    if payload["further_evidence"]:
        lines.append(f"- and {payload['further_evidence']} more, not listed here.")
    if payload["stale_evidence"]:
        tail = (
            f", and {payload['further_stale_evidence']} more"
            if payload["further_stale_evidence"]
            else ""
        )
        lines.append(
            "- recorded against an EARLIER version of this claim, and therefore "
            "not evidence for it: "
            + ", ".join(f"`{item}`" for item in payload["stale_evidence"])
            + tail
        )

    lines.append("")
    lines.append("### Immediate proof obligations (one hop)")
    if payload["routes"]:
        for route in payload["routes"]:
            head = f"- route `{route['route_id']}` ({route['status']})"
            if not route["aims_at_current_statement"]:
                head += " — aims at an earlier version of this claim"
            lines.append(head)
            for obligation in route["obligations"]:
                row = f"  - `{obligation['claim_id']}`: {obligation['status']}"
                if obligation["statement"]:
                    row += f" — {obligation['statement']}"
                lines.append(row)
            if route["further_obligations"]:
                lines.append(
                    f"  - and {route['further_obligations']} more obligation(s)."
                )
            for issue in route["issues"]:
                lines.append(f"  - ISSUE: {issue}")
    else:
        lines.append(
            "- no route recorded for this claim. There is no plan on file; "
            "producing one is legitimate work."
        )
    if payload["further_routes"]:
        lines.append(f"- and {payload['further_routes']} more route(s), not listed.")

    if payload["retired_routes"]:
        lines.append("")
        lines.append("### Already retired — do not retry without new reason")
        for route in payload["retired_routes"]:
            lines.append(f"- `{route['route_id']}`: {route['reason']}")
        if payload["further_retired_routes"]:
            lines.append(
                f"- and {payload['further_retired_routes']} more retired route(s)."
            )

    lines.append("")
    lines.append("### What can change this claim's status")
    lines.extend(f"- {line}" for line in payload["transitions"])
    return "\n".join(lines)
