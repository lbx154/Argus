"""fiction_writing VOICE CARD (``style_profile``) — the fine-grained '预设' layer.

Until now ``fiction/style_profile.json`` was a GHOST: the drafting skill told the
engineer to "load style_profile.json", but there was no schema, nothing created
or validated it, and the review stage never even read it — so the only real style
knob was the coarse 5-value genre profile. This module gives the card a real,
checkable schema and threads it end-to-end:

* **captured** at intake (:func:`voice_card_from_brief` seeds a valid default from
  the genre profile; the author overrides it richly — e.g. a 红楼梦 continuation's
  appellations / forbidden modern words / classical register);
* **injected** into the drafting prompt (the engineer honors register / lexicon);
* **enforced** at review — a declared ``forbidden_lexicon`` term or an exceeded
  ``ai_tell_budget`` is a BLOCKING finding (see :mod:`.style_lint`), while softer
  features stay non-blocking reviewer guidance.

The card is ABSTRACT FEATURES + an EXPLICIT lexicon, never "imitate author X".
Its SHAPE is language/genre-agnostic; a zh classical work and an en thriller
differ only in the DATA they put in it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .profiles import DEFAULT_PROFILE

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_VOICE_CARD_DIR = Path(__file__).resolve().parent / "references" / "voice_cards"


def _load_schema(name: str) -> dict[str, Any]:
    with (_SCHEMA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


STYLE_PROFILE_SCHEMA: dict[str, Any] = _load_schema("style_profile.schema.json")

#: Per-genre-profile default abstract features, so EVERY mission gets a valid,
#: non-empty card even when the operator declares no fine-grained style. The
#: author (especially a continuation) overrides these richly.
_PROFILE_DEFAULTS: dict[str, dict[str, str]] = {
    "web_fiction": {"sentence_rhythm": "short_and_tense", "imagery_density": "low",
                    "exposition_level": "direct", "ending_strategy": "reversal"},
    "genre_fiction": {"sentence_rhythm": "varied", "imagery_density": "medium",
                      "exposition_level": "moderate", "ending_strategy": "reversal"},
    "literary_fiction": {"sentence_rhythm": "long_and_flowing", "imagery_density": "high",
                         "exposition_level": "restrained", "ending_strategy": "image_out"},
    "short_story": {"sentence_rhythm": "varied", "imagery_density": "medium",
                    "exposition_level": "restrained", "ending_strategy": "image_out"},
    "long_form_serial": {"sentence_rhythm": "varied", "imagery_density": "medium",
                         "exposition_level": "moderate", "ending_strategy": "open"},
}

#: Default register per genre profile. ``classical`` is never guessed — a
#: continuation of a classical work sets it explicitly via an override.
_PROFILE_REGISTER: dict[str, str] = {
    "web_fiction": "web",
    "literary_fiction": "literary",
    "short_story": "literary",
}


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
#: genre/market keyword -> library domain preset. The DETERMINISTIC half of
#: "auto-建档 from the prompt": pick a domain preset from the brief's genre. The
#: authored half (称谓 / character_voices, read from the prompt) stays the
#: engineer's job — dialogue and cast can't be regex-extracted reliably.
_DOMAIN_KEYWORDS: dict[str, str] = {
    "suspense": "suspense", "悬疑": "suspense", "推理": "suspense", "mystery": "suspense",
    "romance": "romance", "言情": "romance", "爱情": "romance",
    "scifi": "scifi", "sci-fi": "scifi", "科幻": "scifi",
    "literary": "literary", "纯文学": "literary", "严肃": "literary",
    "web": "web_fiction", "网文": "web_fiction", "网络": "web_fiction",
    "classical": "classical_zhanghui", "古典": "classical_zhanghui",
    "章回": "classical_zhanghui", "红楼": "classical_zhanghui",
}

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
    """Deterministic first guess at a library domain preset from the brief's genre.

    Returns a preset name or ``None`` (no keyword matched). The DETERMINISTIC half
    of auto-建档; the engineer still authors 称谓/character_voices from the prompt.
    """
    hay = " ".join(str(brief.get(k, "")) for k in ("genre", "market_style")).lower()
    for keyword, preset in _DOMAIN_KEYWORDS.items():
        if keyword in hay:
            return preset
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

    Guarantees every mission a schema-valid, non-empty card. If a library ``domain``
    preset applies — passed explicitly OR auto-detected from the brief's genre via
    :func:`domain_for_brief` — the card is the 3-layer compose ``base <- domain <-
    {language} <- overrides`` (the auto-建档 path). Otherwise it falls back to the
    coarse genre-profile defaults. ``overrides`` (the author's hand-authored slots —
    称谓 / character_voices / forbidden anachronisms) always win. Never invents
    lexicon it was not given.
    """
    language = brief.get("language", "zh")
    resolved = domain if domain is not None else domain_for_brief(brief)
    if resolved:
        return compose_voice_card(
            "base", resolved, {"meta": {"language": language}}, overrides or {})

    # no library domain matched -> coarse genre-profile default
    profile_name = (brief.get("profile") or {}).get("name") or DEFAULT_PROFILE
    card: dict[str, Any] = {
        "meta": {
            "language": language,
            "register": _PROFILE_REGISTER.get(profile_name, "contemporary"),
        },
        "abstract_features": dict(
            _PROFILE_DEFAULTS.get(profile_name, _PROFILE_DEFAULTS[DEFAULT_PROFILE])
        ),
    }
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


def ai_tell_budget(card: dict[str, Any]) -> float | None:
    """The declared max anti-AI-cliché hits per 1000 chars, or ``None`` if unset."""
    val = (card.get("ai_tell_budget") or {}).get("max_hits_per_1000_chars")
    return float(val) if isinstance(val, (int, float)) else None


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
    "ai_tell_budget",
    "novelty_budget",
]
