"""Four-state idea evidence, kept separate from execution failure.

An agent that cannot tell "the experiment did not run" from "the idea is
wrong" kills good ideas for bad reasons. A TileLang import error, a missing
GPU, a permission-denied profiler, an under-powered pilot — none of them say
anything about the hypothesis, but all of them produce a failed run, and a
failed run reads as a negative result unless something forbids that reading.

This module is that something. It encodes one vocabulary:

``execution_status``
    Did the attempt actually run? ``completed | blocked | failed``.

``failure_class``
    If it did not run cleanly, what broke? Environment and tooling failures
    are listed separately from failures that carry real information.

``idea_status``
    What do we now believe about the premise?

    * ``untested`` — the binding premise was never actually exercised.
    * ``inconclusive`` — it ran, but the evidence neither supports nor refutes.
    * ``supported`` — the evidence supports the premise *within its stated scope*.
    * ``refuted`` — valid, faithful evidence contradicts the precise premise.

The invariants below are the point. They make it impossible to record "the
idea is refuted" when what actually happened is "nvcc was not on PATH".

This lives in ``core`` because it is not a kernel concept. The GPU-kernel
vertical proved the model out first, and research needs exactly the same
separation: a negative pilot is not a refuted hypothesis, and an inadequate
implementation is not a scientific finding. Each domain supplies its own
failure vocabulary and its own grounding requirements through
:class:`EvidenceContract`; the invariants are shared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BASE_FAILURE_CLASSES",
    "BASE_NON_IDEA_FAILURES",
    "EXECUTION_STATUSES",
    "IDEA_STATUSES",
    "EvidenceContract",
    "is_placeholder_text",
    "validate_evidence",
]

EXECUTION_STATUSES = frozenset({"completed", "blocked", "failed"})
IDEA_STATUSES = frozenset({"untested", "inconclusive", "supported", "refuted"})

#: Failure modes every domain shares.
BASE_FAILURE_CLASSES = frozenset(
    {
        "none",
        "environment",
        "dependency",
        "toolchain",
        "build_configuration",
        "hardware_access",
        "implementation",
    }
)

#: Failures that mean the attempt never became a valid test of the premise.
#: An idea cannot move past ``inconclusive`` on the back of any of these —
#: that is the whole mis-kill this module exists to prevent.
BASE_NON_IDEA_FAILURES = frozenset(
    {
        "environment",
        "dependency",
        "toolchain",
        "build_configuration",
        "hardware_access",
    }
)

_CONCLUSIVE = frozenset({"supported", "refuted"})
_UNRESOLVED = frozenset({"untested", "inconclusive"})


def is_placeholder_text(value: object) -> bool:
    """Whether *value* is real prose rather than an unfilled template slot."""
    return not (
        isinstance(value, str) and bool(value.strip()) and "REPLACE" not in value
    )


@dataclass(frozen=True)
class EvidenceContract:
    """A domain's evidence rules: shared invariants plus local vocabulary.

    ``grounding_fields``
        What must be recorded before an idea may be called supported or
        refuted. These are the fields that make a claim checkable by someone
        else — which baseline, which candidate, which evaluator.

    ``refuting_failures``
        The only ``failure_class`` values that can carry a refutation. This is
        how a domain says "an under-performing implementation is not a
        disproof". ``None`` disables the restriction.
    """

    domain: str
    failure_classes: frozenset[str]
    non_idea_failures: frozenset[str]
    grounding_fields: tuple[str, ...] = ()
    refuting_failures: frozenset[str] | None = None
    #: Failures that are scheduling or scoping signals, not evidence: they may
    #: never change what we believe about the premise.
    advisory_failures: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        unknown = self.non_idea_failures - self.failure_classes
        if unknown:
            raise ValueError(
                f"{self.domain}: non_idea_failures not in failure_classes: {sorted(unknown)}"
            )
        if self.refuting_failures is not None:
            unknown = self.refuting_failures - self.failure_classes
            if unknown:
                raise ValueError(
                    f"{self.domain}: refuting_failures not in failure_classes: {sorted(unknown)}"
                )


def validate_evidence(
    record: dict[str, Any],
    contract: EvidenceContract,
    *,
    required_text_fields: tuple[str, ...] = ("summary", "evidence"),
) -> list[str]:
    """Return every rule *record* breaks, in a stable order.

    An empty list means the record is a coherent statement about what ran and
    what we may conclude — not that the conclusion is correct.
    """
    errors: list[str] = []

    execution = str(record.get("execution_status") or "")
    failure = str(record.get("failure_class") or "")
    idea = str(record.get("idea_status") or "")

    if execution not in EXECUTION_STATUSES:
        errors.append(f"invalid execution_status: {execution!r}")
    if failure not in contract.failure_classes:
        errors.append(f"invalid failure_class: {failure!r}")
    if idea not in IDEA_STATUSES:
        errors.append(f"invalid idea_status: {idea!r}")
    for key in required_text_fields:
        if is_placeholder_text(record.get(key)):
            errors.append(f"{key} is empty or templated")

    # The central invariant: a run that never validly exercised the premise
    # cannot tell us anything about it.
    if failure in contract.non_idea_failures and idea not in _UNRESOLVED:
        errors.append(
            f"{failure} is an execution/environment failure; idea_status must be "
            "untested or inconclusive"
        )
    if execution != "completed" and idea in _CONCLUSIVE:
        errors.append(f"execution_status={execution} cannot support or refute the idea")
    if failure == "none" and execution != "completed":
        errors.append("failure_class=none requires execution_status=completed")

    # Scheduling signals (prior art, descoping) explain what to do next; they
    # are not observations about the premise.
    if failure in contract.advisory_failures and idea not in _UNRESOLVED:
        errors.append(
            f"{failure} is a scheduling/scope signal, not evidence; it cannot make "
            "the idea supported or refuted — record it as a replan reason"
        )

    if (
        idea == "refuted"
        and contract.refuting_failures is not None
        and failure not in contract.refuting_failures
    ):
        allowed = ", ".join(sorted(contract.refuting_failures))
        errors.append(
            f"idea_status=refuted requires a completed valid result of class: {allowed}"
        )

    if idea in _CONCLUSIVE:
        for key in contract.grounding_fields:
            if is_placeholder_text(record.get(key)):
                errors.append(
                    f"{key} is required before an idea can be {idea}; it is what lets "
                    "someone else check the claim"
                )

    return list(dict.fromkeys(errors))
