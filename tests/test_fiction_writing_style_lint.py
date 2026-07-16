"""Unit tests for the anti-AI STYLE LINT (the '正则' layer).

Each zh + en cliché class must fire on a positive sample and stay quiet on clean
prose; an author-declared forbidden_lexicon term is BLOCKING; a declared
ai_tell_budget that is exceeded is BLOCKING; an unset budget keeps everything
non-blocking (today's behavior). Real negatives so gutting the checker goes red.
"""
from __future__ import annotations

from argus_skill.verticals.fiction_writing.style_lint import (
    STYLE_LINT_TYPES,
    check_style,
    is_clean,
)

_CLEAN_ZH = (
    "他推开门，屋里没有开灯。桌上放着一只碗，碗底还剩半口凉茶。\n\n"
    "窗外的雨刚停，屋檐上有水一滴一滴落下来。"
)
_CLEAN_EN = (
    "He pushed the door open. The room was dark, and a single bowl sat on the "
    "table.\n\nOutside, the rain had stopped, and water dripped from the eaves."
)


def _classes(findings):
    return {f["cliche_class"] for f in findings}


def test_lint_types_are_craft_vocabulary():
    assert STYLE_LINT_TYPES == {"ai_tell", "voice"}


def test_clean_prose_triggers_no_blocking_and_no_notes():
    assert check_style(_CLEAN_ZH, {}, "zh") == []
    assert check_style(_CLEAN_EN, {}, "en") == []
    assert is_clean(_CLEAN_ZH, {}, "zh")


def test_zh_cliche_classes_each_fire():
    assert "抽象抒情直说" in _classes(check_style("他心里满是孤独。", {}, "zh"))
    assert "万能陈词" in _classes(check_style("此刻他五味杂陈。", {}, "zh"))
    assert "排比堆砌" in _classes(
        check_style("冰冷的风，破碎的灯，无尽的夜，都压下来。", {}, "zh"))
    assert "翻译腔欧化" in _classes(check_style("他做出了微笑的动作。", {}, "zh"))
    assert "廉价转折词" in _classes(check_style("然而，一切都变了。", {}, "zh"))
    assert "升华式结尾" in _classes(
        check_style("夜很静。\n\n愿每个人都被温柔以待。", {}, "zh"))


def test_en_cliche_classes_each_fire():
    assert "telling_emotion" in _classes(check_style("The room was full of sadness.", {}, "en"))
    assert "filter_words" in _classes(check_style("She noticed that the door was open.", {}, "en"))
    assert "adverb_tags" in _classes(check_style('"Stop," he said angrily.', {}, "en"))
    assert "purple_cliche" in _classes(check_style("A shiver ran down her spine.", {}, "en"))
    assert "throat_clearing" in _classes(check_style("However, nothing changed.", {}, "en"))
    assert "uplift_ending" in _classes(
        check_style("The day ended.\n\nIn the end, it was a testament to hope.", {}, "en"))


def test_all_cliche_hits_are_nonblocking_notes():
    findings = check_style("他心里满是孤独，此刻五味杂陈。", {}, "zh")
    assert findings
    assert all(not f["blocking"] for f in findings)
    assert all(f["type"] == "ai_tell" for f in findings)
    assert all(f["calibration"] == "model-seed (BCC-pending)" for f in findings)


def test_forbidden_lexicon_term_is_blocking_voice():
    card = {"meta": {"language": "zh"}, "forbidden_lexicon": ["手机", "地铁"]}
    findings = check_style("他掏出手机，走进地铁站。", card, "zh")
    blocking = [f for f in findings if f["blocking"]]
    assert blocking
    assert all(f["type"] == "voice" for f in blocking)
    assert {f["cliche_class"] for f in blocking} == {"forbidden_lexicon"}
    # same prose, no declared forbidden lexicon -> nothing blocks
    assert is_clean("他掏出手机，走进地铁站。", {"meta": {"language": "zh"}}, "zh")


def test_avoided_terms_are_nonblocking_voice():
    card = {"meta": {"language": "zh"}, "lexicon": {"avoided_terms": ["忽然"]}}
    findings = check_style("他忽然停下。", card, "zh")
    voice = [f for f in findings if f["type"] == "voice"]
    assert voice and all(not f["blocking"] for f in voice)


def test_ai_tell_budget_blocks_only_when_exceeded():
    text = "他心里满是孤独。"  # one ai_tell hit in a short text -> high rate
    over = {"meta": {"language": "zh"}, "ai_tell_budget": {"max_hits_per_1000_chars": 1}}
    under = {"meta": {"language": "zh"}, "ai_tell_budget": {"max_hits_per_1000_chars": 1000}}
    assert not is_clean(text, over, "zh")          # rate >> 1 -> blocking
    assert is_clean(text, under, "zh")             # rate < 1000 -> no block
    assert is_clean(text, {}, "zh")                # no budget declared -> no block
