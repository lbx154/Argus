"""Factor specs and registry contract for the quant-factor domain.

Argus does not own a factor library. Real factor libraries live in user code
(price/volume features, fundamentals, alt data) and depend on the user's
data substrate (qlib, pandas, in-house warehouse). This module defines the
**shape** the engineer agent and the L2 reviewer rely on so any user-supplied
library can plug in:

* :class:`FactorSpec` is the declarative record for one factor — what column
  it reads, which way "good" points, what cross-sectional transform applies,
  and whether it should be industry-neutralised. Mirrors the field set from
  finance-argus' ``core.factors.FactorDefinition`` so a user with that style
  of registry can adapt with a one-line wrapper.
* :class:`FactorRegistry` is a ``Protocol``: any object that can list factor
  ids and resolve an id to a :class:`FactorSpec` qualifies. No subclassing.
* :class:`InMemoryFactorRegistry` is the minimal reference implementation
  used by tests and by the reference toy engine; production users wire their
  own.

This module is content-only. It does not compute anything, does not import
numpy/pandas, and does not couple to any data vendor.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FactorSpec:
    """Declarative description of one factor.

    Attributes
    ----------
    factor_id
        Stable identifier the search ledger and report cite. Treat as opaque.
    source
        The column / feature name the engine reads. For DSL-expression factors
        this is the synthetic column the evaluator writes to.
    direction
        +1.0 if higher values are expected to predict higher forward returns;
        -1.0 if the predictive sign is inverted (e.g. low PB is "good").
    transform
        Cross-sectional scalar transform applied before scoring. Conventional
        names: ``"identity"``, ``"log1p"``, ``"rank"``, ``"zscore"``. The
        engine decides what each means; this field just records intent.
    neutralize
        Whether the engine should orthogonalise this factor against industry /
        size / other declared style buckets before scoring. Recorded so the
        report can disclose which factors were neutralised.
    expression
        Optional DSL string. When set, the engine evaluates the expression and
        writes the result to ``source``. ``None`` means the factor is read
        directly from the input frame.
    description
        One-line economic story — what mechanism makes this factor predictive.
        The reviewer reads this against ``review.interpretability``; an empty
        description is a red flag, not a neutral default.
    """

    factor_id: str
    source: str
    direction: float = 1.0
    transform: str = "identity"
    neutralize: bool = True
    expression: str | None = None
    description: str = ""

    @property
    def is_dsl(self) -> bool:
        return self.expression is not None

    def __post_init__(self) -> None:
        if not self.factor_id:
            raise ValueError("FactorSpec.factor_id must be non-empty")
        if not self.source:
            raise ValueError(
                f"FactorSpec({self.factor_id!r}).source must be non-empty"
            )
        if self.direction not in (1.0, -1.0):
            raise ValueError(
                f"FactorSpec({self.factor_id!r}).direction must be +1.0 or -1.0, "
                f"got {self.direction!r}"
            )


@runtime_checkable
class FactorRegistry(Protocol):
    """A pool of factor specs the engineer can search and select from.

    Two operations are enough for the loop: list ids, resolve an id. Any object
    matching this shape works — no inheritance required.
    """

    def factor_ids(self) -> tuple[str, ...]:
        ...

    def get(self, factor_id: str) -> FactorSpec:
        ...


@dataclass
class InMemoryFactorRegistry:
    """The minimal in-memory registry used by tests and the toy engine.

    Construct with an iterable of :class:`FactorSpec`. Ids must be unique.
    Frozen after construction — register a factor by building a new registry,
    not by mutating this one (deliberate: a registry change should be visible
    in source control).
    """

    specs: Mapping[str, FactorSpec] = field(default_factory=dict)

    @classmethod
    def from_iter(cls, specs: Iterable[FactorSpec]) -> "InMemoryFactorRegistry":
        out: dict[str, FactorSpec] = {}
        for spec in specs:
            if spec.factor_id in out:
                raise ValueError(f"duplicate factor_id {spec.factor_id!r}")
            out[spec.factor_id] = spec
        return cls(specs=out)

    def factor_ids(self) -> tuple[str, ...]:
        return tuple(self.specs)

    def get(self, factor_id: str) -> FactorSpec:
        try:
            return self.specs[factor_id]
        except KeyError as exc:
            raise KeyError(
                f"unknown factor_id {factor_id!r}; "
                f"registered: {self.factor_ids()}"
            ) from exc

    def __len__(self) -> int:
        return len(self.specs)

    def __contains__(self, factor_id: object) -> bool:
        return factor_id in self.specs
