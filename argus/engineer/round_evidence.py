"""Vertical-blind registry for host-gathered evidence after an Engineer round.

After the Engineer's turn the host may observe the project without spending a
model token -- run the project's own checks, measure an artifact, diff a
config. What it observes is *evidence*: it is rendered into the Reviewer's
raw-evidence slot and summarised for the next Engineer round. Nothing here
blocks a stage, admits or rejects a task, or overrides a role's judgment.

This module knows nothing about what the evidence is. A vertical (or any
other package above this one) registers a provider at import time, the same
way ``team/external_work.py`` registers its external-work source; the round
loop calls :func:`collect_round_evidence` once per completed Engineer turn and
stages whatever comes back. The registry is fail-soft: a provider that raises
is logged and skipped, and the others still run. Keeping a provider within a
wall-clock budget is the provider's own job -- it knows what it is running.

Each provider owns a small opaque ``state`` dict that the host stores between
rounds and hands back on the next request (``previous_state``), so a provider
can compare this round with the last one -- for example, name the tests that
were collected last round but are not collected now.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoundEvidenceRequest:
    """What a provider is told about the round it should look at.

    ``previous_state`` is *this provider's* state from its last successful
    contribution (``{}`` on the first round). The caller hands
    :func:`collect_round_evidence` the whole per-provider map and the
    collector slices it; a provider never sees another provider's state.
    """

    workdir: Path
    life_dir: Path
    round_index: int
    previous_state: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RoundEvidence:
    """One provider's contribution for one round.

    ``reviewer_text`` goes to the Reviewer's raw-evidence slot verbatim;
    ``engineer_note`` is appended to the next Engineer prompt; ``state`` is an
    opaque JSON-serialisable dict returned to the same provider next round.
    ``provider`` is filled in by the collector (see :func:`provider_key`).
    """

    reviewer_text: str = ""
    engineer_note: str = ""
    state: dict = field(default_factory=dict)
    provider: str = ""


RoundEvidenceProvider = Callable[[RoundEvidenceRequest], "RoundEvidence | None"]

_PROVIDERS: list[RoundEvidenceProvider] = []


def provider_key(provider: RoundEvidenceProvider) -> str:
    """Stable identity of a provider for keying its state between rounds."""
    module = getattr(provider, "__module__", "") or ""
    name = getattr(provider, "__qualname__", "") or getattr(provider, "__name__", "") or repr(provider)
    return f"{module}:{name}"


def register_round_evidence_provider(provider: RoundEvidenceProvider) -> RoundEvidenceProvider:
    """Add a provider; idempotent so a re-imported module registers once.

    Usable as a decorator. Providers run in registration order.
    """
    if provider not in _PROVIDERS:
        _PROVIDERS.append(provider)
    return provider


def registered_round_evidence_providers() -> tuple[RoundEvidenceProvider, ...]:
    return tuple(_PROVIDERS)


def collect_round_evidence(request: RoundEvidenceRequest) -> list[RoundEvidence]:
    """Call every registered provider in order and return what they produced.

    ``request.previous_state`` is the per-provider map the caller stored last
    round (``{provider_key: state}``); each provider receives only its own
    slice. A provider that returns ``None`` has nothing to show this round and
    keeps its previous state; a provider that raises is logged and skipped,
    and never stops the others. The returned items carry ``provider`` so the
    caller can store ``{item.provider: item.state}`` for the next round.
    """
    previous_states = request.previous_state if isinstance(request.previous_state, dict) else {}
    results: list[RoundEvidence] = []
    for provider in list(_PROVIDERS):
        key = provider_key(provider)
        own_previous = previous_states.get(key)
        own_request = replace(
            request,
            previous_state=dict(own_previous) if isinstance(own_previous, dict) else {},
        )
        try:
            produced = provider(own_request)
        except Exception:  # noqa: BLE001 - evidence gathering must never break the round
            log.exception("round-evidence provider %s failed for round %s", key, request.round_index)
            continue
        if produced is None:
            continue
        if not isinstance(produced, RoundEvidence):
            log.warning("round-evidence provider %s returned %r; skipped", key, type(produced))
            continue
        state = produced.state if isinstance(produced.state, dict) else {}
        results.append(replace(produced, state=dict(state), provider=key))
    return results


__all__ = [
    "RoundEvidence",
    "RoundEvidenceProvider",
    "RoundEvidenceRequest",
    "collect_round_evidence",
    "provider_key",
    "register_round_evidence_provider",
    "registered_round_evidence_providers",
]
