"""fiction_writing genre PROFILES — data that really changes the plan/rubric.

Loop 10. A profile is NOT a new vertical and it does NOT bypass any contract: a
fiction mission under any profile still goes through the same Task Envelope,
Review, Artifact and Provenance gates and the same story_state patch engine. What
a profile DOES is tune the *creative* knobs — pacing, chapter hooks, exposition
tolerance, character complexity, thematic explicitness, ending strategy — and
carry a per-profile set of REVIEWER EMPHASES into the brief, so the reviewer's
guidance genuinely differs between, say, web_fiction and literary_fiction.

The profile is resolved at intake and recorded in the creative_brief; an UNKNOWN
profile is rejected at the intake gate (so a typo can't silently fall through).
Whether the prose actually honors the pacing is, as always, live-reviewer — the
deterministic part is: the profile is known, its config is distinct, and it is
consumed by the brief + reviewer banner.
"""
from __future__ import annotations

from typing import Any

#: The default when the operator names no profile.
DEFAULT_PROFILE = "genre_fiction"

#: The known fiction genre profiles and their creative knobs. The values are
#: deliberately DISTINCT across profiles so a test can prove the profile changes
#: the rubric rather than being an ignored data file.
FICTION_PROFILES: dict[str, dict[str, Any]] = {
    "web_fiction": {
        "pacing": "fast",
        "chapter_hooks": "required",
        "exposition_tolerance": "high",
        "character_complexity": "medium",
        "thematic_explicitness": "medium",
        "ending_strategy": "cliffhanger_ok",
        "reviewer_emphasis": [
            "每章结尾留钩子", "节奏快、信息密度高", "少大段静态铺垫"],
    },
    "genre_fiction": {
        "pacing": "medium",
        "chapter_hooks": "preferred",
        "exposition_tolerance": "medium",
        "character_complexity": "medium",
        "thematic_explicitness": "low",
        "ending_strategy": "resolved",
        "reviewer_emphasis": [
            "类型套路扎实", "冲突线清晰", "结尾收束核心悬念"],
    },
    "literary_fiction": {
        "pacing": "slow",
        "chapter_hooks": "optional",
        "exposition_tolerance": "low",
        "character_complexity": "high",
        "thematic_explicitness": "implicit",
        "ending_strategy": "open_ok",
        "reviewer_emphasis": [
            "人物复杂度/内在矛盾", "主题隐含、避免说教", "语言密度", "克制铺陈与升华"],
    },
    "short_story": {
        "pacing": "tight",
        "chapter_hooks": "n/a",
        "exposition_tolerance": "low",
        "character_complexity": "focused",
        "thematic_explicitness": "implicit",
        "ending_strategy": "single_turn",
        "reviewer_emphasis": [
            "单一转折/落点", "开场即入戏", "无冗余场景"],
    },
    "long_form_serial": {
        "pacing": "sustained",
        "chapter_hooks": "required",
        "exposition_tolerance": "high",
        "character_complexity": "ensemble",
        "thematic_explicitness": "medium",
        "ending_strategy": "arc_continues",
        "reviewer_emphasis": [
            "长线伏笔管理", "多线并行推进", "每章推进+钩子", "避免中段拖沓"],
    },
}


class FictionProfileError(ValueError):
    """Raised when an unknown genre profile is requested."""


def resolve_profile(name: str | None) -> dict[str, Any]:
    """Return the resolved profile config for ``name``.

    ``None``/empty -> the default profile. A named-but-unknown profile is REJECTED
    (never silently defaulted) so a typo fails loudly at the intake gate. The
    returned dict includes the profile ``name`` plus its creative knobs.
    """
    key = (name or "").strip() or DEFAULT_PROFILE
    if key not in FICTION_PROFILES:
        raise FictionProfileError(
            f"unknown fiction profile {key!r} (known: {sorted(FICTION_PROFILES)})"
        )
    return {"name": key, **FICTION_PROFILES[key]}


__all__ = [
    "DEFAULT_PROFILE",
    "FICTION_PROFILES",
    "FictionProfileError",
    "resolve_profile",
]
