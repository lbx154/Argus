"""Shared literary PROVENANCE contract — the per-mission source-usage log and the
rules that make it honest.

:mod:`.source_registry` is the catalog (what may be used, and how). This module
is the LEDGER (what was actually used, and for what), plus the cross-check that
ties the two together at run time. A literary vertical that consults an external
source — queries a corpus, reads a public-domain text, quotes a passage — records
a ``source_usage`` entry; :func:`validate_usage` then rejects the whole log unless
every entry is defensible against the registry.

The rejections this enforces (each a real, observable runtime failure, not a
lint):

* the ``source_id`` is not registered — you cannot use a source that does not
  exist in the registry;
* the ``use`` is not permitted by the source's rights (not in ``allowed_uses``,
  or in ``prohibited_uses``) — e.g. treating a query-only corpus as training data;
* an ``evidence_citation`` carries no ``citation`` — a verbatim quote must name
  its source;
* a use that implies INGESTION (``evidence_citation`` / ``local_indexing`` /
  ``model_training`` / ``redistribution``) names a source whose ``ingested`` flag
  is false — the honesty guard that stops "we queried it" from being laundered
  into "we learned/quoted it".

Scope note (kept honest): this validates the RIGHTS/PROVENANCE shape of a use. It
does NOT verify a citation quote appears verbatim in the source text — that is the
ingestion track's job (see :mod:`argus_skill.skills.provenance`) and only becomes
possible once a source is actually ingested with local text.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .source_registry import (
    USES_REQUIRING_INGESTION,
    RegistryError,
    assert_use_allowed,
)

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def _load_schema(name: str) -> dict[str, Any]:
    with (_SCHEMA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


SOURCE_USAGE_SCHEMA: dict[str, Any] = _load_schema("source_usage.schema.json")


class ProvenanceError(ValueError):
    """Raised when a source-usage log is malformed or not defensible."""


def validate_usage(usage: dict[str, Any], registry: dict[str, Any]) -> None:
    """Structural + provenance validation of a usage log against ``registry``.

    ``registry`` is expected to be already valid (see
    :func:`argus_skill.literary.source_registry.validate_registry`). Raises
    :class:`ProvenanceError` on the first indefensible entry.
    """
    try:
        jsonschema.validate(usage, SOURCE_USAGE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ProvenanceError(f"invalid source_usage: {exc.message}") from exc

    uses = usage["uses"]
    ids = [u["use_id"] for u in uses]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ProvenanceError(f"duplicate use_id(s): {dupes}")

    for u in uses:
        uid = u["use_id"]
        source_id = u["source_id"]
        use = u["use"]
        try:
            item = assert_use_allowed(registry, source_id, use)
        except RegistryError as exc:
            raise ProvenanceError(f"use {uid!r}: {exc}") from exc

        if use == "evidence_citation" and not (u.get("citation") or "").strip():
            raise ProvenanceError(
                f"use {uid!r}: evidence_citation of {source_id!r} carries no "
                f"citation — a verbatim quote must name its source"
            )
        if use in USES_REQUIRING_INGESTION and not item.get("ingested"):
            raise ProvenanceError(
                f"use {uid!r}: {use!r} of {source_id!r} but the source is not "
                f"ingested (ingested=false) — a queried-but-not-ingested source "
                f"cannot be cited, indexed, trained on, or redistributed"
            )


def normalize_usage(raw: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    """Fill per-use optional defaults (citation/result_summary/note), then
    validate against ``registry``. Returns a new dict; input is not mutated."""
    if not isinstance(raw, dict):
        raise ProvenanceError("source_usage must be a JSON object")
    usage = dict(raw)
    uses = usage.get("uses", [])
    if not isinstance(uses, list):
        raise ProvenanceError("source_usage.uses must be an array")
    norm: list[dict[str, Any]] = []
    for u in uses:
        if not isinstance(u, dict):
            raise ProvenanceError("each use must be an object")
        g = dict(u)
        g.setdefault("citation", "")
        g.setdefault("result_summary", "")
        g.setdefault("note", "")
        norm.append(g)
    usage["uses"] = norm
    validate_usage(usage, registry)
    return usage


__all__ = [
    "SOURCE_USAGE_SCHEMA",
    "ProvenanceError",
    "validate_usage",
    "normalize_usage",
]
