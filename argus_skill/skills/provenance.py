"""Evidence-span provenance verification (anti-fabrication, mechanical).

A learned skill or wiki claim must cite a span into the IMMUTABLE material stored
in the wiki ``sources/`` layer. This module mechanically checks that each cited
quote actually appears — verbatim, modulo whitespace — in the referenced source.

It makes NO judgement about *sufficiency* (is the evidence enough to justify the
claim?) — that is the reviewer's call. It only catches FABRICATED citations: a
quote that is simply not in the source at all. That is the one anti-fraud floor
the harness is entitled to enforce; everything else is the agent's judgement.
"""
from __future__ import annotations

from pathlib import Path


def _norm(text: str) -> str:
    """Whitespace-normalize for a forgiving verbatim match: collapse runs of
    whitespace (a PDF/markdown extractor may reflow lines) and lowercase."""
    return " ".join((text or "").split()).lower()


def _source_text(wiki_root: Path, source_id: str) -> str | None:
    """Return the raw text of the immutable source named by ``source_id``.

    Resolution is deterministic and honors the type/sub-dir prefix so distinct
    sources that share a filename stem do not collide:

    * ``"papers/grpo"`` resolves ONLY under ``sources/papers/grpo.md``;
    * a bare ``"grpo"`` matches ``sources/**/grpo.md`` — but ONLY if exactly one
      such file exists; an ambiguous bare stem returns ``None`` (treated as
      unresolved) rather than silently picking one, since a wrong pick would
      corrupt the anti-fraud check in either direction.
    """
    sid = str(source_id).strip()
    sources = Path(wiki_root) / "sources"
    if not sid or not sources.exists():
        return None
    if "/" in sid:
        # Explicit subdir/type prefix — resolve exactly there, no fallback.
        cand = sources / f"{sid}.md"
        if not cand.exists():
            return None
        try:
            return cand.read_text(encoding="utf-8")
        except OSError:
            return None
    matches = sorted(sources.rglob(f"{sid}.md"))
    if len(matches) != 1:  # missing, or ambiguous across sub-dirs
        return None
    try:
        return matches[0].read_text(encoding="utf-8")
    except OSError:
        return None


def verify_evidence(evidence, wiki_root) -> list[str]:
    """Return a list of human-readable problems for evidence spans whose quote is
    NOT found verbatim (whitespace-normalized) in the referenced immutable source.

    An empty list means every span checks out. Each span is a mapping with at
    least ``source_id`` and ``quote`` (``locator`` is advisory). Callers decide
    whether *absence* of evidence is itself a problem — this function only judges
    the spans it is given.
    """
    problems: list[str] = []
    for span in evidence or []:
        if not isinstance(span, dict):
            problems.append(f"malformed evidence span: {span!r}")
            continue
        sid = str(span.get("source_id") or "").strip()
        quote = str(span.get("quote") or "").strip()
        if not sid or not quote:
            problems.append(
                f"incomplete evidence span (need source_id + quote): {span!r}"
            )
            continue
        text = _source_text(wiki_root, sid)
        if text is None:
            problems.append(f"source not found for evidence: {sid!r}")
        elif _norm(quote) not in _norm(text):
            snippet = quote[:60] + ("…" if len(quote) > 60 else "")
            problems.append(
                f"quote not found verbatim in source {sid!r}: {snippet!r}"
            )
    return problems


__all__ = ["verify_evidence"]
