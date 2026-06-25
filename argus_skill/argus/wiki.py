"""argus.wiki — VK / external-knowledge base, in the project folder.

Holds (a) Research findings (every task's first stage is Research; a dedicated
Research skill runs often and feeds useful findings here) and (b) external knowledge
fetched via Search. The raw Trace is NOT stored here.
"""
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Callable, Optional


class Wiki:
    def __init__(self, project_root: Path):
        self.root = Path(project_root) / "wiki"
        (self.root / "research").mkdir(parents=True, exist_ok=True)
        (self.root / "external").mkdir(parents=True, exist_ok=True)

    def add_research(self, topic: str, finding: str) -> Path:
        p = self.root / "research" / f"{_slug(topic)}.md"
        p.write_text(f"# {topic}\n\n{finding}\n", encoding="utf-8")
        return p

    def add_external(self, source: str, content: str) -> Path:
        p = self.root / "external" / f"{_slug(source)}.md"
        p.write_text(f"# {source}\n\n{content}\n", encoding="utf-8")
        return p

    def search_in(self, query: str) -> list[str]:
        q = query.lower()
        hits = []
        for p in self.root.rglob("*.md"):
            t = p.read_text(encoding="utf-8")
            if q in t.lower():
                hits.append(str(p.relative_to(self.root)))
        return hits


def research(wiki: Wiki, topic: str, search_fn: Optional[Callable[[str], str]] = None) -> str:
    """The dedicated Research step: fetch external knowledge, file useful bits into the wiki.
    `search_fn` is the real searcher (arxiv/web); stub returns a placeholder."""
    found = (search_fn(topic) if search_fn else f"(no external search) notes on {topic}")
    wiki.add_research(topic, found)
    return found


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower())[:60].strip("-") or "x"
