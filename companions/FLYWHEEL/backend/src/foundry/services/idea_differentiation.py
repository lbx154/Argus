"""Deterministic lexical differentiation aid for refreshed research sources.

This is deliberately a triage heuristic. It never emits a novelty percentage
and never claims that absence of lexical overlap proves novelty.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

_STOP = {
    "about", "after", "against", "based", "between", "from", "into", "method",
    "model", "paper", "research", "study", "that", "their", "these", "this",
    "using", "with", "一种", "一个", "以及", "可以", "基于", "方法", "模型", "研究",
}


@dataclass(frozen=True)
class NearestSource:
    item_id: str
    title: str
    url: str
    overlap_terms: tuple[str, ...]


@dataclass(frozen=True)
class IdeaDelta:
    novelty_risk: str
    heuristic_notice: str
    overlap_terms: tuple[str, ...]
    nearest_items: tuple[NearestSource, ...]
    changed_since_snapshot: Mapping[str, tuple[str, ...]]
    change_basis: str
    differentiation_summary: str
    suggested_refresh_prompt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def differentiate_idea(
    idea: Mapping[str, Any],
    source_items: Sequence[Mapping[str, Any]],
    *,
    previous_items: Sequence[Mapping[str, Any]] = (),
    observed_changes: Mapping[str, Sequence[str]] | None = None,
    nearest_limit: int = 5,
) -> IdeaDelta:
    idea_text = " ".join(str(idea.get(key) or "") for key in (
        "title", "title_zh", "problem_gap", "mechanism_hypothesis", "core_hypothesis",
        "method", "method_seed",
    ))
    idea_terms = _terms(idea_text)
    mechanism_terms = _terms(" ".join(str(idea.get(key) or "") for key in (
        "mechanism_hypothesis", "core_hypothesis", "method", "method_seed",
    )))
    ranked: list[tuple[int, int, NearestSource]] = []
    all_overlap: set[str] = set()
    for item in source_items:
        metadata = item.get("metadata") or {}
        abstract = metadata.get("abstract", "") if isinstance(metadata, Mapping) else ""
        item_terms = _terms(f"{item.get('title', '')} {abstract}")
        shared = tuple(sorted(idea_terms & item_terms))
        if not shared:
            continue
        all_overlap.update(shared)
        mechanism_count = len(mechanism_terms & item_terms)
        ranked.append((mechanism_count, len(shared), NearestSource(
            item_id=str(item.get("item_id") or item.get("id") or ""),
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            overlap_terms=shared,
        )))
    ranked.sort(key=lambda row: (-row[0], -row[1], row[2].item_id))
    nearest = tuple(row[2] for row in ranked[:nearest_limit])
    if not source_items:
        risk = "coverage_insufficient"
        summary = "没有来源条目，无法进行差异判断。"
    elif ranked and ranked[0][0] >= 1 and ranked[0][1] >= 3:
        risk = "high_collision_risk"
        summary = "至少一个最近条目与候选机制存在多项词汇重叠；必须进行机制级人工 collision 审核。"
    elif ranked:
        risk = "needs_human_review"
        summary = "发现词汇重叠，但词面相似不能判断机制是否相同。"
    else:
        risk = "no_obvious_lexical_collision"
        summary = "当前快照未发现明显词汇碰撞；这不构成新颖性证明。"
    if observed_changes is None:
        old = {_item_id(item): _signature(item) for item in previous_items if _item_id(item)}
        new = {_item_id(item): _signature(item) for item in source_items if _item_id(item)}
        changes = {
            "added": tuple(sorted(new.keys() - old.keys())),
            "removed": tuple(sorted(old.keys() - new.keys())),
            "changed": tuple(sorted(
                key for key in new.keys() & old.keys() if new[key] != old[key]
            )),
        }
        change_basis = "provided_snapshot_comparison"
    else:
        # Source adapters compare each successful refresh against their own
        # prior cache.  Preserve those observations instead of inferring a
        # delta by comparing the current aggregate with an unavailable prior
        # aggregate (which would falsely label every current item as added).
        changes = {
            key: tuple(sorted({
                str(item_id)
                for item_id in observed_changes.get(key, ())
                if str(item_id)
            }))
            for key in ("added", "removed", "changed")
        }
        change_basis = "adapter_source_updates"
    nearest_ids = ", ".join(item.item_id for item in nearest) or "无"
    refresh_prompt = (
        "重新审核该 idea 的 NEAREST_WORK_MATRIX。只使用来源快照中的一手条目；"
        f"优先逐项比较 {nearest_ids} 的机制、主张、setting 与证据。"
        f"本次新增 {len(changes['added'])}、移除 {len(changes['removed'])}、变化 {len(changes['changed'])} 条。"
        "如果同机制同主张已被覆盖，输出 NOVELTY_COLLISION；否则明确写出差异和仍未覆盖的检索盲区。"
    )
    return IdeaDelta(
        novelty_risk=risk,
        heuristic_notice="Deterministic lexical triage only; not a novelty score or proof.",
        overlap_terms=tuple(sorted(all_overlap)),
        nearest_items=nearest,
        changed_since_snapshot=changes,
        change_basis=change_basis,
        differentiation_summary=summary,
        suggested_refresh_prompt=refresh_prompt,
    )


def _item_id(item: Mapping[str, Any]) -> str:
    return str(item.get("item_id") or item.get("id") or "")


def _signature(item: Mapping[str, Any]) -> tuple[str, str]:
    metadata = item.get("metadata") or {}
    abstract = metadata.get("abstract", "") if isinstance(metadata, Mapping) else ""
    return str(item.get("title") or ""), str(abstract)


def _terms(text: str) -> set[str]:
    lowered = text.casefold()
    ascii_terms = {
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", lowered)
        if token not in _STOP
    }
    chinese: set[str] = set()
    for sequence in re.findall(r"[\u3400-\u9fff]+", lowered):
        if len(sequence) <= 2:
            chinese.add(sequence)
        else:
            chinese.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return {term for term in ascii_terms | chinese if term and term not in _STOP}
