"""Shared literary-vertical SOURCE REGISTRY contract — the rights-and-provenance catalog a
literary vertical consults before it uses any external text.

Where :mod:`.task_envelope` governs the request and :mod:`.artifact_manifest`
governs what was produced, this module governs *what outside material may be used
and how*. A registry has two layers (rights are decided per work/edition, not per
website):

* ``providers`` — the site/service and whether its terms were reviewed;
* ``items``     — a specific work/edition, its jurisdiction-based ``rights_status``
  and, crucially, its ``allowed_uses`` / ``prohibited_uses`` drawn from a granular
  ``allowed_use_vocabulary`` (querying is not downloading, indexing is not
  training, training is not redistribution).

This module does NOT ingest anything. It (1) loads and validates a registry, and
(2) answers the one question the runtime provenance gate needs:
:func:`assert_use_allowed` — *may source X be used for purpose P?* — so that a
mission which uses a source for a purpose its rights do not permit fails loudly
rather than silently laundering a query-only corpus into training data.

Semantic invariants the raw YAML cannot express, enforced by
:func:`validate_registry`:

* provider and item ids are unique; every item's ``provider`` exists;
* ``allowed_uses`` and ``prohibited_uses`` are subsets of the vocabulary and are
  DISJOINT (a use cannot be both allowed and forbidden);
* ``rights_status`` is a known value;
* honesty guard — ``ingested: true`` REQUIRES a ``checksum`` and
  ``rights_status == "cleared"``: you cannot mark something learned/ingested that
  has no fixed content hash or whose rights were never cleared.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

#: Uses that imply the content was actually taken in (a local copy / index / a
#: verbatim quote / a training corpus). Merely querying a hosted corpus or a human
#: reading it do NOT. The runtime gate uses this to reject "cite/index/train on a
#: source we never ingested".
USES_REQUIRING_INGESTION: frozenset[str] = frozenset({
    "evidence_citation", "local_indexing", "model_training", "redistribution",
})

#: Known rights states an item may carry.
RIGHTS_STATUSES: frozenset[str] = frozenset({
    "pending_review", "cleared", "restricted",
})

_ITEM_REQUIRED = ("id", "provider", "rights_status", "allowed_uses")


class RegistryError(ValueError):
    """Raised when a source registry is malformed or a use is not permitted."""


def load_registry(path: str | Path) -> dict[str, Any]:
    """Parse a ``sources.yaml`` registry into a dict (no validation)."""
    p = Path(path)
    if not p.is_file():
        raise RegistryError(f"source registry not found: {path}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegistryError(f"source registry is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError("source registry must be a mapping")
    return data


def validate_registry(registry: dict[str, Any]) -> None:
    """Structural + semantic validation of a parsed registry.

    Raises :class:`RegistryError` on any violation (see module docstring).
    """
    if not isinstance(registry, dict):
        raise RegistryError("source registry must be a mapping")
    vocab = registry.get("allowed_use_vocabulary")
    if not isinstance(vocab, list) or not vocab or not all(isinstance(v, str) for v in vocab):
        raise RegistryError("allowed_use_vocabulary must be a non-empty list of strings")
    vocab_set = set(vocab)

    providers = registry.get("providers") or []
    items = registry.get("items") or []
    if not isinstance(providers, list) or not isinstance(items, list):
        raise RegistryError("providers and items must be lists")

    provider_ids: set[str] = set()
    for prov in providers:
        if not isinstance(prov, dict) or "id" not in prov:
            raise RegistryError(f"malformed provider entry: {prov!r}")
        pid = prov["id"]
        if pid in provider_ids:
            raise RegistryError(f"duplicate provider id {pid!r}")
        provider_ids.add(pid)

    item_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise RegistryError(f"malformed item entry: {item!r}")
        for key in _ITEM_REQUIRED:
            if key not in item:
                raise RegistryError(f"item {item.get('id')!r} missing required {key!r}")
        iid = item["id"]
        if iid in item_ids:
            raise RegistryError(f"duplicate item id {iid!r}")
        item_ids.add(iid)

        if item["provider"] not in provider_ids:
            raise RegistryError(
                f"item {iid!r} references unknown provider {item['provider']!r}"
            )
        if item["rights_status"] not in RIGHTS_STATUSES:
            raise RegistryError(
                f"item {iid!r}: unknown rights_status {item['rights_status']!r} "
                f"(known: {sorted(RIGHTS_STATUSES)})"
            )

        allowed = set(item.get("allowed_uses") or [])
        prohibited = set(item.get("prohibited_uses") or [])
        if not allowed <= vocab_set:
            raise RegistryError(
                f"item {iid!r}: allowed_uses {sorted(allowed - vocab_set)} "
                f"outside the vocabulary"
            )
        if not prohibited <= vocab_set:
            raise RegistryError(
                f"item {iid!r}: prohibited_uses {sorted(prohibited - vocab_set)} "
                f"outside the vocabulary"
            )
        overlap = allowed & prohibited
        if overlap:
            raise RegistryError(
                f"item {iid!r}: use(s) {sorted(overlap)} both allowed and prohibited"
            )

        if item.get("ingested"):
            if not item.get("checksum"):
                raise RegistryError(
                    f"item {iid!r}: ingested=true but no checksum — an ingested "
                    f"source must pin a fixed content hash"
                )
            if item["rights_status"] != "cleared":
                raise RegistryError(
                    f"item {iid!r}: ingested=true but rights_status is "
                    f"{item['rights_status']!r}, not 'cleared'"
                )


def load_validated_registry(path: str | Path) -> dict[str, Any]:
    """Load a registry and validate it; return the parsed dict."""
    registry = load_registry(path)
    validate_registry(registry)
    return registry


def query(registry: dict[str, Any], source_id: str) -> dict[str, Any]:
    """Return the registry item for ``source_id`` (read-only lookup).

    Raises :class:`RegistryError` if no such source is registered — an unknown
    source can never be silently treated as usable.
    """
    for item in registry.get("items") or []:
        if item.get("id") == source_id:
            return item
    raise RegistryError(f"unknown source {source_id!r} — not in the registry")


def assert_use_allowed(registry: dict[str, Any], source_id: str, use: str) -> dict[str, Any]:
    """Raise :class:`RegistryError` unless ``source_id`` may be used for ``use``.

    Enforces: the source is registered; ``use`` is in the registry vocabulary; it
    is in the source's ``allowed_uses`` and not in its ``prohibited_uses``. Returns
    the item so callers can apply further rules (e.g. ingestion requirements).
    """
    vocab = set(registry.get("allowed_use_vocabulary") or [])
    if use not in vocab:
        raise RegistryError(f"unknown use {use!r} (vocabulary: {sorted(vocab)})")
    item = query(registry, source_id)
    prohibited = set(item.get("prohibited_uses") or [])
    allowed = set(item.get("allowed_uses") or [])
    if use in prohibited:
        raise RegistryError(f"source {source_id!r}: use {use!r} is PROHIBITED")
    if use not in allowed:
        raise RegistryError(
            f"source {source_id!r}: use {use!r} not in allowed_uses {sorted(allowed)}"
        )
    return item


__all__ = [
    "USES_REQUIRING_INGESTION",
    "RIGHTS_STATUSES",
    "RegistryError",
    "load_registry",
    "validate_registry",
    "load_validated_registry",
    "query",
    "assert_use_allowed",
]
