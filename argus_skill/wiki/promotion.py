"""Mechanical promotion/demotion of wiki pages based on RunCard references.

Diagnosis
---------

The reviewer-side wiki-curator playbook says ``scratch -> candidate``
should happen when *"this mission found additional evidence (a second
source supporting the same technique, a run that exercises it)"*. In
practice the reviewer makes the call subjectively and (per the v1
project's 9 mission closes) ~never promotes — every page card stays at
``scratch`` forever.

This module replaces the subjective call with a mechanical rule based
on observed reuse, matching the consensus design of high-star 2025/2026
agent-memory frameworks:

* **EverMind-AI/EverOS** (~7k stars, Apache-2.0) — *"Common skills are
  extracted from real usage; repeated patterns become reusable
  workflows."* Promotion is by observed repetition, not LLM judgement.
* **mem0 v3** (~30k stars) — memories accumulate, scoring happens at
  read-time via multi-signal retrieval, not at write-time.
* **SkillEvolBench** (arXiv:2605.24117, May 2026) empirically warns:
  "Raw-trajectory reuse frequently outperforms distilled skills... extra
  updates can cause episode-specific drift and procedural clutter."
  Mechanical promotion keeps us conservative.

Rules
-----

For each ``pages/*/<slug>.md``, count distinct ``sources/runs/*.md``
files whose body mentions ``<slug>`` (case-insensitive substring). The
RunCard is the "trace" the EverOS doc talks about — a reference IS
genuine evidence the page card got reused, not just authored.

Transitions:

* ``scratch -> candidate``   when references >= 2
* ``candidate -> stable``    when references >= 3 AND at least 2 of
  those referencing missions had ``success=True``
* ``stable    -> candidate`` (demote) when references >= 2 AND >= 2 of
  those referencing missions had ``success=False`` (the skill was tried
  but failed twice — drop confidence)
* ``candidate -> scratch``   (demote) on same condition

Mission success is read from the RunCard body — every RunCard the
engineer/reviewer writes per ``wiki-curator.md`` Step 5 has a
``outcome:`` line we parse. Missing outcome defaults to "unknown" and
does NOT count toward either side.

Concurrency: the wiki store's ``_wiki_lock`` is held across the entire
promote pass so two daemons cannot race.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml

from .store import WikiStore

log = logging.getLogger(__name__)

EventSink = Callable[[dict], None] | None

# Promotion thresholds. Conservative on purpose — per SkillEvolBench,
# over-promotion causes "procedural clutter".
_PROMOTE_TO_CANDIDATE_MIN = 2  # ≥ this many distinct RunCard refs
_PROMOTE_TO_STABLE_MIN = 3
_PROMOTE_TO_STABLE_SUCCESSES = 2  # of which ≥ this had outcome=success
_DEMOTE_FAILURES = 2  # ≥ this many failure refs triggers demotion


_OUTCOME_RE = re.compile(r"^\s*outcome\s*:\s*(\w+)", re.IGNORECASE | re.MULTILINE)


def _split_frontmatter(text: str) -> tuple[dict, str] | None:
    """Return (frontmatter_dict, body_text) or None if no parseable FM.

    Intentionally tolerant: we only need to read/write a few fields, not
    validate the full PageCard schema.
    """
    if not text.startswith("---\n"):
        return None
    _, _, rest = text.partition("---\n")
    front_text, sep, body = rest.partition("\n---\n")
    if not sep:
        return None
    try:
        fm = yaml.safe_load(front_text) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm, body


def _join_frontmatter(fm: dict, body: str) -> str:
    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n{body}"


def _parse_run_outcome(run_path: Path) -> str:
    """Read a RunCard, return its outcome ('success'/'failure'/'unknown')."""
    try:
        text = run_path.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    split = _split_frontmatter(text)
    if split is not None:
        fm, _ = split
        val = str(fm.get("outcome") or fm.get("status") or "").lower()
        normalised = {
            "done": "success",
            "blocked": "failure",
            "continue": "unknown",
        }.get(val, val)
        if normalised in {"success", "failure", "partial"}:
            return normalised
    m = _OUTCOME_RE.search(text)
    if m:
        val = m.group(1).lower()
        normalised = {
            "done": "success",
            "blocked": "failure",
            "continue": "unknown",
        }.get(val, val)
        if normalised in {"success", "failure", "partial"}:
            return normalised
    return "unknown"


def _index_run_references(wiki_root: Path) -> dict[str, list[tuple[str, str]]]:
    """For every page slug, find which RunCards reference it.

    Returns ``{page_slug: [(run_id, outcome), ...]}``. Matching is
    case-insensitive substring of the slug in the RunCard body — the
    same loose rule the curator playbook implicitly uses when it links
    runs to patterns/techniques.
    """
    runs_dir = wiki_root / "sources" / "runs"
    pages_dir = wiki_root / "pages"
    if not runs_dir.exists() or not pages_dir.exists():
        return {}

    page_slugs = [p.stem for p in pages_dir.rglob("*.md")]
    refs: dict[str, list[tuple[str, str]]] = {slug: [] for slug in page_slugs}

    for run_path in sorted(runs_dir.glob("*.md")):
        try:
            body = run_path.read_text(encoding="utf-8").lower()
        except OSError:
            continue
        outcome = _parse_run_outcome(run_path)
        for slug in page_slugs:
            if slug.lower() in body:
                refs[slug].append((run_path.stem, outcome))
    return refs


def _decide_transition(
    current_status: str,
    n_refs: int,
    n_success: int,
    n_failure: int,
) -> str | None:
    """Return the new status, or None if no change."""
    if current_status == "scratch":
        if n_refs >= _PROMOTE_TO_CANDIDATE_MIN:
            return "candidate"
        return None
    if current_status == "candidate":
        # Failure demote takes priority over promote
        if n_failure >= _DEMOTE_FAILURES:
            return "scratch"
        if (
            n_refs >= _PROMOTE_TO_STABLE_MIN
            and n_success >= _PROMOTE_TO_STABLE_SUCCESSES
        ):
            return "stable"
        return None
    if current_status == "stable":
        if n_failure >= _DEMOTE_FAILURES:
            return "candidate"
        return None
    return None


def _apply_transition(
    page_path: Path,
    new_status: str,
    *,
    related_run_ids: list[str],
    note_suffix: str,
) -> bool:
    """Rewrite a page card with the new status. Returns True on change."""
    text = page_path.read_text(encoding="utf-8")
    split = _split_frontmatter(text)
    if split is None:
        return False
    fm, body = split
    if fm.get("status") == new_status:
        return False
    fm["status"] = new_status
    fm["last_reviewed_at"] = date.today().isoformat()
    existing = list(fm.get("related_runs") or [])
    for r in related_run_ids:
        ref = f"runs/{r}.md"
        if ref not in existing:
            existing.append(ref)
    fm["related_runs"] = existing
    prior_note = (fm.get("reviewer_note") or "").rstrip()
    suffix = f"\n[auto-promotion @ {date.today().isoformat()}] {note_suffix}"
    fm["reviewer_note"] = (prior_note + suffix).lstrip()
    page_path.write_text(_join_frontmatter(fm, body), encoding="utf-8")
    return True


def mechanical_promote(
    wiki_root: Path,
    *,
    emit: EventSink = None,
) -> dict:
    """Scan a wiki, run mechanical promotion/demotion. Returns summary dict.

    Safe to run on every mission close. Fail-open: errors are logged and
    skipped, never raised.
    """
    summary = {"promoted": 0, "demoted": 0, "unchanged": 0, "errors": 0}
    try:
        store = WikiStore(wiki_root)
        with store._wiki_lock():
            refs_by_slug = _index_run_references(wiki_root)
            for kind_dir in ("techniques", "conflicts", "patterns"):
                kdir = wiki_root / "pages" / kind_dir
                if not kdir.exists():
                    continue
                for page_path in sorted(kdir.glob("*.md")):
                    slug = page_path.stem
                    refs = refs_by_slug.get(slug, [])
                    n_refs = len(refs)
                    n_success = sum(1 for _, o in refs if o == "success")
                    n_failure = sum(1 for _, o in refs if o == "failure")
                    try:
                        text = page_path.read_text(encoding="utf-8")
                        split = _split_frontmatter(text)
                        if split is None:
                            summary["errors"] += 1
                            continue
                        fm, _ = split
                        cur = fm.get("status", "scratch")
                        new = _decide_transition(cur, n_refs, n_success, n_failure)
                        if new is None:
                            summary["unchanged"] += 1
                            continue
                        note = (
                            f"{cur}→{new} "
                            f"(refs={n_refs}, success={n_success}, "
                            f"failure={n_failure})"
                        )
                        ok = _apply_transition(
                            page_path,
                            new,
                            related_run_ids=[r for r, _ in refs],
                            note_suffix=note,
                        )
                        if ok:
                            direction = (
                                "promoted"
                                if {"scratch": 0, "candidate": 1, "stable": 2}[new]
                                > {"scratch": 0, "candidate": 1, "stable": 2}[cur]
                                else "demoted"
                            )
                            summary[direction] += 1
                            if emit is not None:
                                try:
                                    emit({
                                        "type": f"wiki.promotion.{direction}",
                                        "text": f"{kind_dir}/{slug}: {note}",
                                    })
                                except Exception:  # noqa: BLE001
                                    log.debug("emit failed", exc_info=True)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("promote %s failed: %s: %s",
                                    page_path, type(exc).__name__, exc)
                        summary["errors"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("mechanical_promote failed for %s: %s",
                    wiki_root, exc)
        summary["errors"] += 1
    return summary


__all__ = ["mechanical_promote"]
