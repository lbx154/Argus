"""fiction_writing VOICE CARD (``style_profile``) — the fine-grained '预设' layer.

Until now ``fiction/style_profile.json`` was a GHOST: the drafting skill told the
engineer to "load style_profile.json", but there was no schema, nothing created
or validated it, and the review stage never even read it — so the only real style
knob was the coarse 5-value genre profile. This module gives the card a real,
checkable schema and threads it end-to-end:

* **captured** at intake (:func:`voice_card_from_brief` preserves explicit style
  choices and otherwise produces a neutral card for the Engineer to develop);
* **injected** into the drafting prompt (the engineer honors register / lexicon);
* **reviewed** in context — explicit ``forbidden_lexicon`` constraints remain hard,
  while word-list cues and other voice features inform the Reviewer.

The card is ABSTRACT FEATURES + an EXPLICIT lexicon, never "imitate author X".
Its SHAPE is language/genre-agnostic; a zh classical work and an en thriller
differ only in the DATA they put in it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_VOICE_CARD_DIR = Path(__file__).resolve().parent / "references" / "voice_cards"


def _load_schema(name: str) -> dict[str, Any]:
    with (_SCHEMA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


STYLE_PROFILE_SCHEMA: dict[str, Any] = _load_schema("style_profile.schema.json")

class StyleProfileError(ValueError):
    """Raised when a voice card (style_profile) is malformed."""


def validate_voice_card(card: dict[str, Any]) -> None:
    """Raise :class:`StyleProfileError` if ``card`` violates the style_profile schema."""
    try:
        jsonschema.validate(card, STYLE_PROFILE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise StyleProfileError(f"invalid style_profile: {exc.message}") from exc


# --------------------------------------------------------------------------- #
# Voice-card LIBRARY + 3-layer composition (base <- domain preset <- work/char)
# --------------------------------------------------------------------------- #
#: lexicon list keys whose layers UNION (accumulate across base/domain/work);
#: object-list keys dedup by an identity field so a later layer overrides a
#: same-named entry.
_KEYED_LISTS: dict[str, str] = {"character_voices": "character", "appellations": "referent"}


def list_voice_card_presets() -> list[str]:
    """The library domain preset names available to compose from."""
    if not _VOICE_CARD_DIR.is_dir():
        return []
    return sorted(p.stem for p in _VOICE_CARD_DIR.glob("*.json"))


def load_voice_card_preset(name: str) -> dict[str, Any]:
    """Load + validate a library preset. Keys starting with ``_`` (e.g. ``_note``)
    are documentation and stripped before validation."""
    path = _VOICE_CARD_DIR / f"{name}.json"
    if not path.is_file():
        raise StyleProfileError(
            f"unknown voice-card preset {name!r} (known: {list_voice_card_presets()})")
    raw = json.loads(path.read_text(encoding="utf-8"))
    card = {k: v for k, v in raw.items() if not str(k).startswith("_")}
    validate_voice_card(card)
    return card


def _merge_lists(key: str, a: list[Any], b: list[Any]) -> list[Any]:
    if key in _KEYED_LISTS:
        idk = _KEYED_LISTS[key]
        merged: dict[Any, Any] = {}
        for item in list(a) + list(b):  # later layer wins per identity
            marker = item.get(idk) if isinstance(item, dict) else item
            merged[marker] = item
        return list(merged.values())
    seen: set[str] = set()
    out: list[Any] = []
    for item in list(a) + list(b):  # string lists: union, order-preserving
        marker = item if isinstance(item, str) else json.dumps(item, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        elif isinstance(val, list) and isinstance(out.get(key), list):
            out[key] = _merge_lists(key, out[key], val)
        else:
            out[key] = val
    return out


def compose_voice_card(*layers: str | dict[str, Any]) -> dict[str, Any]:
    """Compose a voice card by deep-merging layers left-to-right, then validate.

    Each layer is a library preset NAME (str) or a card dict. This is the 3-layer
    model: ``compose_voice_card("base", "classical_zhanghui", work_overlay)`` ->
    universal defaults, then the domain preset, then the work/character overlay.
    Nested dicts merge; ``forbidden_lexicon``/``preferred_terms``/``avoided_terms``
    UNION across layers; ``character_voices``/``appellations`` dedup by identity so
    a later layer overrides a same-named entry.
    """
    result: dict[str, Any] = {}
    for layer in layers:
        card = load_voice_card_preset(layer) if isinstance(layer, str) else {
            k: v for k, v in (layer or {}).items() if not str(k).startswith("_")}
        result = _deep_merge(result, card)
    validate_voice_card(result)
    return result


def domain_for_brief(brief: dict[str, Any]) -> str | None:
    """Return no automatic preset; voice requires whole-brief judgment.

    Kept as a compatibility hook for callers that previously asked for a keyword
    guess. A preset is now used only when passed explicitly to
    :func:`voice_card_from_brief`.
    """
    return None


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """One-level-deep merge: nested dict values are merged, everything else replaced."""
    out = dict(base)
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **val}
        else:
            out[key] = val
    return out


def voice_card_from_brief(
    brief: dict[str, Any],
    overrides: dict[str, Any] | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Derive a valid voice card from a ``creative_brief``, then merge overrides.

    An explicit ``domain`` composes a library preset. Without one, return a neutral
    card containing only language and explicit overrides; the Engineer proposes voice
    after reading the whole brief. Never infer a preset from a substring or invent a
    lexicon the operator did not provide.
    """
    language = brief.get("language", "zh")
    if domain:
        return compose_voice_card(
            "base", domain, {"meta": {"language": language}}, overrides or {})

    card: dict[str, Any] = {"meta": {"language": language}}
    if overrides:
        card = _merge(card, overrides)
    validate_voice_card(card)
    return card


def forbidden_lexicon(card: dict[str, Any]) -> list[str]:
    """The author-declared HARD forbidden terms (drives a BLOCKING lint finding)."""
    return [w for w in (card.get("forbidden_lexicon") or []) if w]


def avoided_terms(card: dict[str, Any]) -> list[str]:
    """Soft terms to avoid (drives a NON-blocking lint note)."""
    return [w for w in ((card.get("lexicon") or {}).get("avoided_terms") or []) if w]


def novelty_budget(card: dict[str, Any]) -> dict[str, Any]:
    """The declared anti-copy thresholds (drives :mod:`.novelty`), or ``{}`` if unset.

    Keys, both optional: ``max_verbatim_run`` (a positive int overriding the
    model-seed block threshold, in the language's token unit — zh chars / en words)
    and ``max_overlap_ratio`` (a 0..1 fraction whose exceedance is BLOCKING).
    """
    nb = card.get("novelty_budget") or {}
    out: dict[str, Any] = {}
    run = nb.get("max_verbatim_run")
    if isinstance(run, int) and not isinstance(run, bool) and run > 0:
        out["max_verbatim_run"] = run
    ratio = nb.get("max_overlap_ratio")
    if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and ratio >= 0:
        out["max_overlap_ratio"] = float(ratio)
    return out


__all__ = [
    "STYLE_PROFILE_SCHEMA",
    "StyleProfileError",
    "validate_voice_card",
    "list_voice_card_presets",
    "load_voice_card_preset",
    "compose_voice_card",
    "domain_for_brief",
    "voice_card_from_brief",
    "forbidden_lexicon",
    "avoided_terms",
    "novelty_budget",
]
