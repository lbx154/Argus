"""Corpus-ingestion SCAFFOLD for fiction_writing — the do-able, data-free half of
"step 4": the contract + plan skeleton the real ingestion plugs into. It does NOT
fetch, store, or fabricate anything (see ``references/corpus_ingestion_architecture.md``
for the full design). Like the calibration harness, it is honest about being
blocked without authorized sources rather than inventing cards.

The pipeline rides existing argus infrastructure — the source registry
(:mod:`.sources`) for authorization, and the
``learning`` vertical for distillation — and emits :data:`CRAFT_CARD_SCHEMA`
objects: ABSTRACT technique cards that carry evidence LOCATORS, never liftable
prose (the same anti-copy principle the voice cards / novelty gate enforce).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
with (_SCHEMA_DIR / "craft_card.schema.json").open(encoding="utf-8") as _fh:
    CRAFT_CARD_SCHEMA: dict[str, Any] = json.load(_fh)


class CraftCardError(ValueError):
    """Raised when a distilled craft_card violates the contract."""


#: The four-layer ingestion design (see the architecture doc). Each layer names
#: the source KIND and the allowed_use it is bound to — retrieval-only layers
#: (modern copyrighted corpora) never store text, they inform naturalness checks.
INGESTION_LAYERS: tuple[dict[str, str], ...] = (
    {"layer": "public_domain_study", "sources": "Gutenberg / ctext (public domain)",
     "allowed_use": "study technique, distill abstract craft cards"},
    {"layer": "modern_corpus_retrieval", "sources": "BCC / COCA (licensed)",
     "allowed_use": "retrieval-only naturalness lookup — NEVER ingest verbatim"},
    {"layer": "criticism_narratology", "sources": "scholarship on craft/narratology",
     "allowed_use": "distill craft cards with attribution"},
    {"layer": "authorized_samples", "sources": "self-authored / licensed modern samples",
     "allowed_use": "genre exemplars for modern verticals"},
)


def validate_craft_card(card: dict[str, Any]) -> None:
    """Raise :class:`CraftCardError` unless ``card`` satisfies the abstract-only
    contract (``abstracted: true``, evidence locators + short notes, bound rights)."""
    try:
        jsonschema.validate(card, CRAFT_CARD_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise CraftCardError(f"invalid craft_card: {exc.message}") from exc


def plan_ingestion(authorized_sources: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Return the ordered ingestion plan for the AUTHORIZED sources, or a blocked
    marker. Honest: with no authorized source it refuses to proceed — it neither
    fetches nor invents craft cards.

    ``authorized_sources`` are entries already cleared against the source
    registry; each should carry a ``kind`` matching an
    :data:`INGESTION_LAYERS` layer. Returns ``{"blocked": True, "reason": ...}``
    or ``{"blocked": False, "steps": [...]}``.
    """
    if not authorized_sources:
        return {"blocked": True,
                "reason": "no authorized sources — provide rights-cleared entries from the "
                          "source registry; will not fetch or fabricate craft cards."}
    layers = {layer["layer"] for layer in INGESTION_LAYERS}
    steps = []
    for src in authorized_sources:
        kind = src.get("kind")
        steps.append({
            "source_id": src.get("source_id"),
            "layer": kind if kind in layers else "unclassified",
            "action": "retrieval-only" if kind == "modern_corpus_retrieval" else "distill_craft_cards",
        })
    return {"blocked": False, "steps": steps}


def distill_card(evidence: list[dict[str, Any]], distill_fn: Callable[[list[dict[str, Any]]], dict[str, Any]],
                 ) -> dict[str, Any]:
    """Turn authorized ``evidence`` into a validated craft_card via a caller-supplied
    ``distill_fn`` (the LLM/``learning`` vertical in production). The result MUST pass
    :func:`validate_craft_card` — an invalid or non-abstract card raises, never slips
    through. This module supplies no distiller of its own and fabricates nothing.
    """
    card = distill_fn(evidence)
    validate_craft_card(card)
    return card


__all__ = [
    "CRAFT_CARD_SCHEMA",
    "CraftCardError",
    "INGESTION_LAYERS",
    "validate_craft_card",
    "plan_ingestion",
    "distill_card",
]
