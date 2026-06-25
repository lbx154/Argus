"""Skill-store compaction — merge near-duplicate playbooks.

Why: as the daemon runs, near-identical skills accumulate (same family,
slightly different framing). The matcher then has to score them all
on every match (~14k tokens per call × N candidates). Without
compaction the matcher cost grows linearly forever.

Approach:

  1. Cluster skills by a structure-aware similarity over title intent,
     category, description, and ``When to use``.
     Two skills join the same cluster when sim ≥ ``sim_threshold``.
  2. For each cluster of size ≥ 2 we pick a representative (highest
     ``version`` × ``len(task_history)`` heuristic — the most-reinforced)
     and ``archive`` the rest via ``lifecycle.archive_skill``.
  3. Optionally write a short ``merged-into:`` breadcrumb on the
     representative so we can audit later.

We deliberately avoid LLM-based merging in v1: it's expensive,
non-deterministic, and the merged playbook would still need a
quality-gate pass. Picking the strongest existing skill and
archiving the rest already removes the matcher-cost tax and keeps the
proven content. v2 can add a author-mediated "merge & rewrite"
pass behind ``--smart``.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_SIM_THRESHOLD = 0.55
MIN_CLUSTER_SIZE = 2


@dataclass
class CompactionPlan:
    clusters: list[list[Any]] = field(default_factory=list)
    keep: list[Any] = field(default_factory=list)
    archive: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "argus-skill — compaction plan",
            "-" * 50,
            f"clusters: {len(self.clusters)}    keep: {len(self.keep)}    "
            f"archive: {len(self.archive)}",
            "",
        ]
        for idx, cluster in enumerate(self.clusters, 1):
            names = [getattr(s, "name", "?") for s in cluster]
            keep = next(
                (getattr(s, "name", "?") for s in cluster if s in self.keep), "?"
            )
            lines.append(f"cluster {idx}: {len(cluster)} skills")
            lines.append(f"   keep    : {keep}")
            archived = [n for n in names if n != keep]
            for n in archived:
                lines.append(f"   archive : {n}")
        if self.notes:
            lines.append("")
            for note in self.notes:
                lines.append(f"note: {note}")
        return "\n".join(lines)


def _tokenize(text: str) -> Counter:
    return Counter(_token_list(text))


def _token_list(text: str) -> list[str]:
    text = (text or "").lower().replace("_", " ").replace("-", " ")
    return [t for t in re.findall(r"[a-z0-9]+", text) if len(t) >= 3]


def _normalize_phrase(text: str) -> str:
    return " ".join(_token_list(text))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[k] * b[k] for k in common)
    if num == 0:
        return 0.0
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)



def _section_body(content: str, heading: str) -> str:
    pattern = (
        rf"(?ims)^\s*#{{1,6}}\s+{re.escape(heading)}\s*$"
        r"(.*?)"
        rf"(?=^\s*#{{1,6}}\s+\S|\Z)"
    )
    m = re.search(pattern, content or "")
    return m.group(1) if m else ""


def _skill_profile(skill: Any) -> dict[str, Any]:
    content = getattr(skill, "content", "") or ""
    return {
        "title": _tokenize(getattr(skill, "name", "")),
        "description": _tokenize(getattr(skill, "description", "")),
        "when_to_use": _tokenize(_section_body(content, "When to use")),
        "category": _normalize_phrase(getattr(skill, "category", "")),
    }


def _pair_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    title = _cosine(left["title"], right["title"])
    description = _cosine(left["description"], right["description"])
    when_to_use = _cosine(left["when_to_use"], right["when_to_use"])
    category = 1.0 if left["category"] and left["category"] == right["category"] else 0.0

    score = (
        0.45 * title
        + 0.15 * description
        + 0.15 * when_to_use
        + 0.25 * category
    )
    # If the categories do not line up, the title has to carry some of
    # the intent on its own; otherwise generic scaffolding text can drown
    # out the real difference between two distinct skills.
    if not category and title < 0.6:
        score -= 0.1
    return score


def _cluster(
    skills: list[Any], sim_threshold: float
) -> list[list[Any]]:
    """Group skills by a structure-aware similarity over skill intent."""
    profiles = [_skill_profile(s) for s in skills]
    assigned = [-1] * len(skills)
    next_id = 0
    for i in range(len(skills)):
        if assigned[i] != -1:
            continue
        assigned[i] = next_id
        for j in range(i + 1, len(skills)):
            if assigned[j] != -1:
                continue
            if _pair_similarity(profiles[i], profiles[j]) >= sim_threshold:
                assigned[j] = next_id
        next_id += 1
    clusters: list[list[Any]] = [[] for _ in range(next_id)]
    for idx, cid in enumerate(assigned):
        clusters[cid].append(skills[idx])
    return clusters


def _representative(cluster: list[Any]) -> Any:
    def score(s: Any) -> tuple[int, int, str]:
        version = int(getattr(s, "version", 1) or 1)
        history = len(getattr(s, "task_history", []) or [])
        # Prefer (version * (1+history)) — proven skills win ties.
        return (-(version * (1 + history)), -history, getattr(s, "name", ""))
    return sorted(cluster, key=score)[0]


def plan_compaction(
    skills: list[Any], *, sim_threshold: float = DEFAULT_SIM_THRESHOLD,
) -> CompactionPlan:
    plan = CompactionPlan()
    clusters = _cluster(skills, sim_threshold)
    interesting = [c for c in clusters if len(c) >= MIN_CLUSTER_SIZE]
    plan.clusters = interesting
    for cluster in interesting:
        rep = _representative(cluster)
        plan.keep.append(rep)
        for s in cluster:
            if s is not rep:
                plan.archive.append(s)
    if not interesting:
        plan.notes.append(
            f"no clusters with size >= {MIN_CLUSTER_SIZE} at "
            f"sim_threshold={sim_threshold}"
        )
    return plan


def execute_plan(
    plan: CompactionPlan, *, archive_skill_fn: Any
) -> dict[str, Any]:
    """Execute the plan; return a result dict with archived paths."""
    archived: list[str] = []
    errors: list[str] = []
    for s in plan.archive:
        path = getattr(s, "path", None)
        if not path:
            errors.append(f"{getattr(s, 'name', '?')}: no path on skill")
            continue
        try:
            target = archive_skill_fn(path)
            if target is None:
                errors.append(f"{getattr(s, 'name', '?')}: source missing")
            else:
                archived.append(str(target))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{getattr(s, 'name', '?')}: {type(exc).__name__}: {exc}")
    return {"archived": archived, "errors": errors}


def run_compact(
    skills_dir: Path,
    *,
    sim_threshold: float = DEFAULT_SIM_THRESHOLD,
    dry_run: bool = True,
    as_json: bool = False,
) -> int:
    """CLI entry point. Returns shell exit code."""
    import json as _json

    skills_dir = Path(skills_dir)
    if not skills_dir.is_dir():
        print("compact: no skill files found")
        return 0
    from .lifecycle import archive_skill
    from .store import SkillStore

    store = SkillStore(skills_dir=skills_dir)
    summaries = store.list_summaries()
    if not summaries:
        print("compact: no skill files found")
        return 0
    skills = [store.load(s["path"]) for s in summaries]
    plan = plan_compaction(skills, sim_threshold=sim_threshold)
    if as_json and dry_run:
        rendered = {
            "clusters": [[getattr(s, "name", "?") for s in c]
                         for c in plan.clusters],
            "keep": [getattr(s, "name", "?") for s in plan.keep],
            "archive": [getattr(s, "name", "?") for s in plan.archive],
            "notes": plan.notes,
        }
        print(_json.dumps(rendered, ensure_ascii=False, indent=2))
        return 0
    print(plan.render())
    if dry_run:
        if plan.archive:
            print("\n(dry-run: pass --apply to actually archive)")
        return 0
    if not plan.archive:
        return 0
    result = execute_plan(plan, archive_skill_fn=archive_skill)
    print("\n=== applied ===")
    print(f"archived files: {len(result['archived'])}")
    for line in result["archived"]:
        print(f"  -> {line}")
    if result["errors"]:
        print(f"errors: {len(result['errors'])}")
        for err in result["errors"]:
            print(f"  !! {err}")
    return 0 if not result["errors"] else 1


__all__ = [
    "CompactionPlan",
    "plan_compaction",
    "execute_plan",
    "run_compact",
    "DEFAULT_SIM_THRESHOLD",
]
