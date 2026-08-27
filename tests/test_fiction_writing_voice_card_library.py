"""Voice-card LIBRARY + 3-layer composition tests.

Proves: every shipped preset validates against the schema; compose merges by the
documented semantics (nested dicts merge, lexicon lists UNION, character_voices
dedup by name with the later layer winning); genre substrings do not select a preset;
and the classical preset ships
per-character voices ('什么样的人物什么卡').
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.fiction_writing.style import (
    StyleProfileError,
    compose_voice_card,
    domain_for_brief,
    list_voice_card_presets,
    load_voice_card_preset,
    validate_voice_card,
    voice_card_from_brief,
)

_EXPECTED_DOMAINS = {
    "base", "suspense", "classical_zhanghui", "web_fiction",
    "romance", "scifi", "literary",
}


def test_library_ships_expected_presets():
    assert _EXPECTED_DOMAINS <= set(list_voice_card_presets())


@pytest.mark.parametrize("name", sorted(_EXPECTED_DOMAINS))
def test_every_preset_is_schema_valid(name):
    card = load_voice_card_preset(name)  # loads + validates (strips _note)
    validate_voice_card(card)


def test_unknown_preset_is_rejected():
    with pytest.raises(StyleProfileError):
        load_voice_card_preset("does_not_exist")


def test_classical_preset_has_per_character_voices():
    card = load_voice_card_preset("classical_zhanghui")
    voices = {c["character"] for c in card["character_voices"]}
    assert {"林黛玉", "王熙凤", "刘姥姥"} <= voices
    # a rustic character bans literary diction from her own mouth
    liu = next(c for c in card["character_voices"] if c["character"] == "刘姥姥")
    assert liu["forbidden_for_character"]


def test_domain_for_brief_never_routes_from_substrings():
    for brief in (
        {"genre": "近未来悬疑科幻"},
        {"genre": "红楼章回体续写"},
        {"genre": "严肃文学"},
        {"market_style": "网络小说"},
    ):
        assert domain_for_brief(brief) is None


def test_compose_three_layers_merges_by_semantics():
    card = compose_voice_card(
        "base", "classical_zhanghui",
        {
            "meta": {"work_title": "红楼梦（续）"},
            "forbidden_lexicon": ["路灯"],
            "character_voices": [
                {"character": "薛宝钗", "notes": "端方稳重"},          # new
                {"character": "林黛玉", "notes": "本作覆盖"},          # override
            ],
        },
    )
    # domain register wins over base; work title applied
    assert card["meta"]["register"] == "classical"
    assert card["meta"]["work_title"] == "红楼梦（续）"
    # forbidden lexicon UNIONs library + work
    assert "手机" in card["forbidden_lexicon"] and "路灯" in card["forbidden_lexicon"]
    # character_voices dedup by name: library 4 + 1 new = 5; 黛玉 overridden
    names = [c["character"] for c in card["character_voices"]]
    assert "薛宝钗" in names
    assert names.count("林黛玉") == 1
    daiyu = next(c for c in card["character_voices"] if c["character"] == "林黛玉")
    assert daiyu["notes"] == "本作覆盖"


def test_build_from_brief_stays_neutral_without_explicit_voice():
    card = voice_card_from_brief({"language": "zh", "genre": "悬疑推理"})
    assert card == {"meta": {"language": "zh"}}
    validate_voice_card(card)


def test_profile_label_does_not_lock_a_voice_preset():
    card = voice_card_from_brief({"language": "zh", "profile": {"name": "literary_fiction"}})
    assert card == {"meta": {"language": "zh"}}


def test_explicit_domain_choice_still_composes_a_preset():
    card = voice_card_from_brief({"language": "zh"}, domain="suspense")
    assert card["abstract_features"]["sentence_rhythm"] == "short_and_tense"
