"""One JSON file, append-only, with the checks that keep it honest.

JSON rather than SQLite, and that is a considered choice rather than an
expedient one. Every piece of project state in this repository is JSON or JSONL
behind a file lock; the two ``sqlite3`` call sites are peripheral tooling. A
database here would be the first, and it would buy indexes for a workload of a
few hundred claims — while costing the property that matters most early on,
which is that a human and a diff can read what the system believes. When a
query is demonstrably the bottleneck, that is the moment to change this, and
the schema above does not depend on the answer.

Append-only, in the one sense that has teeth: a version already written is
never replaced. ``add_claim`` refuses to overwrite ``(claim_id, version)``, so
two writers racing on the same version collide loudly instead of one of them
losing silently — the optimistic-concurrency check, expressed as a schema
constraint rather than as a transaction log. ``revise_claim`` is the only way
to change a claim, and it mints the next version.

Reads use no file lock, and writes take none here either. That is not an
omission: a lock taken inside ``save_state`` would cover the write and not the
read that decided what to write, which is the half that actually races. The
adapter that owns the read-modify-write owns the lock — see ``locked_state`` in
the math vertical, which holds one across load, mutate, and save. Anything that
mutates this file outside such a wrapper is a bug in the caller, and the
``(claim_id, version)`` collision above is the backstop that makes it loud.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assessment import (
    CitationAssessment,
    ClaimAssessment,
    RouteAssessment,
    assess_citation,
    assess_claim,
    assess_routes,
    route_cycles,
)
from .models import (
    ARTIFACT_REQUIRED_TIERS,
    ClaimVersion,
    ContextVersion,
    EvidenceRecord,
    ExternalAssumption,
    ProofRoute,
    RetiredAssumption,
    SubjectRef,
)

__all__ = [
    "SCHEMA_VERSION",
    "STATE_RELPATH",
    "MathState",
    "MathStateError",
    "StateIssue",
    "load_state",
    "save_state",
    "state_path",
]

#: Where a project keeps it. Sits beside ``research/PROOF_GRAPH.json`` and
#: ``research/LITERATURE_GROUNDING.json`` so the whole research state of a
#: project is one directory.
STATE_RELPATH = ("research", "MATH_STATE.json")

#: Versions the *file*, never the digests inside it. Bumping this must stay
#: able to leave every recorded proof valid; see ``models.content_digest``.
SCHEMA_VERSION = 1


class MathStateError(Exception):
    """A write that would destroy history, or a file that is not this schema."""


@dataclass(frozen=True)
class StateIssue:
    """One structural defect, in the shape ``literature_ledger`` and
    ``lean_evidence`` already use, so a later gate can render all three the
    same way."""

    code: str
    path: str
    message: str

    def rendered(self) -> str:
        return f"{self.path}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass
class MathState:
    """Every version of everything, plus the queries worth having.

    Not indexed. Linear scans over a few hundred records cost nothing, and the
    absence of an index is what keeps this file short enough to read in one
    sitting.
    """

    contexts: list[ContextVersion] = field(default_factory=list)
    claims: list[ClaimVersion] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    routes: list[ProofRoute] = field(default_factory=list)

    # -- appending ---------------------------------------------------------

    def add_context(self, context: ContextVersion) -> ContextVersion:
        if self.context_version(context.context_id, context.version) is not None:
            raise MathStateError(
                f"context {context.context_id!r} version {context.version} already "
                "exists; revise it instead of rewriting history"
            )
        self.contexts.append(context)
        return context

    def add_claim(self, claim: ClaimVersion) -> ClaimVersion:
        if self.claim_version(claim.claim_id, claim.version) is not None:
            raise MathStateError(
                f"claim {claim.claim_id!r} version {claim.version} already exists; "
                "revise it instead of rewriting history"
            )
        self.claims.append(claim)
        return claim

    def add_evidence(self, record: EvidenceRecord) -> EvidenceRecord:
        if any(item.evidence_id == record.evidence_id for item in self.evidence):
            raise MathStateError(
                f"evidence {record.evidence_id!r} already exists; a verifier "
                "answering twice must say so under two ids"
            )
        self.evidence.append(record)
        return record

    def retire_superseded_evidence(
        self, record: EvidenceRecord
    ) -> tuple[EvidenceRecord, ...]:
        """Drop the answers this one replaces: same checker, same statement.

        Call this before adding ``record``. It removes every record that agrees
        with it on subject, tier, and producer while naming a different
        ``artifact``, and returns what it removed so the caller can say whose
        reading just stopped being the one in force.

        This is not a hole in the append-only rule, which has teeth about
        *versions*: a version already written is never replaced, and none is
        replaced here. Evidence is a different kind of row. It says what one
        checker currently answers about one statement, and one checker has one
        current answer — so two rows differing only in which document the answer
        was reached from do not describe two supports, they describe one support
        and a stale pointer to a document the project has moved past.

        The case that forces this is a rewritten statement fidelity note. The
        Lean file is untouched, so the compiler says exactly what it said before
        and the record's identifying fields are unchanged; what changed is the
        reading of the theorem the answer was paired with. Keeping both would
        leave the ledger unable to say which reading the claim means, with no
        way to ever resolve it — restating the claim mints no new version when
        the statement itself did not change, so a defect reported about the pair
        would be a defect nobody could clear.

        Nothing is lost. The retired record's artifact is a file on disk and
        stays there; what is dropped is the assertion that it is still what the
        claim stands on.
        """
        retired = tuple(
            item
            for item in self.evidence
            if item.evidence_id != record.evidence_id
            and item.subject == record.subject
            and item.tier == record.tier
            and item.produced_by == record.produced_by
            and item.artifact != record.artifact
        )
        if retired:
            dropped = {item.evidence_id for item in retired}
            self.evidence[:] = [
                item for item in self.evidence if item.evidence_id not in dropped
            ]
        return retired

    def add_route(self, route: ProofRoute) -> ProofRoute:
        """Record a decomposition, unless it is one the project can chase forever.

        The cycle check lives here as well as in ``validate`` for the same
        reason the assumption gate does: refusing at write time is what keeps a
        defect from being written by the API at all, and reporting at read time
        is what catches the copy that arrived by way of a text editor. Only a
        cycle *through this route* is refused — a state that was already
        circular is a defect ``validate`` will report, and blocking unrelated
        writes until somebody repairs it would help nobody.

        A route that records why it was abandoned is exempt, because it is not
        a plan: "we tried deriving A from B and B needs A" is a result worth
        keeping, and the only way to keep it is to be allowed to write it down.
        """
        if any(item.route_id == route.route_id for item in self.routes):
            raise MathStateError(f"route {route.route_id!r} already exists")
        closing = next(
            (
                group
                for group in route_cycles([*self.routes, route])
                if route.route_id in group
            ),
            None,
        )
        if closing is not None:
            raise MathStateError(
                f"route {route.route_id!r} would close a cycle with "
                f"{', '.join(closing)}: a decomposition that eventually asks for "
                "the claim it is decomposing proves nothing, and it is the one "
                "shape an agent can expand forever without noticing. Set "
                "retired_because if the circular attempt is worth recording."
            )
        self.routes.append(route)
        return route

    # -- revising ----------------------------------------------------------

    def revise_context(
        self,
        context_id: str,
        *,
        statement: str | None = None,
        definitions: Mapping[str, str] | None = None,
    ) -> ContextVersion:
        """Mint the next version of a context. The old one stays readable.

        Every claim bound to the previous definitions keeps pointing at them,
        so ``validate`` reports exactly which claims are now standing on a
        problem statement the project has moved past. Silently re-pointing them
        would be re-asserting each claim against a definition nobody re-read.
        """
        current = self.latest_context(context_id)
        if current is None:
            raise MathStateError(f"no context {context_id!r} to revise")
        return self.add_context(
            ContextVersion(
                context_id=context_id,
                version=current.version + 1,
                statement=current.statement if statement is None else statement,
                definitions=(
                    dict(current.definitions)
                    if definitions is None
                    else dict(definitions)
                ),
            )
        )

    def revise_claim(
        self,
        claim_id: str,
        *,
        context: SubjectRef | None = None,
        natural_statement: str | None = None,
        formal_statement: str | None = None,
        external_assumptions: Sequence[ExternalAssumption] | None = None,
        retire_assumptions: Mapping[str, str] | None = None,
    ) -> ClaimVersion:
        """The only way to change a claim.

        Whether the new version keeps the old version's evidence is not decided
        here and cannot be influenced from here: it falls out of whether the
        digest moved. Restating the theorem loses the certificate; adding an
        assumption, or discharging one, keeps it.

        Removing one is the case that needs a gate. Assumptions are outside the
        digest, so dropping a dependency leaves a finished kernel verdict
        binding and promotes the claim to ``closed_kernel`` for free — the
        cheapest possible route to the most expensive status in the package.
        This refuses to mint such a version: every assumption the claim is
        standing on and the new version does not list must be named in
        ``retire_assumptions`` with a reason. The reason is not checked, and
        cannot be; what is enforced is that somebody had to write one down.

        The set it demands reasons for is everything *carried*, not just what
        the previous version listed, so a claim whose ledger was damaged by a
        hand-edited file gets repaired the first time it is revised rather than
        inheriting the damage forever.
        """
        current = self.latest_claim(claim_id)
        if current is None:
            raise MathStateError(f"no claim {claim_id!r} to revise")

        listed = tuple(
            current.external_assumptions
            if external_assumptions is None
            else external_assumptions
        )
        listed_ids = {item.assumption_id for item in listed}
        carried, _ = self._assumption_ledger(claim_id)
        reasons = dict(retire_assumptions or {})

        retirements: list[RetiredAssumption] = []
        for assumption_id, reason in sorted(reasons.items()):
            held = carried.get(assumption_id)
            if held is None:
                raise MathStateError(
                    f"claim {claim_id!r} is not standing on assumption "
                    f"{assumption_id!r}, so there is nothing to retire"
                )
            if assumption_id in listed_ids:
                raise MathStateError(
                    f"assumption {assumption_id!r} is still listed on claim "
                    f"{claim_id!r}; retiring it and keeping it are different things"
                )
            if not reason.strip():
                raise MathStateError(
                    f"retiring assumption {assumption_id!r} needs a reason, because "
                    "the only honest one is that the proof turns out not to need it "
                    "and that is a mathematical claim"
                )
            retirements.append(
                RetiredAssumption(assumption_id, held.content_hash, reason)
            )

        unexplained = sorted(set(carried) - listed_ids - set(reasons))
        if unexplained:
            raise MathStateError(
                f"claim {claim_id!r} would stop standing on "
                f"{', '.join(unexplained)} with nothing recorded about why. "
                "Deleting an assumption asserts that the proof does not need it, "
                "and that does not become true by going unsaid; a discharged "
                "assumption stays listed and keeps its evidence. Pass "
                "retire_assumptions={id: why} if the proof really is independent "
                "of it."
            )

        return self.add_claim(
            ClaimVersion(
                claim_id=claim_id,
                version=current.version + 1,
                context=current.context if context is None else context,
                natural_statement=(
                    current.natural_statement
                    if natural_statement is None
                    else natural_statement
                ),
                formal_statement=(
                    current.formal_statement
                    if formal_statement is None
                    else formal_statement
                ),
                external_assumptions=listed,
                retired_assumptions=tuple(retirements),
            )
        )

    # -- reading -----------------------------------------------------------

    def context_version(self, context_id: str, version: int) -> ContextVersion | None:
        for item in self.contexts:
            if item.context_id == context_id and item.version == version:
                return item
        return None

    def claim_version(self, claim_id: str, version: int) -> ClaimVersion | None:
        for item in self.claims:
            if item.claim_id == claim_id and item.version == version:
                return item
        return None

    def latest_context(self, context_id: str) -> ContextVersion | None:
        found = [item for item in self.contexts if item.context_id == context_id]
        return max(found, key=lambda item: item.version) if found else None

    def latest_claim(self, claim_id: str) -> ClaimVersion | None:
        found = [item for item in self.claims if item.claim_id == claim_id]
        return max(found, key=lambda item: item.version) if found else None

    def claim_history(self, claim_id: str) -> tuple[ClaimVersion, ...]:
        """Every version, oldest first. Nothing is ever dropped from it."""
        return tuple(
            sorted(
                (item for item in self.claims if item.claim_id == claim_id),
                key=lambda item: item.version,
            )
        )

    def current_claims(self) -> tuple[ClaimVersion, ...]:
        ids = sorted({item.claim_id for item in self.claims})
        latest = (self.latest_claim(claim_id) for claim_id in ids)
        return tuple(item for item in latest if item is not None)

    # -- the questions this package exists to answer -----------------------

    def _assumption_ledger(
        self, claim_id: str
    ) -> tuple[dict[str, ExternalAssumption], list[RetiredAssumption]]:
        """What a claim still stands on, and which retirements retired nothing.

        Walks the entire history rather than comparing the last two versions,
        because two revisions would otherwise launder a deletion: drop the
        assumption in one, and by the next version the predecessor no longer
        has it to be missing from. The rule this implements is one sentence —
        *an assumption a claim has ever carried is carried still, until some
        version says in writing why it is not.*

        Keyed by id, not by digest, so that correcting an assumption's wording
        or its citation reads as a correction rather than as a deletion plus an
        addition. Changing what a dependency says is already handled elsewhere,
        and harshly: it changes the digest, so it un-discharges itself.
        """
        carried: dict[str, ExternalAssumption] = {}
        unmatched: list[RetiredAssumption] = []
        for version in self.claim_history(claim_id):
            for retirement in version.retired_assumptions:
                held = carried.get(retirement.assumption_id)
                if (
                    held is not None
                    and held.content_hash == retirement.content_hash
                    and retirement.reason.strip()
                ):
                    del carried[retirement.assumption_id]
                else:
                    unmatched.append(retirement)
            for assumption in version.external_assumptions:
                carried[assumption.assumption_id] = assumption
        return carried, unmatched

    def effective_assumptions(self, claim_id: str) -> tuple[ExternalAssumption, ...]:
        """Everything the current version stands on, whether it lists it or not.

        Differs from ``latest_claim(...).external_assumptions`` exactly when a
        revision dropped a dependency without explaining itself, which is the
        case this method exists for.
        """
        if self.latest_claim(claim_id) is None:
            raise MathStateError(f"no claim {claim_id!r}")
        carried, _ = self._assumption_ledger(claim_id)
        return tuple(
            sorted(carried.values(), key=lambda item: item.assumption_id)
        )

    def _inherited(self, claim: ClaimVersion) -> tuple[ExternalAssumption, ...]:
        listed = {item.assumption_id for item in claim.external_assumptions}
        carried, _ = self._assumption_ledger(claim.claim_id)
        return tuple(
            sorted(
                (
                    assumption
                    for assumption_id, assumption in carried.items()
                    if assumption_id not in listed
                ),
                key=lambda item: item.assumption_id,
            )
        )

    def _assess_claim(self, claim: ClaimVersion) -> ClaimAssessment:
        """One claim from the evidence alone, before any route is attached."""
        return assess_claim(
            claim, self.evidence, inherited_assumptions=self._inherited(claim)
        )

    def _claim_assessments(self) -> dict[SubjectRef, ClaimAssessment]:
        """The first of two passes: every current claim, without its routes.

        A route's obligations are claims, so routes cannot be assessed until
        this map exists; and attaching routes to a claim never changes what
        this pass computed, which is what makes two passes correct rather than
        merely convenient.
        """
        return {claim.ref(): self._assess_claim(claim) for claim in self.current_claims()}

    def assess(self, claim_id: str) -> ClaimAssessment:
        claim = self.latest_claim(claim_id)
        if claim is None:
            raise MathStateError(f"no claim {claim_id!r}")
        return self.assess_all()[claim.ref()]

    def assess_all(self) -> dict[SubjectRef, ClaimAssessment]:
        """Keyed by reference, so a caller cannot confuse two statements that
        happen to share an id."""
        base = self._claim_assessments()
        grouped: dict[SubjectRef, list[RouteAssessment]] = {}
        for route, assessment in zip(
            self.routes, assess_routes(self.routes, base), strict=True
        ):
            grouped.setdefault(route.goal, []).append(assessment)
        return {
            ref: assessment.with_routes(grouped.get(ref, ()))
            for ref, assessment in base.items()
        }

    def assess_routes(self) -> tuple[RouteAssessment, ...]:
        # The free function of the same name, not this method: routes are
        # assessed as a set because whether one is circular is a fact about all
        # of them.
        return assess_routes(self.routes, self._claim_assessments())

    def undischarged_assumptions(
        self, claim_id: str
    ) -> tuple[ExternalAssumption, ...]:
        """What this claim is still taking on faith.

        The query the whole ``conditional_kernel`` distinction exists to make
        answerable without reading anybody's prose. Drawn from
        ``effective_assumptions``, so a dependency that was quietly deleted is
        still reported: it is precisely the one nobody wants to see.
        """
        claim = self.latest_claim(claim_id)
        if claim is None:
            raise MathStateError(f"no claim {claim_id!r}")
        # Deliberately not ``assess``: routes cannot change which results a
        # claim is standing on, and ``open_assumptions`` asks this once per
        # claim, so answering it through the whole-project route pass would
        # make a cheap project-wide question quadratic in the claims.
        open_ids = set(self._assess_claim(claim).undischarged)
        return tuple(
            item
            for item in self.effective_assumptions(claim_id)
            if item.assumption_id in open_ids
        )

    def open_assumptions(self) -> dict[str, tuple[ExternalAssumption, ...]]:
        """Every undischarged external assumption in the project, by claim.

        The project-level version of the same question: what does this body of
        work rest on that nobody here has checked?
        """
        result: dict[str, tuple[ExternalAssumption, ...]] = {}
        for claim in self.current_claims():
            outstanding = self.undischarged_assumptions(claim.claim_id)
            if outstanding:
                result[claim.claim_id] = outstanding
        return result

    def citations(self, claim_id: str) -> tuple[CitationAssessment, ...]:
        """Where every result this claim imports was looked up, and by whom.

        Drawn from ``effective_assumptions`` rather than from the latest
        version's own list, for the same reason ``undischarged_assumptions`` is:
        a dependency dropped without a recorded reason still counts, and it is
        exactly the one whose citation nobody wants examined.

        Unfiltered by status on purpose. A caller that wants only the open ones
        can say so, and a method that answered "the unchecked citations" would
        make ``confirmed`` and ``uncited`` indistinguishable from absent — which
        is the distinction ``CitationStatus`` exists to keep.
        """
        return tuple(
            assess_citation(item, self.evidence)
            for item in self.effective_assumptions(claim_id)
        )

    def open_citations(self) -> dict[str, tuple[CitationAssessment, ...]]:
        """Every citation in the project that still owes a retrieval, by claim.

        The question a delivery gate asks. It is asked project-wide rather than
        per claim because "has everything been checked" is not answerable one
        claim at a time, and because the answer has to be empty for the whole
        project before anything ships.
        """
        result: dict[str, tuple[CitationAssessment, ...]] = {}
        for claim in self.current_claims():
            outstanding = tuple(
                item for item in self.citations(claim.claim_id) if not item.is_settled
            )
            if outstanding:
                result[claim.claim_id] = outstanding
        return result

    # -- validation --------------------------------------------------------

    def validate(self) -> tuple[StateIssue, ...]:
        """Structural defects that make a record unable to mean anything.

        Deliberately not a mathematical review. Everything checked here is a
        fact about references and required fields, so the answer is the same on
        every machine and no model is consulted.

        Superseded claim versions are exempt from everything that is a
        judgement about the present. History is append-only, so a defect in a
        record that has already been replaced cannot be fixed, and reporting it
        forever would bury the rows that can be.
        """
        issues: list[StateIssue] = []
        context_refs = {item.ref() for item in self.contexts}
        current_context_refs = {
            item.ref()
            for item in (
                self.latest_context(context_id)
                for context_id in {entry.context_id for entry in self.contexts}
            )
            if item is not None
        }
        current_claims = {_claim_key(item) for item in self.current_claims()}

        for index, claim in enumerate(sorted(self.claims, key=_claim_key)):
            path = f"$.claims[{index}]"
            if not claim.claim_id:
                issues.append(StateIssue("claim_unidentified", path, "claim has no id"))
            if not claim.natural_statement.strip():
                issues.append(
                    StateIssue(
                        "claim_empty",
                        f"{path}.natural_statement",
                        "a claim with no statement asserts nothing",
                    )
                )
            if claim.context not in context_refs:
                issues.append(
                    StateIssue(
                        "claim_context_stale",
                        f"{path}.context",
                        f"claim {claim.claim_id!r} is stated against a context "
                        "version that is not in this state, so what its terms mean "
                        "is not recorded anywhere",
                    )
                )
            elif (
                claim.context not in current_context_refs
                and _claim_key(claim) in current_claims
            ):
                # Not an error: the claim is a correct statement about the
                # problem as it stood. It is a decision nobody has made yet —
                # does this survive the new definitions? — and it has to be
                # visible or the silence answers "yes".
                #
                # The message names the one remedy that exists. An earlier
                # version also offered "or record why it still holds", which
                # nothing implements: there is no way to mark a claim as
                # unaffected by a revision. That was harmless while this issue
                # was advisory and stopped being harmless when a completion
                # gate began blocking on it, because a blocked agent will try
                # what the message says.
                issues.append(
                    StateIssue(
                        "claim_context_outdated",
                        f"{path}.context",
                        f"claim {claim.claim_id!r} is stated against a superseded "
                        "context version; read it under the new definitions and "
                        "restate it with `revise_claim(context=...)`, which mints "
                        "a new digest — so evidence recorded about the old "
                        "statement stops binding and whatever verifier certified "
                        "it has to run again. A revision that does not touch this "
                        "claim still costs it that re-check; nothing here can "
                        "record it as unaffected",
                    )
                )
            if _claim_key(claim) in current_claims:
                issues.extend(_assumption_issues(claim, path))
                issues.extend(self._dropped_assumption_issues(claim, path))

        issues.extend(self._assumption_collisions())
        issues.extend(self._evidence_issues())
        issues.extend(self._route_issues())
        return tuple(issues)

    def _assumption_collisions(self) -> list[StateIssue]:
        """One id must name one statement, or nobody can read the ledger.

        Discharge is by digest, so a collision cannot cause a false discharge.
        What it does cause is two different results answering to one name in
        every report and every conversation about the project.
        """
        seen: dict[str, str] = {}
        issues: list[StateIssue] = []
        for claim in sorted(self.current_claims(), key=_claim_key):
            for assumption in claim.external_assumptions:
                digest = assumption.content_hash
                previous = seen.setdefault(assumption.assumption_id, digest)
                if previous != digest:
                    issues.append(
                        StateIssue(
                            "assumption_id_collision",
                            f"$.claims[{claim.claim_id}].{assumption.assumption_id}",
                            "the same assumption id names two different statements, "
                            "so one project cannot say which result it is standing "
                            "on",
                        )
                    )
        return issues

    def _dropped_assumption_issues(
        self, claim: ClaimVersion, path: str
    ) -> list[StateIssue]:
        """Catch a deletion that never went through ``revise_claim``.

        The API refuses to mint a version that abandons a dependency in
        silence, but this file is JSON and a text editor is not bound by the
        API. Since deleting an assumption is the cheap route to
        ``closed_kernel``, the check has to exist where a hand-written state
        file also passes through it. ``assess`` already refuses to award the
        status; this is what tells a reader why.
        """
        carried, unmatched = self._assumption_ledger(claim.claim_id)
        listed = {item.assumption_id for item in claim.external_assumptions}
        issues: list[StateIssue] = []
        for assumption_id in sorted(set(carried) - listed):
            issues.append(
                StateIssue(
                    "assumption_dropped_silently",
                    f"{path}.external_assumptions",
                    f"claim {claim.claim_id!r} stopped listing assumption "
                    f"{assumption_id!r} without any version recording why; it is "
                    "still counted as undischarged, because a proof does not stop "
                    "needing a result by having the mention of it deleted",
                )
            )
        for retirement in unmatched:
            issues.append(
                StateIssue(
                    "assumption_retirement_unmatched",
                    f"{path}.retired_assumptions",
                    f"a retirement of {retirement.assumption_id!r} names a "
                    "statement this claim was not standing on, or gives no reason, "
                    "so it retires nothing",
                )
            )
        return issues

    def _evidence_issues(self) -> list[StateIssue]:
        known = {item.ref() for item in self.contexts}
        known |= {item.ref() for item in self.claims}
        known |= {
            assumption.ref()
            for claim in self.claims
            for assumption in claim.external_assumptions
        }
        known_ids = {ref.subject_id for ref in known}

        issues: list[StateIssue] = []
        for index, record in enumerate(
            sorted(self.evidence, key=lambda item: item.evidence_id)
        ):
            path = f"$.evidence[{index}]"
            if not record.evidence_id:
                issues.append(
                    StateIssue("evidence_unidentified", path, "evidence has no id")
                )
            if not record.produced_by.strip():
                issues.append(
                    StateIssue(
                        "evidence_unattributed",
                        f"{path}.produced_by",
                        "evidence with no producer cannot be weighed against other "
                        "evidence: independence is a fact about who answered",
                    )
                )
            if record.tier in ARTIFACT_REQUIRED_TIERS and not record.artifact.strip():
                issues.append(
                    StateIssue(
                        "evidence_unauditable",
                        f"{path}.artifact",
                        f"{record.tier.value} evidence names no artifact, so its "
                        "verdict cannot be re-inspected or re-run; a certificate "
                        "nobody can check is not evidence",
                    )
                )
            if record.subject not in known:
                # Distinguish "about something that has been restated" from
                # "about something that was never here": the first is the
                # ordinary, expected result of revising a claim.
                if record.subject.subject_id in known_ids:
                    issues.append(
                        StateIssue(
                            "evidence_stale",
                            f"{path}.subject",
                            f"evidence {record.evidence_id!r} is about "
                            f"{record.subject.subject_id!r} as it was stated then, "
                            "which is not any version recorded here",
                        )
                    )
                else:
                    issues.append(
                        StateIssue(
                            "evidence_orphaned",
                            f"{path}.subject",
                            f"evidence {record.evidence_id!r} names "
                            f"{record.subject.subject_id!r}, which does not exist "
                            "in this state at all",
                        )
                    )
        return issues

    def _route_issues(self) -> list[StateIssue]:
        current = {claim.ref() for claim in self.current_claims()}
        issues: list[StateIssue] = []
        for index, route in enumerate(
            sorted(self.routes, key=lambda item: item.route_id)
        ):
            path = f"$.routes[{index}]"
            if route.goal not in current:
                issues.append(
                    StateIssue(
                        "route_goal_stale",
                        f"{path}.goal",
                        f"route {route.route_id!r} aims at a statement that is not "
                        "the current version of any claim",
                    )
                )
            if route.goal in route.obligations and not route.retired_because.strip():
                issues.append(
                    StateIssue(
                        "route_circular",
                        f"{path}.obligations",
                        f"route {route.route_id!r} lists its own goal as an "
                        "obligation; a proof that rests on itself proves nothing",
                    )
                )

        for group in route_cycles(self.routes):
            if len(group) == 1:
                # A route that names its own goal is a cycle too, and it is
                # reported just above under a code whose message can be sharper.
                # Saying it twice would teach a reader to skim this list.
                continue
            issues.append(
                StateIssue(
                    "route_cycle",
                    f"$.routes[{group[0]}].obligations",
                    "routes " + ", ".join(group) + " depend on each other in a "
                    "circle: each waits on a claim another of them is meant to "
                    "prove, so none of them can be started. The file was not "
                    "written through add_route, which refuses this",
                )
            )
        return issues

    # -- serialization -----------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "contexts": [item.as_dict() for item in self.contexts],
            "claims": [item.as_dict() for item in self.claims],
            "evidence": [item.as_dict() for item in self.evidence],
            "routes": [item.as_dict() for item in self.routes],
        }

    @classmethod
    def from_dict(cls, payload: object) -> MathState:
        if not isinstance(payload, Mapping):
            raise MathStateError("math state must be a JSON object")
        recorded = payload.get("schema_version")
        if recorded is not None and int(recorded) > SCHEMA_VERSION:
            raise MathStateError(
                f"math state was written by schema version {recorded}, which this "
                f"build (version {SCHEMA_VERSION}) cannot read"
            )
        return cls(
            contexts=list(_parse("contexts", payload, ContextVersion.from_dict)),
            claims=list(_parse("claims", payload, ClaimVersion.from_dict)),
            evidence=list(_parse("evidence", payload, EvidenceRecord.from_dict)),
            routes=list(_parse("routes", payload, ProofRoute.from_dict)),
        )


def _claim_key(claim: ClaimVersion) -> tuple[str, int]:
    return (claim.claim_id, claim.version)


def _assumption_issues(claim: ClaimVersion, path: str) -> list[StateIssue]:
    issues: list[StateIssue] = []
    for position, assumption in enumerate(claim.external_assumptions):
        where = f"{path}.external_assumptions[{position}]"
        if not assumption.assumption_id:
            issues.append(
                StateIssue("assumption_unidentified", where, "assumption has no id")
            )
        if not assumption.statement.strip():
            issues.append(
                StateIssue(
                    "assumption_empty",
                    f"{where}.statement",
                    "an assumption with no statement cannot be discharged, because "
                    "there is nothing to prove",
                )
            )
        if not assumption.source.strip():
            issues.append(
                StateIssue(
                    "assumption_unsourced",
                    f"{where}.source",
                    f"assumption {assumption.assumption_id!r} names no source, so "
                    "nobody can check what was assumed; that is a gap in the proof, "
                    "not a citation",
                )
            )
        issues.extend(_citation_issues(assumption, where))
    return issues


def _citation_issues(assumption: ExternalAssumption, where: str) -> list[StateIssue]:
    """Half a machine-readable citation, which reads as checkable and is not.

    Neither field is required — a private communication has no DOI and no
    theorem number, and the prose ``source`` covers that case. What is refused
    is one without the other. A ``source_id`` alone cites a document, and no
    proof leans on a document; whoever comes to check it has to guess which
    result inside it was meant, which is the guess the locator exists to remove.
    A ``locator`` alone names ``Theorem 3.2`` of nothing.

    Reported rather than corrected, and reported as a defect rather than
    tolerated as a partial answer, because the failure is silent in the other
    direction: ``assess_citation`` reads a half citation as ``uncited``, so a
    dependency the agent believed it had made checkable would sit in the state
    looking like one that could not be checked at all, and no checker would ever
    be sent to it.
    """
    source_id = assumption.source_id.strip()
    locator = assumption.locator.strip()
    if bool(source_id) == bool(locator):
        return []
    missing, given = (
        ("locator", f"source_id {source_id!r}")
        if source_id
        else ("source_id", f"locator {locator!r}")
    )
    return [
        StateIssue(
            "citation_incomplete",
            f"{where}.{missing}",
            f"assumption {assumption.assumption_id!r} gives {given} and no "
            f"{missing}; a citation names a proposition, not a document and not "
            "a theorem number floating free, and half of one cannot be looked "
            "up. Give both or neither — with neither, the prose source stands "
            "and the citation is reported as `uncited` rather than unchecked",
        )
    ]


def _parse(key: str, payload: Mapping[str, Any], build: Any) -> Iterable[Any]:
    rows = payload.get(key) or []
    if not isinstance(rows, list):
        raise MathStateError(f"{key} must be a list")
    for index, row in enumerate(rows):
        try:
            yield build(row)
        except (ValueError, TypeError) as exc:
            raise MathStateError(f"{key}[{index}] is not usable: {exc}") from exc


# -- persistence -------------------------------------------------------------

def state_path(project_root: Path | str) -> Path:
    return Path(str(project_root)).joinpath(*STATE_RELPATH)


def load_state(project_root: Path | str) -> MathState:
    """Read the project's math state, or an empty one when there is none.

    A missing file is not an error — most projects have no mathematical state
    and should pay nothing for that. A file that exists and cannot be read is
    an error, loudly: returning an empty state there would silently discard
    every proof the project had recorded.
    """
    path = state_path(project_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return MathState()
    except OSError as exc:
        raise MathStateError(f"{path} cannot be read: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MathStateError(f"{path} is not valid JSON: {exc}") from exc
    return MathState.from_dict(payload)


def save_state(project_root: Path | str, state: MathState) -> Path:
    """Write atomically, because a truncated state file is a lost project.

    Same ``mkstemp`` + ``os.replace`` shape as ``literature_ledger``: a crash
    mid-write leaves the previous state intact rather than a half-written file
    that ``load_state`` would refuse to read.
    """
    path = state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state.as_dict(), ensure_ascii=False, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return path
