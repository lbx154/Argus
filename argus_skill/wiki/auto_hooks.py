"""Post-mission wiki harness hooks — auto-run wiki-curator mechanics.

Diagnosis (this fixes a real observed bug)
-----------------------------------------

The reviewer-side ``wiki-curator.md`` skill is injected as **text** into
the reviewer prompt. Its "Step 0 backfill" and "Step 2 mechanical lift"
sections say *"always do this"* — but the LLM reviewer frequently
skips them when its judgement is busy with run-stage iteration. The
result observed across 9 mission closes in the agent-multimodal-
reasoning-v1 project: every mission writes its ``sources/runs/mission-N.md``
RunCard, but ``sources/papers/`` and ``pages/techniques/scratch`` cards
stay at zero growth despite the engineer's ``paper/refs.bib`` accruing
14 new BibTeX entries.

The fix
-------

Move the parts of wiki-curator that are **deterministic and side-effect
free** (per-spec "mechanical, always do this") out of the LLM-prompt
playbook and into a Python hook that the harness calls unconditionally
at every mission close. The LLM-judgement parts (when to write a
``conflict`` card, when to promote ``candidate→stable``, what
``reviewer_note`` to write) stay in the playbook — the reviewer remains
in charge of judgement, the harness only owns the mechanical pieces the
reviewer was specced to do but reliably skipped.

What this module does on every mission close
-------------------------------------------

1. **Discover** all ``.autors/<project>/wiki/`` under the workdir.
2. For each wiki:
   a. **Backfill sources** from ``paper/refs.bib`` (BibTeX → sources/papers/)
      and ``research/LIT_MATRIX.tsv`` (per-row relevance line).
   b. **Mechanical scratch lift**: for every ``sources/papers/<key>.md``
      that has no matching ``pages/techniques/<key>.md``, create a
      ``status: scratch`` placeholder card the reviewer can later judge.
   c. **Rebuild queries/** indexes.
3. Fail-open: any error becomes a warning event, never blocks the
   mission verdict (matches the curator skill's "Recovery policy").

Tag system note
---------------

The mechanical scratch cards intentionally use **no tags** — the
controlled vocab in ``data/tags.yaml`` is small at first and the
reviewer is supposed to add tags as concepts recur (per wiki design).
Letting the harness guess tags would flood the vocab; an empty
``tags: []`` is the explicit "I don't know yet" marker.

Design references
-----------------

* The "always-do-this mechanical" / "judgement-required" split is
  literally how ``builtin_skills/reviewer/wiki-curator.md`` Step 2 is
  written. This module moves the first half into the harness.
* EverMind-AI/EverOS (Apache-2.0): "Common skills are extracted from
  real usage; repeated patterns become reusable workflows" — the
  promotion criterion (in ``promotion.py``) follows that mechanical
  rule instead of LLM judgement.
* SkillEvolBench (arXiv:2605.24117): "Raw-trajectory reuse frequently
  outperforms distilled skills" — we therefore prioritise keeping the
  RunCard (raw trajectory) always written and treat ``pages/*`` as
  *additional* synthesis, never as a substitute for raw evidence.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType

log = logging.getLogger(__name__)

EventSink = Callable[[dict], None] | None


# Reviewer-only judgement cards we never write mechanically.
# patterns/* needs the reviewer to compare failure signatures.
# conflicts/* needs the reviewer to identify inverted claims.
_MECHANICAL_PAGE_KIND = "techniques"


def discover_wikis(workdir: Path) -> list[Path]:
    """Return ``.autors/<project>/wiki/`` dirs that have been initialised.

    A wiki counts as initialised when ``query_pack.md`` exists at its
    root — matches the planner-side discovery in ``planner/planner.py``.
    """
    autors = workdir / ".autors"
    if not autors.exists():
        return []
    out: list[Path] = []
    for child in sorted(autors.iterdir()):
        wiki = child / "wiki"
        if (wiki / "query_pack.md").exists():
            out.append(wiki)
    return out


def _safe(fn: Callable[[], object], *, what: str, emit: EventSink) -> object | None:
    """Run ``fn``; on exception emit a warning event and return ``None``.

    Wiki maintenance MUST NOT block a mission verdict. This matches the
    curator skill's "Recovery policy": backfill warnings are isolated.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki auto-hook %s failed: %s: %s",
                    what, type(exc).__name__, exc)
        if emit is not None:
            try:
                emit({
                    "type": EventType.WIKI_HOOK_WARNING,
                    "operation": what,
                    "error": f"{type(exc).__name__}: {exc}",
                    "text": f"{what}: {type(exc).__name__}: {exc}",
                })
            except Exception:  # noqa: BLE001
                log.exception("wiki hook emit failed")
        return None


def _ingest_sources(
    wiki_root: Path,
    *,
    source_root: Path,
    mission_id: str,
    emit: EventSink,
) -> int:
    """Step 0 backfill: import paper/refs.bib + LIT_MATRIX.tsv if present.

    Returns count of new sources/papers/*.md files written.
    """
    from .ingest import ingest_lit_matrix, ingest_refs_bib
    from .store import WikiStore

    store = WikiStore(wiki_root)
    written = 0

    refs_bib = source_root / "paper" / "refs.bib"
    if refs_bib.exists():
        r = _safe(
            lambda: ingest_refs_bib(
                store,
                bib_path=refs_bib,
                ingested_by=f"wiki-auto-hook@mission-{mission_id}",
            ),
            what="ingest_refs_bib",
            emit=emit,
        )
        if r is not None:
            written += len(getattr(r, "written", []) or [])

    lit_matrix = source_root / "research" / "LIT_MATRIX.tsv"
    if lit_matrix.exists():
        _safe(
            lambda: ingest_lit_matrix(store, tsv_path=lit_matrix),
            what="ingest_lit_matrix",
            emit=emit,
        )

    return written


def _write_run_source(
    wiki_root: Path,
    *,
    mission_id: str,
    source_id: str | None = None,
    task: str,
    success: bool,
    rounds: list[Any] | None,
    emit: EventSink,
    context_packet_path: Path | None = None,
    checkpoint_path: Path | None = None,
) -> int:
    """Persist one immutable reviewer-grounded RunCard at mission close."""
    _ = task
    if not rounds:
        return 0
    if source_id is None and any(
        (wiki_root / "sources" / "runs").glob(f"{mission_id}-r*.md")
    ):
        return 0
    review = next(
        (
            getattr(record, "review", None)
            for record in reversed(rounds)
            if getattr(record, "review", None) is not None
        ),
        None,
    )
    if review is None:
        return 0
    if getattr(review, "backend_unavailable", False):
        return 0

    artifacts: dict[str, str] = {}
    if checkpoint_path is not None:
        artifacts[str(checkpoint_path)] = "canonical durable state"
    if context_packet_path is not None:
        context_path = Path(context_packet_path)
        artifacts[str(context_path.parent / "latest.json")] = (
            "canonical machine handoff"
        )
    outcome = "success" if success else "failure"
    body_parts = [f"Reviewer verdict: {getattr(review, 'status', '')}"]
    if checkpoint_path is not None:
        body_parts.append(f"Durable state: {checkpoint_path}")
    if context_packet_path is not None:
        context_path = Path(context_packet_path)
        body_parts.append(
            f"Structured handoff: {context_path.parent / 'latest.json'}"
        )

    from .schema import SourceRun
    from .store import WikiStore

    source = SourceRun(
        id=f"runs/{source_id or mission_id}",
        mission_id=mission_id,
        git_commit="",
        project=wiki_root.parent.name,
        config_path="",
        dataset="",
        metrics={},
        artifacts=artifacts,
        outcome=outcome,
        failure_signature=(
            str(getattr(review, "reason", "") or "").strip()[:1000]
            if not success
            else ""
        ),
        suspected_cause="",
        next_action=str(getattr(review, "next_action", "") or "").strip()[:2000],
        body="\n\n".join(body_parts),
        closed_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        path = WikiStore(wiki_root).write_source(source)
    except FileExistsError:
        return 0
    except Exception as exc:  # noqa: BLE001 - wiki maintenance is fail-open
        log.warning(
            "wiki run-source write failed for %s (%s: %s)",
            mission_id,
            type(exc).__name__,
            exc,
        )
        if emit is not None:
            try:
                emit({
                    "type": EventType.WIKI_HOOK_WARNING,
                    "operation": "write_run_source",
                    "mission_id": mission_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "text": f"write_run_source: {type(exc).__name__}: {exc}",
                })
            except Exception:  # noqa: BLE001
                log.debug("wiki run-source warning emit failed", exc_info=True)
        return 0
    if emit is not None:
        try:
            emit({
                "type": EventType.WIKI_SOURCE_CREATED,
                "source_id": source.id,
                "path": str(path),
                "mission_id": mission_id,
                "text": f"recorded immutable wiki run source {source.id}",
            })
        except Exception:  # noqa: BLE001
            log.debug("wiki run-source emit failed", exc_info=True)
    return 1


def _mechanical_scratch_lift(wiki_root: Path, *, mission_id: str, emit: EventSink) -> int:
    """Step 2 mechanical: for every sources/papers/<key>.md without a
    matching pages/techniques/<key>.md, create a scratch technique card.

    Returns count of new scratch cards written.
    """
    papers_dir = wiki_root / "sources" / "papers"
    tech_dir = wiki_root / "pages" / "techniques"
    if not papers_dir.exists():
        return 0
    tech_dir.mkdir(parents=True, exist_ok=True)

    from .schema import PageCard
    from .store import WikiStore
    store = WikiStore(wiki_root)
    today = date.today()
    written = 0

    for paper_path in sorted(papers_dir.glob("*.md")):
        key = paper_path.stem
        tech_path = tech_dir / f"{key}.md"
        if tech_path.exists():
            continue
        # Pull title + first relevance line from paper source body, if any.
        title = key
        reviewer_note = ""
        try:
            text = paper_path.read_text(encoding="utf-8")
            # Frontmatter title takes priority over file name.
            for line in text.splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"').strip("'")
                    break
            for line in text.splitlines():
                if line.lower().startswith("relevance"):
                    reviewer_note = line.split(":", 1)[-1].strip()
                    break
        except OSError:
            pass

        card = PageCard(
            id=key,
            type="technique",
            status="scratch",
            title=title or key,
            tags=[],  # reviewer adds tags later; do not flood vocab
            sources=[f"papers/{key}.md"],
            related_runs=[],
            related_projects=[],
            revisit_after=None,
            created_at=today,
            last_reviewed_at=today,
            reviewer_note=reviewer_note,
            body=(
                f"_Auto-created by wiki-auto-hook at mission-{mission_id}._\n"
                "_Reviewer: replace this body with your synthesis, or promote/"
                "demote per the curator playbook._\n"
            ),
        )

        ok = _safe(lambda: store.write_page(card),
                   what=f"write_page {key}", emit=emit)
        if ok is not None:
            written += 1

    return written


def _rebuild_indexes(wiki_root: Path, *, emit: EventSink) -> None:
    from .index import rebuild_indexes
    from .store import WikiStore
    store = WikiStore(wiki_root)
    _safe(lambda: rebuild_indexes(store), what="rebuild_indexes", emit=emit)


def run_post_mission_hooks(
    workdir: Path,
    *,
    mission_id: str,
    success: bool,
    task: str = "",
    rounds: list[Any] | None = None,
    emit: EventSink = None,
    context_packet_path: Path | None = None,
    checkpoint_path: Path | None = None,
) -> dict:
    """Run all wiki auto-hooks after a mission close.

    Returns a summary dict ``{wiki_path: {sources_written, scratch_written}}``
    so callers can log a single line summary. Never raises.

    The same mechanics run on both success and failure because reviewed raw
    trajectories remain useful evidence; ``success`` classifies the immutable
    RunCard outcome rather than gating capture.
    """
    summary: dict = {}
    wikis = discover_wikis(workdir)
    if not wikis:
        return summary
    for wiki_root in wikis:
        run_written = _write_run_source(
            wiki_root,
            mission_id=mission_id,
            task=task,
            success=success,
            rounds=rounds,
            emit=emit,
            context_packet_path=context_packet_path,
            checkpoint_path=checkpoint_path,
        )
        paper_sources_written = _ingest_sources(
            wiki_root,
            source_root=workdir,
            mission_id=mission_id,
            emit=emit,
        )
        s_written = run_written + paper_sources_written
        t_written = _mechanical_scratch_lift(
            wiki_root, mission_id=mission_id, emit=emit
        )
        _rebuild_indexes(wiki_root, emit=emit)
        summary[str(wiki_root)] = {
            "sources_written": s_written,
            "scratch_written": t_written,
            "success": success,
        }
        if emit is not None and (s_written or t_written):
            try:
                emit({
                    "type": EventType.WIKI_HOOK_OK,
                    "project": wiki_root.parent.name,
                    "path": str(wiki_root),
                    "sources_written": s_written,
                    "scratch_written": t_written,
                    "text": (
                        f"{wiki_root.parent.name}: "
                        f"+{s_written} sources, +{t_written} scratch cards"
                    ),
                })
            except Exception:  # noqa: BLE001
                log.debug("wiki hook ok emit failed", exc_info=True)
    return summary


__all__ = [
    "discover_wikis",
    "run_post_mission_hooks",
]
