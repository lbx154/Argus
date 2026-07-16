"""Unit tests for the VOICE CARD (style_profile) schema + derivation.

A thin card ({}) validates; a rich 红楼梦-style card validates; a bad enum or a
stray key is rejected; voice_card_from_brief derives per-profile defaults and
merges author overrides — fixing the ghost style_profile.json.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.fiction_writing.style import (
    StyleProfileError,
    ai_tell_budget,
    forbidden_lexicon,
    validate_voice_card,
    voice_card_from_brief,
)

_RICH_CARD = {
    "meta": {"language": "zh", "work_title": "红楼梦（续）", "register": "classical"},
    "abstract_features": {"sentence_rhythm": "long_and_flowing", "imagery_density": "high",
                          "dialogue_ratio": "high", "ending_strategy": "image_out"},
    "lexicon": {
        "appellations": [
            {"referent": "贾宝玉", "address_forms": ["宝二爷", "宝玉"], "by": "下人"},
            {"referent": "贾母", "address_forms": ["老太太", "老祖宗"], "by": "晚辈"},
        ],
        "preferred_terms": ["丫鬟", "老爷"],
        "avoided_terms": ["忽然"],
    },
    "forbidden_lexicon": ["手机", "地铁", "OK"],
    "sentence_targets": {"max_mean_sentence_len": None, "parallelism_ok": True},
    "dialogue_conventions": {"quote_style": "cjk_corner", "tag_discipline": "action_beats"},
    "ai_tell_budget": {"max_hits_per_1000_chars": 2.5},
}


def test_thin_card_is_valid():
    validate_voice_card({})  # every field optional


def test_rich_classical_card_is_valid():
    validate_voice_card(_RICH_CARD)
    assert forbidden_lexicon(_RICH_CARD) == ["手机", "地铁", "OK"]
    assert ai_tell_budget(_RICH_CARD) == 2.5


def test_bad_enum_is_rejected():
    with pytest.raises(StyleProfileError):
        validate_voice_card({"meta": {"register": "modern"}})  # not an allowed register
    with pytest.raises(StyleProfileError):
        validate_voice_card({"abstract_features": {"sentence_rhythm": "punchy"}})


def test_stray_key_is_rejected():
    with pytest.raises(StyleProfileError):
        validate_voice_card({"vibe": "cozy"})  # additionalProperties: false


def test_appellation_requires_referent_and_forms():
    with pytest.raises(StyleProfileError):
        validate_voice_card({"lexicon": {"appellations": [{"referent": "贾母"}]}})


def test_default_card_derives_from_profile():
    literary = voice_card_from_brief({"language": "zh", "profile": {"name": "literary_fiction"}})
    assert literary["meta"]["register"] == "literary"
    assert literary["meta"]["language"] == "zh"
    assert literary["abstract_features"]["sentence_rhythm"] == "long_and_flowing"

    web = voice_card_from_brief({"language": "zh", "profile": {"name": "web_fiction"}})
    assert web["meta"]["register"] == "web"
    assert web["abstract_features"]["sentence_rhythm"] == "short_and_tense"


def test_default_card_is_schema_valid_even_with_empty_brief():
    validate_voice_card(voice_card_from_brief({}))


def test_overrides_merge_onto_defaults():
    card = voice_card_from_brief(
        {"language": "zh", "profile": {"name": "literary_fiction"}},
        {"meta": {"register": "classical"}, "forbidden_lexicon": ["手机"]},
    )
    # nested meta merged (register overridden, language kept), forbidden added
    assert card["meta"]["register"] == "classical"
    assert card["meta"]["language"] == "zh"
    assert card["forbidden_lexicon"] == ["手机"]
    # abstract_features default survived the merge
    assert card["abstract_features"]["imagery_density"] == "high"
