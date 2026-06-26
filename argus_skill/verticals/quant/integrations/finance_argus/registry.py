"""Adapt finance-argus ``FactorDefinition`` records into quant-factor ``FactorSpec``.

The two records are deliberately near-identical (the quant-factor ``FactorSpec``
docstring even says it "mirrors the field set from finance-argus'
``core.factors.FactorDefinition``"), so the conversion is a one-line field map
plus a description lookup. The only semantic gap is ``description``: ``FactorSpec``
treats an empty description as a red flag, while ``FactorDefinition`` has none —
so we pull the economic story from ``FACTOR_DESCRIPTIONS``.

``finance_argus`` is imported lazily *inside* the functions so that importing
this module never drags in pandas/tushare/qlib. Importing ``finance_argus.core
.factors`` itself is cheap (a pure dataclass module), but we keep the lazy
boundary uniform across the integration.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ...factors import FactorSpec, InMemoryFactorRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from finance_argus.core.factors import FactorDefinition


def factor_spec_from_definition(
    definition: "FactorDefinition", *, description: str = ""
) -> FactorSpec:
    """Convert one finance-argus ``FactorDefinition`` to a ``FactorSpec``.

    Fields map 1:1 except ``name`` -> ``factor_id``. ``description`` should be
    the factor's economic story (from ``FACTOR_DESCRIPTIONS``); a fallback note
    is supplied so ``FactorSpec`` never carries an empty (red-flag) description.
    """
    return FactorSpec(
        factor_id=definition.name,
        source=definition.source,
        direction=float(definition.direction),
        transform=definition.transform,
        neutralize=bool(definition.neutralize),
        expression=definition.expression,
        description=description or f"finance-argus factor {definition.name!r}",
    )


def build_finance_argus_registry(
    names: Sequence[str] | None = None,
    *,
    pool: Any | None = None,
) -> InMemoryFactorRegistry:
    """Build a quant-factor ``FactorRegistry`` from finance-argus factors.

    Parameters
    ----------
    names
        Restrict to these factor names (in order). ``None`` uses all builtins.
    pool
        Optional finance-argus ``FactorPool``. When given, definitions are
        resolved from it (so synthetic/evolved factors are included); otherwise
        the seed 9 builtins are used.

    Raises ``KeyError`` for an unknown name and ``ValueError`` (from
    ``InMemoryFactorRegistry.from_iter``) on duplicates.
    """
    from finance_argus.core.factors import (  # lazy: keep pandas/qlib out of import
        FACTOR_DESCRIPTIONS,
        FACTORS,
    )

    if pool is not None:
        definitions = list(pool.definitions(names))
    elif names is None:
        definitions = list(FACTORS)
    else:
        by_name = {d.name: d for d in FACTORS}
        missing = [n for n in names if n not in by_name]
        if missing:
            raise KeyError(f"unknown finance-argus factor(s): {missing}")
        definitions = [by_name[n] for n in names]

    specs = [
        factor_spec_from_definition(
            d, description=FACTOR_DESCRIPTIONS.get(d.name, "")
        )
        for d in definitions
    ]
    return InMemoryFactorRegistry.from_iter(specs)
