"""fiction_writing anti-AI STYLE LINT — the SillyTavern-style '正则' layer.

references/zh/style-and-anti-ai.md listed regex-detectable AI-cliché classes and
said "落地建议：做成一份 zh 正则表，reviewer 跑一遍" — but it was never built; the
patterns lived only in an orphan doc. This module IS that lint, modeled 1:1 on
``modern_poetry/form.py``'s deterministic ``check_form``.

Honesty contract: the cliché tables and the default budget are **model-seed**,
pending calibration against the BCC modern-Chinese corpus (see
``references/source_registry/README.md``). So a table hit is a *prompt for review*,
NOT ground truth — every such finding is a NON-blocking ``ai_tell`` note carrying
``calibration="model-seed (BCC-pending)"``. The only teeth are author-declared
HARD contracts weighed here: a ``forbidden_lexicon`` term present, or a declared
``ai_tell_budget`` exceeded — those are deterministic facts (a named word appeared
/ a declared budget was passed), so they are BLOCKING ``voice``/``ai_tell``.
"""
from __future__ import annotations

import re
from typing import Any

from .style import ai_tell_budget, avoided_terms, forbidden_lexicon

#: The finding types this lint emits (both already in FICTION_CRAFT_TYPES).
STYLE_LINT_TYPES: frozenset[str] = frozenset({"ai_tell", "voice"})

_CALIBRATION = "model-seed (BCC-pending)"

# --------------------------------------------------------------------------- #
# zh anti-AI cliché tables — verbatim classes from references/zh/style-and-ai.md
# --------------------------------------------------------------------------- #
_ZH_FULLTEXT: dict[str, list[re.Pattern[str]]] = {
    "抽象抒情直说": [re.compile(r"思念|孤独|悲伤|温暖|治愈")],
    "排比堆砌": [re.compile(r"(?:[^，。！？\n]{1,20}的[^，。！？\n]{1,20}，){3,}")],
    "万能陈词": [re.compile(
        r"仿佛全世界|像被抽空|五味杂陈|潸然泪下|嘴角[勾扬]起.{0,4}弧度|不禁潸然")],
    "翻译腔欧化": [re.compile(
        r"进行了一[次场]|做出了.{0,6}的动作|其中的一个|不得不说|一个.{0,8}的存在")],
    # --- register-level structural tells: survive AIGC 查重 but read as "AI 味" ---
    "情绪涌动模板": [re.compile(
        r"(?:心[中里底头]|心底|胸口|眼[眶底]|脑海|喉[咙间])(?:深处)?[，、]?[^，。！？\n]{0,4}"
        r"(?:涌|泛|升|漫|溢|袭|漾)(?:起|上|来|过)[^，。！？\n]{0,6}"
        r"(?:暖流|酸楚|暖意|悸动|情绪|思绪|温暖|甜蜜|苦涩|愧疚|寒意|感动|一丝|一股|一阵|一抹)")],
    "凝固时刻": [re.compile(
        r"(?:空气|时间|时光|一切|周围|四周|世界|画面)[^，。！？\n]{0,3}"
        r"(?:仿佛|似乎|像是|好像|竟)?[^，。！？\n]{0,2}(?:凝固|静止|停滞|凝滞)")],
    "时刻拔高": [re.compile(r"(?:在)?(?:这一|那一)(?:刻|瞬间|刹那)|刹那间|一瞬间")],
    "虚化感受": [re.compile(r"说不出的|难以言喻|难以名状|无法形容|一种[^，。！？\n]{0,8}的感觉")],
    "副词堆砌": [re.compile(
        r"(?:(?:无声|静静|轻轻|缓缓|默默|淡淡|悄悄|微微|深深)地[^。！？\n]{0,12}){2,}")],
}
_ZH_PARA_INITIAL: dict[str, list[re.Pattern[str]]] = {
    "廉价转折词": [re.compile(r"^(然而|但是|其实|事实上)")],
}
_ZH_ENDING: dict[str, list[re.Pattern[str]]] = {
    "升华式结尾": [re.compile(
        r"愿[^。！\n]{0,20}[。！]|让我们|在这个.{0,12}的时代|"
        r"这就是.{0,15}的意义|无论.{0,20}都(?:是|会|能|要)")],
}

# --------------------------------------------------------------------------- #
# en anti-AI cliché tables — classes from references/en/style-and-anti-ai.md
# --------------------------------------------------------------------------- #
_EN_FULLTEXT: dict[str, list[re.Pattern[str]]] = {
    "filter_words": [re.compile(r"\b(felt|saw|noticed|realized|watched) that\b", re.I)],
    "telling_emotion": [re.compile(r"\b(sadness|loneliness|joy|warmth|happiness)\b", re.I)],
    "adverb_tags": [re.compile(r"\bsaid\s+\w+ly\b", re.I)],
    "purple_cliche": [re.compile(
        r"a shiver ran down|time seemed to stand still|a single tear|"
        r"heart skipped a beat", re.I)],
    # --- register-level structural tells ---
    "frozen_moment": [re.compile(
        r"the air (?:seemed to |)(?:freeze|thicken|grew still)|"
        r"the world (?:held its breath|seemed to stop|stood still)", re.I)],
    "vague_feeling": [re.compile(
        r"an? (?:odd|strange|inexplicable|unfamiliar|nameless|quiet) "
        r"(?:sense|feeling|sensation)|couldn'?t (?:quite )?(?:place|name|explain|shake)|"
        r"something (?:unspoken|unsaid|passed between)", re.I)],
}
_EN_PARA_INITIAL: dict[str, list[re.Pattern[str]]] = {
    "throat_clearing": [re.compile(r"^(However|Indeed|In fact|Honestly),", re.I)],
}
_EN_ENDING: dict[str, list[re.Pattern[str]]] = {
    "uplift_ending": [re.compile(
        r"In the end,|Little did (she|he|they) know|a testament to", re.I)],
}


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _finding(ftype: str, cliche_class: str, blocking: bool, detail: str,
             line: int | None = None) -> dict[str, Any]:
    return {
        "type": ftype,
        "cliche_class": cliche_class,
        "severity": "major" if blocking else "note",
        "blocking": blocking,
        "line": line,
        "detail": detail,
        "calibration": _CALIBRATION,
    }


def _paragraphs(text: str) -> list[tuple[str, int]]:
    """Return ``(paragraph_text, start_line)`` for each blank-line-separated block."""
    out: list[tuple[str, int]] = []
    idx = 0
    for part in re.split(r"(\n[ \t]*\n)", text):
        if part.strip() and not re.fullmatch(r"\n[ \t]*\n", part):
            out.append((part, _line_of(text, idx)))
        idx += len(part)
    return out


def check_style(
    text: str, card: dict[str, Any] | None = None, language: str = "zh"
) -> list[dict[str, Any]]:
    """Return anti-AI + voice findings for ``text`` given an optional voice ``card``.

    Cliché-table hits are NON-blocking ``ai_tell`` notes (model-seed). A declared
    ``forbidden_lexicon`` term present is a BLOCKING ``voice`` finding; a declared
    ``ai_tell_budget`` exceeded is a BLOCKING ``ai_tell`` finding; ``avoided_terms``
    are non-blocking ``voice`` notes. Absent a card, only the cliché notes apply
    (nothing blocks) — today's behavior is preserved.
    """
    card = card or {}
    findings: list[dict[str, Any]] = []
    if language == "en":
        fulltext, para_initial, ending = _EN_FULLTEXT, _EN_PARA_INITIAL, _EN_ENDING
    else:
        fulltext, para_initial, ending = _ZH_FULLTEXT, _ZH_PARA_INITIAL, _ZH_ENDING

    # 1. full-text cliché classes -> non-blocking ai_tell notes
    for cls, pats in fulltext.items():
        for pat in pats:
            for m in pat.finditer(text):
                findings.append(_finding(
                    "ai_tell", cls, False, m.group(0).strip()[:50], _line_of(text, m.start())))

    paras = _paragraphs(text)
    # 2. paragraph-initial throat-clearing / cheap pivots
    for cls, pats in para_initial.items():
        for para_text, line in paras:
            head = para_text.lstrip()
            for pat in pats:
                if pat.search(head):
                    findings.append(_finding("ai_tell", cls, False, head[:24].strip(), line))

    # 3. slogan / uplift ending — only the final paragraph
    if paras:
        last_text, last_line = paras[-1]
        for cls, pats in ending.items():
            for pat in pats:
                m = pat.search(last_text)
                if m:
                    findings.append(_finding(
                        "ai_tell", cls, False, m.group(0).strip()[:50], last_line))

    # 4. author-declared HARD forbidden lexicon -> BLOCKING voice
    for w in forbidden_lexicon(card):
        idx = text.find(w)
        if idx != -1:
            findings.append(_finding(
                "voice", "forbidden_lexicon", True,
                f"forbidden term {w!r} present", _line_of(text, idx)))

    # 5. author-declared soft avoided terms -> non-blocking voice
    for w in avoided_terms(card):
        idx = text.find(w)
        if idx != -1:
            findings.append(_finding(
                "voice", "avoided_term", False,
                f"avoided term {w!r} present", _line_of(text, idx)))

    # 6. declared ai_tell budget exceeded -> one BLOCKING ai_tell
    budget = ai_tell_budget(card)
    if budget is not None:
        n_chars = max(1, len(re.sub(r"\s", "", text)))
        n_hits = sum(1 for f in findings if f["type"] == "ai_tell")
        rate = n_hits * 1000.0 / n_chars
        if rate > budget:
            findings.append(_finding(
                "ai_tell", "ai_tell_budget", True,
                f"{n_hits} anti-AI hits = {rate:.2f}/1000 chars exceeds declared "
                f"budget {budget}"))
    return findings


def is_clean(text: str, card: dict[str, Any] | None = None, language: str = "zh") -> bool:
    """True iff ``text`` triggers NO blocking finding (non-blocking notes are OK)."""
    return not any(f["blocking"] for f in check_style(text, card, language))


__all__ = ["STYLE_LINT_TYPES", "check_style", "is_clean"]
