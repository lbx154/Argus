"""Unified ``argus-skill query <text>`` — trajectory + skills + wiki.

A single query returns three layers of evidence in one shot:

1. **trajectory** — raw rows from past codex/argus JSONL via FTS5
   (``argus_skill.tools.trajectory_index``).
2. **skills** — curated skill cards whose name/summary/intent text
   matches via the existing BM25 prefilter
   (``argus_skill.skills.bm25_prefilter``).
3. **wiki** — markdown pages under any project's
   ``.autors/<slug>/wiki/pages/`` whose body contains the query terms.

Used by:

* engineer in a tight retry loop (``argus-skill query "overfull hbox"``
  to find the past trajectory + the skill card + the wiki page in one
  call, before grinding another round on the same blocker);
* reviewer post-mission to seed wiki promotion (FTS5 evidence count
  flips scratch -> candidate without needing reviewer recall);
* operators on the cron-snapshot path who want a fast "did we hit this
  before" check without running the full daemon status report.

The function returns a structured dict so callers (CLI / hooks / tests)
can render it however they like; the CLI wrapper formats a tight
human-readable summary.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from ..skills.bm25_prefilter import bm25_prefilter, bm25_tokens
from .trajectory_index import (
    TrajectoryHit,
    default_db_path,
    index_all,
    search_trajectories,
)


@dataclass(frozen=True)
class SkillHit:
    name: str
    path: str
    summary: str
    score: float


@dataclass(frozen=True)
class WikiHit:
    project: str
    page: str
    path: str
    snippet: str


def _load_skill_summaries(skills_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not skills_dir.exists():
        return out
    for md in sorted(skills_dir.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        name = md.stem
        summary = ""
        intent = ""
        # parse frontmatter if present
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                front = text[4:end]
                for line in front.splitlines():
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("summary:") or line.startswith("description:"):
                        summary = line.split(":", 1)[1].strip()
                    elif line.startswith("intent:"):
                        intent = line.split(":", 1)[1].strip()
        # Field names align with the matcher-visible BM25 fields.
        out.append({
            "name": name,
            "description": summary or intent,
            "category": "",
            "path": str(md),
            "summary": summary,
        })
    return out


def _bm25_skills(query: str, summaries: list[dict[str, Any]], top_k: int = 5) -> list[SkillHit]:
    if not summaries:
        return []
    # The matcher-facing BM25 fallback intentionally returns the full pool on
    # no-overlap for recall. The standalone query tool has no LLM second pass, so
    # keep only skills that share at least one BM25 token before ranking.
    q_tokens = set(bm25_tokens(query))
    if not q_tokens:
        return []
    candidate: list[dict[str, Any]] = []
    for s in summaries:
        blob = " ".join(str(s.get(k, "")) for k in ("name", "description", "category", "summary"))
        toks = set(bm25_tokens(blob))
        if q_tokens & toks:
            candidate.append(s)
    if not candidate:
        return []
    ranked = bm25_prefilter(query, candidate, top_k=top_k)
    out: list[SkillHit] = []
    n = max(1, len(ranked))
    for i, s in enumerate(ranked):
        out.append(SkillHit(
            name=str(s.get("name") or ""),
            path=str(s.get("path") or ""),
            summary=str(s.get("summary") or s.get("description") or "")[:200],
            score=round(1.0 - i / n, 3),
        ))
    return out


_SNIPPET_WIN = 80


def _wiki_snippet(text: str, terms: list[str]) -> str:
    lower = text.lower()
    for t in terms:
        idx = lower.find(t.lower())
        if idx != -1:
            start = max(0, idx - _SNIPPET_WIN // 2)
            end = min(len(text), idx + _SNIPPET_WIN)
            snip = text[start:end].replace("\n", " ").strip()
            return ("…" if start > 0 else "") + snip + ("…" if end < len(text) else "")
    return text[:_SNIPPET_WIN].replace("\n", " ").strip()


def _scan_wiki(query: str, search_roots: Iterable[Path], top_k: int = 5) -> list[WikiHit]:
    terms = [t.lower() for t in re.findall(r"[\w][\w\-]*", query) if t]
    if not terms:
        return []
    hits: list[tuple[int, WikiHit]] = []
    for root in search_roots:
        if not root.exists():
            continue
        for autors in root.rglob(".autors"):
            if not autors.is_dir():
                continue
            for project_dir in autors.iterdir():
                if not project_dir.is_dir():
                    continue
                pages_dir = project_dir / "wiki" / "pages"
                if not pages_dir.exists():
                    continue
                for md in pages_dir.rglob("*.md"):
                    try:
                        text = md.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    lower = text.lower()
                    score = sum(lower.count(t) for t in terms)
                    if score == 0:
                        continue
                    hits.append((score, WikiHit(
                        project=project_dir.name,
                        page=md.relative_to(pages_dir).as_posix(),
                        path=str(md),
                        snippet=_wiki_snippet(text, terms),
                    )))
    hits.sort(key=lambda x: -x[0])
    return [h for _, h in hits[:top_k]]


def unified_query(
    query: str,
    *,
    skills_dir: Path | None = None,
    wiki_search_roots: Iterable[Path] | None = None,
    db_path: Path | None = None,
    auto_index: bool = True,
    top_k: int = 5,
) -> dict[str, Any]:
    """Run a unified query across trajectory / skills / wiki.

    Returns ``{"query": q, "trajectory": [...], "skills": [...],
    "wiki": [...], "stats": {...}}``.
    """
    db_path = db_path or default_db_path()
    stats: dict[str, Any] = {}
    if auto_index:
        try:
            stats["index"] = index_all(db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            stats["index_error"] = repr(exc)

    traj: list[TrajectoryHit] = search_trajectories(query, limit=top_k, db_path=db_path)

    # skill layer
    skills_dir = skills_dir or _resolve_default_skills_dir()
    summaries = _load_skill_summaries(skills_dir) if skills_dir else []
    skills = _bm25_skills(query, summaries, top_k=top_k)

    # wiki layer — scan a small set of likely roots
    if wiki_search_roots is None:
        wiki_search_roots = _default_wiki_roots()
    wiki = _scan_wiki(query, wiki_search_roots, top_k=top_k)

    return {
        "query": query,
        "trajectory": [asdict(h) for h in traj],
        "skills": [asdict(h) for h in skills],
        "wiki": [asdict(h) for h in wiki],
        "stats": stats,
    }


def _resolve_default_skills_dir() -> Path | None:
    from ..core.paths import resolve_runtime_path, shared_skills_root

    env = os.environ.get("ARGUS_SKILL_SKILLS_DIR")
    if env:
        return resolve_runtime_path(env, context="ARGUS_SKILL_SKILLS_DIR")
    candidate = shared_skills_root()
    return candidate if candidate.exists() else None


def _default_wiki_roots() -> list[Path]:
    roots: list[Path] = [Path.cwd()]
    env = os.environ.get("ARGUS_SKILL_WIKI_ROOTS")
    if env:
        for p in env.split(":"):
            if p.strip():
                roots.append(Path(p.strip()))
    return roots


# ---------------------------------------------------------------------------
# Human-readable rendering
# ---------------------------------------------------------------------------


def render_text(result: dict[str, Any]) -> str:
    out: list[str] = []
    q = result.get("query") or ""
    out.append(f"argus-skill query: {q!r}")
    idx = (result.get("stats") or {}).get("index") or {}
    if idx:
        out.append(
            f"  index: {idx.get('rows_total', 0)} rows total "
            f"({idx.get('files_scanned', 0)} files re-scanned this run)"
        )
    traj = result.get("trajectory") or []
    out.append(f"\n[trajectory] {len(traj)} hit(s)")
    for h in traj:
        sid = h.get("session_id") or "?"
        ts = h.get("ts") or ""
        kind = h.get("kind") or ""
        text = (h.get("text") or "").replace("\n", " ")[:160]
        out.append(f"  {h.get('source','?'):<16} {sid[:24]:<24} {kind:<14} {ts[:19]:<19} {text}")
    skills = result.get("skills") or []
    out.append(f"\n[skills] {len(skills)} match(es)")
    for s in skills:
        out.append(f"  {s.get('name','?'):<40} score={s.get('score',0):.2f}  {s.get('summary','')[:80]}")
    wiki = result.get("wiki") or []
    out.append(f"\n[wiki] {len(wiki)} page(s)")
    for w in wiki:
        out.append(f"  {w.get('project','?'):<24} {w.get('page','?'):<50} {w.get('snippet','')[:80]}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Standalone entry: ``python -m argus_skill.tools.query_unified <text>``."""
    import argparse

    p = argparse.ArgumentParser(prog="argus-skill query")
    p.add_argument("query", nargs="+", help="search text (whitespace-joined)")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    p.add_argument("--no-index", action="store_true", help="skip incremental re-index")
    args = p.parse_args(argv)
    q = " ".join(args.query)
    result = unified_query(q, top_k=args.top_k, auto_index=not args.no_index)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
