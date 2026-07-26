"""The Project's goal contract: what was asked for, and what may change it.

North-Star review §4.1/§4.2/§5.2. The pieces of a goal contract already existed
— ``mission.json`` carries the objective, the acceptance check and non-goals,
campaign identity hashes the objective, and ``VerticalDecision`` carries the
target level and venue — but they were fragments with no single type and no
rule about who may rewrite which part.

The distinction this module adds is between two kinds of clause:

``precise``
    A constraint that can be checked mechanically and that the operator
    actually chose: a target number, a hardware budget, a named baseline, a
    deadline. Changing one changes what "done" means, so the Manager may
    propose it but not commit it alone (operator decision §9.3).

``semantic``
    A description of intent that a Reviewer judges: "the write-up should be
    readable by a systems engineer", "prefer approaches that generalise". The
    Manager clarifies these autonomously — that is the job, and requiring a
    human for every rewording would make the contract read-only in practice.

Why the split is enforced here rather than by asking the model nicely: a Manager
that quietly relaxes a precise constraint it cannot meet produces a project that
reports success against a goal nobody agreed to. That is the one failure mode a
contract exists to prevent, and it is mechanical enough for the harness to own
without judging any research.

Deliberate non-goal: this module does not decide whether a project is finished.
Completion lives in :mod:`argus_skill.core.project_api`, and existing projects
are exempt from contract-based completion (operator decision §9.6). Writing a
contract is additive today — it records what the Manager committed to and
disciplines later edits.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

CLAUSE_PRECISE = "precise"
CLAUSE_SEMANTIC = "semantic"
_CLAUSE_KINDS = frozenset({CLAUSE_PRECISE, CLAUSE_SEMANTIC})

CONTRACT_FILENAME = "goal_contract.json"

_CONFIRMATION_TTL_SECONDS = 7 * 24 * 60 * 60


class ContractError(RuntimeError):
    """A revision was refused. The message says which clause and why."""


@dataclass(frozen=True)
class Clause:
    """One requirement, tagged by how it can be checked."""

    kind: str
    text: str

    @property
    def id(self) -> str:
        """A stable handle for this clause, derived from its content.

        Content-derived rather than sequential so that a confirmation naming a
        clause cannot be silently redirected to a different one by reordering
        the list.
        """
        raw = f"{self.kind}\0{self.text.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class GoalContract:
    """What the operator asked for, as the Manager committed to understand it."""

    objective: str
    clauses: tuple[Clause, ...] = ()
    exclusions: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    revision: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0

    def precise(self) -> tuple[Clause, ...]:
        return tuple(c for c in self.clauses if c.kind == CLAUSE_PRECISE)

    def semantic(self) -> tuple[Clause, ...]:
        return tuple(c for c in self.clauses if c.kind == CLAUSE_SEMANTIC)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "clauses": [c.to_dict() for c in self.clauses],
            "exclusions": list(self.exclusions),
            "ambiguities": list(self.ambiguities),
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ContractConfirmation:
    """The operator agreeing to a specific change of precise constraints.

    Bound to the exact clause ids being added or removed and to the revision it
    was issued against. A blanket "the Manager may edit constraints" flag would
    authorise the next change too, which is the thing being prevented.
    """

    confirmation_id: str
    covers: tuple[str, ...]
    from_revision: int
    nonce: str
    issued_at: float
    expires_at: float
    issued_by: str = "operator"

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "covers": list(self.covers),
            "from_revision": self.from_revision,
            "nonce": self.nonce,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "issued_by": self.issued_by,
        }


@dataclass(frozen=True)
class ContractRevision:
    """One committed change, with what moved and what was kept."""

    revision: int
    at: float
    by: str
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    confirmation_id: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "at": self.at,
            "by": self.by,
            "added": list(self.added),
            "removed": list(self.removed),
            "preserved": list(self.preserved),
            "confirmation_id": self.confirmation_id,
            "note": self.note,
        }


# -- construction ------------------------------------------------------------


def make_clause(kind: str, text: str) -> Clause:
    cleaned = str(text or "").strip()
    normalized = str(kind or "").strip().lower()
    if normalized not in _CLAUSE_KINDS:
        raise ContractError(
            f"clause kind {kind!r} is not one of {sorted(_CLAUSE_KINDS)}"
        )
    if not cleaned:
        raise ContractError("a clause needs text")
    return Clause(kind=normalized, text=cleaned)


def new_contract(
    *,
    objective: str,
    clauses: Iterable[Clause] = (),
    exclusions: Iterable[str] = (),
    ambiguities: Iterable[str] = (),
    now: float | None = None,
) -> GoalContract:
    stamp = float(now if now is not None else time.time())
    cleaned_objective = str(objective or "").strip()
    if not cleaned_objective:
        raise ContractError("a goal contract needs an objective")
    return GoalContract(
        objective=cleaned_objective,
        clauses=_dedup(clauses),
        exclusions=tuple(dict.fromkeys(str(v).strip() for v in exclusions if str(v).strip())),
        ambiguities=tuple(dict.fromkeys(str(v).strip() for v in ambiguities if str(v).strip())),
        revision=1,
        created_at=stamp,
        updated_at=stamp,
    )


def _dedup(clauses: Iterable[Clause]) -> tuple[Clause, ...]:
    seen: dict[str, Clause] = {}
    for clause in clauses:
        seen.setdefault(clause.id, clause)
    return tuple(seen.values())


# -- revision ----------------------------------------------------------------


def issue_confirmation(
    *,
    contract: GoalContract,
    covers: Iterable[str],
    issued_by: str = "operator",
    ttl_seconds: float = _CONFIRMATION_TTL_SECONDS,
    now: float | None = None,
) -> ContractConfirmation:
    """Mint the operator's agreement to change specific precise clauses.

    Bound to the contract revision it was issued against so that a confirmation
    cannot be held and replayed after the contract has moved on.
    """
    stamp = float(now if now is not None else time.time())
    clause_ids = tuple(dict.fromkeys(str(v).strip() for v in covers if str(v).strip()))
    if not clause_ids:
        raise ContractError("a confirmation must name the clauses it covers")
    return ContractConfirmation(
        confirmation_id=f"conf-{secrets.token_hex(8)}",
        covers=clause_ids,
        from_revision=contract.revision,
        nonce=secrets.token_urlsafe(16),
        issued_at=stamp,
        expires_at=stamp + max(0.0, float(ttl_seconds)),
        issued_by=str(issued_by or "operator")[:120],
    )


def revise_contract(
    *,
    current: GoalContract,
    objective: str | None = None,
    clauses: Iterable[Clause] | None = None,
    exclusions: Iterable[str] | None = None,
    ambiguities: Iterable[str] | None = None,
    by: str,
    confirmation: ContractConfirmation | None = None,
    note: str = "",
    now: float | None = None,
) -> tuple[GoalContract, ContractRevision]:
    """Apply a proposed change, or raise :class:`ContractError` saying why not.

    Semantic clauses, exclusions and ambiguities move freely — clarifying intent
    is the Manager's job. Precise clauses and the objective need a confirmation
    covering exactly the ids that change.
    """
    stamp = float(now if now is not None else time.time())
    proposed = _dedup(clauses) if clauses is not None else current.clauses

    before = {c.id: c for c in current.precise()}
    after = {c.id: c for c in proposed if c.kind == CLAUSE_PRECISE}
    added = tuple(sorted(set(after) - set(before)))
    removed = tuple(sorted(set(before) - set(after)))

    new_objective = current.objective if objective is None else str(objective).strip()
    if not new_objective:
        raise ContractError("a goal contract needs an objective")
    objective_changed = new_objective != current.objective

    needs_confirmation = bool(added or removed or objective_changed)
    if needs_confirmation:
        _require_confirmation(
            confirmation=confirmation,
            current=current,
            changed=added + removed + (("objective",) if objective_changed else ()),
            now=stamp,
        )

    preserved = tuple(sorted(set(before) & set(after)))
    revision = ContractRevision(
        revision=current.revision + 1,
        at=stamp,
        by=str(by or "")[:120],
        added=added,
        removed=removed,
        preserved=preserved,
        confirmation_id=confirmation.confirmation_id if confirmation else "",
        note=str(note or "")[:2000],
    )
    updated = GoalContract(
        objective=new_objective,
        clauses=proposed,
        exclusions=(
            current.exclusions
            if exclusions is None
            else tuple(dict.fromkeys(str(v).strip() for v in exclusions if str(v).strip()))
        ),
        ambiguities=(
            current.ambiguities
            if ambiguities is None
            else tuple(dict.fromkeys(str(v).strip() for v in ambiguities if str(v).strip()))
        ),
        revision=revision.revision,
        created_at=current.created_at,
        updated_at=stamp,
    )
    return updated, revision


def _require_confirmation(
    *,
    confirmation: ContractConfirmation | None,
    current: GoalContract,
    changed: tuple[str, ...],
    now: float,
) -> None:
    if confirmation is None:
        raise ContractError(
            "changing a precise constraint needs operator confirmation; "
            f"unconfirmed: {', '.join(changed)}"
        )
    if confirmation.from_revision != current.revision:
        raise ContractError(
            f"confirmation was issued against revision {confirmation.from_revision}, "
            f"the contract is at {current.revision}"
        )
    if confirmation.expires_at and now >= confirmation.expires_at:
        raise ContractError("operator confirmation expired; ask again")
    uncovered = tuple(c for c in changed if c not in confirmation.covers)
    if uncovered:
        raise ContractError(
            "operator confirmation does not cover every precise change; "
            f"uncovered: {', '.join(uncovered)}"
        )


# -- persistence -------------------------------------------------------------


def contract_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / CONTRACT_FILENAME


def load_contract(state_dir: Path | str) -> GoalContract | None:
    """The committed contract, or ``None`` when this project has none.

    ``None`` is a first-class answer: projects that predate contracts are
    exempt, and a caller that cannot tell "no contract" from "empty contract"
    would start enforcing an empty one.
    """
    try:
        payload = json.loads(contract_path(state_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    contract = payload.get("contract") if isinstance(payload, dict) else None
    if not isinstance(contract, dict):
        return None
    try:
        return GoalContract(
            objective=str(contract.get("objective") or ""),
            clauses=tuple(
                Clause(kind=str(row.get("kind") or ""), text=str(row.get("text") or ""))
                for row in contract.get("clauses") or []
                if isinstance(row, dict) and str(row.get("kind") or "") in _CLAUSE_KINDS
            ),
            exclusions=tuple(str(v) for v in contract.get("exclusions") or []),
            ambiguities=tuple(str(v) for v in contract.get("ambiguities") or []),
            revision=int(contract.get("revision") or 1),
            created_at=float(contract.get("created_at") or 0.0),
            updated_at=float(contract.get("updated_at") or 0.0),
        )
    except (TypeError, ValueError):
        return None


def load_history(state_dir: Path | str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(contract_path(state_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    history = payload.get("history") if isinstance(payload, dict) else None
    return [row for row in history or [] if isinstance(row, dict)]


def save_contract(
    state_dir: Path | str,
    *,
    contract: GoalContract,
    revision: ContractRevision | None = None,
) -> Path:
    """Persist the contract, appending ``revision`` to its audit history.

    The history is append-only: a revision that rewrote its own past would make
    the preservation audit unfalsifiable.
    """
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    history = load_history(root)
    if revision is not None:
        history.append(revision.to_dict())
    payload = {"contract": contract.to_dict(), "history": history}
    path = contract_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


__all__ = [
    "CLAUSE_PRECISE",
    "CLAUSE_SEMANTIC",
    "CONTRACT_FILENAME",
    "Clause",
    "ContractConfirmation",
    "ContractError",
    "ContractRevision",
    "GoalContract",
    "contract_path",
    "issue_confirmation",
    "load_contract",
    "load_history",
    "make_clause",
    "new_contract",
    "revise_contract",
    "save_contract",
]
