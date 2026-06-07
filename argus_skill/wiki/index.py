"""Regenerate queries/*.md from page frontmatter.

queries/ is a derived layer; it is never the source of truth and is safe
to delete and rebuild from scratch.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

from .schema import PageCard
from .store import WikiStore, _atomic_write_text


def rebuild_indexes(store: WikiStore, *, today: date | None = None) -> None:
    today = today or date.today()
    pages = store.iter_pages()
    qroot = store.root / "queries"
    rendered = {
        "by-status.md": _render_by_status(pages),
        "by-tag.md": _render_by_tag(pages),
        "stale-watchlist.md": _render_stale_watchlist(pages, today),
        "open-contradictions.md": _render_open_contradictions(pages),
    }
    qroot.mkdir(parents=True, exist_ok=True)
    for name, body in rendered.items():
        _atomic_write_text(qroot / name, body)


def _render_by_status(pages: Iterable[PageCard]) -> str:
    buckets: dict[str, list[PageCard]] = defaultdict(list)
    for p in pages:
        buckets[p.status].append(p)
    out = ["# Cards by status\n"]
    for status in ("stable", "candidate", "scratch"):
        out.append(f"\n## {status}\n")
        for p in sorted(buckets.get(status, []), key=lambda c: c.id):
            out.append(f"- `{p.type}/{p.id}` -- {p.title}\n")
    return "".join(out)


def _render_by_tag(pages: Iterable[PageCard]) -> str:
    buckets: dict[str, list[PageCard]] = defaultdict(list)
    for p in pages:
        for tag in p.tags or ["<untagged>"]:
            buckets[tag].append(p)
    out = ["# Cards by tag\n"]
    for tag in sorted(buckets):
        out.append(f"\n## {tag}\n")
        for p in sorted(buckets[tag], key=lambda c: c.id):
            out.append(f"- `{p.type}/{p.id}` -- {p.title} ({p.status})\n")
    return "".join(out)


def _render_stale_watchlist(pages: Iterable[PageCard], today: date) -> str:
    stale = [
        p
        for p in pages
        if p.type == "technique"
        and p.status in ("candidate", "stable")
        and p.revisit_after is not None
        and p.revisit_after < today
    ]
    stale.sort(key=lambda c: c.revisit_after or date.max)
    out = ["# Stale watchlist (revisit_after < today)\n\n"]
    if not stale:
        out.append("_None._\n")
        return "".join(out)
    for p in stale:
        out.append(
            f"- `{p.id}` -- {p.title} (status={p.status}, "
            f"due {p.revisit_after.isoformat() if p.revisit_after else 'unknown'})\n"
        )
    return "".join(out)


def _render_open_contradictions(pages: Iterable[PageCard]) -> str:
    open_c = [
        p
        for p in pages
        if p.type == "conflict" and p.status in ("candidate", "stable")
    ]
    open_c.sort(key=lambda c: c.id)
    out = ["# Open contradictions\n\n"]
    if not open_c:
        out.append("_None._\n")
        return "".join(out)
    for p in open_c:
        out.append(f"- `{p.id}` -- {p.title} (status={p.status})\n")
    return "".join(out)
