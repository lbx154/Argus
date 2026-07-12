"""WikiRouter — the structured front door to wiki CRUD, symmetric to SkillRouter.

Today wiki writes happen two ways: the reviewer executes the ``wiki-curator``
skill and free-hands ``WikiStore`` calls (no structured record, no gate, no
intent audit), and the mechanical post-mission hooks lift/promote pages. This
router adds the missing third path: a reviewer (or a learning mission) PROPOSES
structured ``wiki_ops`` and the harness validates + applies them, rebuilds the
indexes, and emits ``wiki.*`` events — the same shape as skill ``skill_ops``.

Three hard, mechanical guardrails (everything else is the reviewer's judgement):

  * evidence — a create_page/update_page's cited spans must quote the immutable
    source verbatim (anti-fabrication); fabricated citations are rejected;
  * independence — a page proposed under a NEW id must not be a near-duplicate
    of an EXISTING page. Judged ENTIRELY by an LLM over compact summaries
    (title + card_type + a short body excerpt — progressive disclosure,
    mirroring ``SkillRouter``'s duplicate judge). There is no lexical/scored
    fallback, and no silent fail-open: when a non-empty wiki needs the
    duplicate judge but no usable verdict is available, the proposal is
    rejected explicitly. A genuine revision of the SAME id (``update_page``,
    or a ``create_page`` that resolves to an id already on disk) is exempt —
    it is compared against nothing, never itself;
  * removals are tombstones (``retire_page``), never hard deletes, and reversible.

Best-effort: one bad op never breaks the others; the router never raises.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ..core.event_catalog import EventType
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from ..skills.provenance import verify_evidence
from .compaction import build_duplicate_check_prompt
from .index import rebuild_indexes
from .schema import PageCard, SourceNote
from .store import _PAGE_SUBDIR, WikiStore, _validate_stem

log = logging.getLogger(__name__)

EventSink = Callable[[dict], None]


class WikiRouter:
    """Owns validated, audited structured CRUD for one project wiki."""

    def __init__(
        self,
        wiki_root: "str | Path",
        *,
        retired_by: str = "reviewer",
        judge_runner: Any = None,
        judge_model: str = "",
        judge_reasoning_effort: str = "high",
    ) -> None:
        self.wiki_root = Path(wiki_root)
        self.store = WikiStore(self.wiki_root)
        self.retired_by = retired_by
        # LLM duplicate JUDGE (progressive disclosure over summaries) — the
        # ONLY independence check; never an approval authority over
        # well-formed, non-duplicate content.
        self.judge_runner = judge_runner
        self.judge_model = judge_model
        self.judge_reasoning_effort = judge_reasoning_effort

    def apply_ops(
        self,
        ops: list[dict],
        *,
        task: str = "",  # noqa: ARG002 — apply_ops keyword parity with SkillRouter.apply_ops
        on_event: EventSink | None = None,
        require_evidence: bool = False,
    ) -> dict[str, int]:
        """Apply reviewer-proposed ``wiki_ops``. ``require_evidence`` (set by the
        learning vertical) additionally rejects a page write that carries NO
        evidence at all; by default only FABRICATED evidence is rejected."""
        counts = {"sources": 0, "created": 0, "updated": 0, "retired": 0,
                  "skipped": 0, "rejected": 0}
        touched = False
        for op in ops or []:
            kind = (op.get("op") or "").strip().lower()
            try:
                if kind == "create_source":
                    r = self._create_source(op, on_event)
                    if r == "created":
                        counts["sources"] += 1
                        touched = True
                    elif r == "skipped":
                        counts["skipped"] += 1
                    else:
                        counts["rejected"] += 1
                elif kind in ("create_page", "update_page"):
                    verb = self._write_page(op, on_event, require_evidence=require_evidence)
                    if verb in ("created", "updated"):
                        counts[verb] += 1
                        touched = True
                    else:
                        counts["rejected"] += 1
                elif kind == "retire_page":
                    if self._retire_page(op, on_event):
                        counts["retired"] += 1
                        touched = True
                    else:
                        counts["rejected"] += 1
                else:
                    self._emit(on_event, {
                        "type": EventType.WIKI_OP_ERROR,
                        "operation": kind,
                        "error": "unknown operation",
                        "text": f"unknown wiki op: {kind!r}",
                    })
                    counts["rejected"] += 1
            except Exception as exc:  # noqa: BLE001 — one bad op never breaks the rest
                log.warning("wiki_op %s failed (%s: %s)", kind, type(exc).__name__, exc)
                self._emit(on_event, {
                    "type": EventType.WIKI_OP_ERROR,
                    "operation": kind,
                    "error": f"{type(exc).__name__}: {exc}",
                    "text": f"{kind} failed: {type(exc).__name__}",
                })
                counts["rejected"] += 1
        if touched:
            try:
                rebuild_indexes(self.store)
            except Exception:  # noqa: BLE001 — index maintenance must never break the caller
                log.debug("wiki index rebuild failed", exc_info=True)
        return counts

    # ------------------------------------------------------------------
    def _create_source(self, op: dict, on_event: EventSink | None) -> str:
        """Returns 'created', 'skipped' (benign immutable re-ingest), or 'error'."""
        sid = str(op.get("id") or "").strip()
        if not sid:
            self._emit(on_event, {
                "type": EventType.WIKI_OP_ERROR,
                "operation": "create_source",
                "error": "missing id",
                "text": "create_source: missing id",
            })
            return "error"
        note = SourceNote(
            id=sid,
            title=str(op.get("title") or sid).strip(),
            mission_id=str(op.get("mission_id") or ""),
            created_at=op.get("created_at") or date.today(),
            tags=list(op.get("tags") or []),
            body=str(op.get("body") or ""),
        )
        try:
            source_path = self.store.write_source(note)
        except FileExistsError:
            # Sources are immutable; re-ingesting the same material is a benign
            # no-op, NOT a rejection.
            self._emit(on_event, {
                "type": EventType.WIKI_SOURCE_SKIPPED,
                "source_id": sid,
                "reason": "already exists (immutable)",
                "text": f"source {sid} already exists (immutable)",
            })
            return "skipped"
        self._emit(on_event, {
            "type": EventType.WIKI_SOURCE_CREATED,
            "source_id": sid,
            "path": str(source_path),
            "text": f"source {sid}",
        })
        return "created"

    def _write_page(self, op: dict, on_event: EventSink | None,
                    *, require_evidence: bool) -> str:
        slug = str(op.get("id") or op.get("slug") or "").strip()
        if not slug:
            self._emit(on_event, {
                "type": EventType.WIKI_OP_ERROR,
                "operation": str(op.get("op") or "page_write"),
                "error": "missing id/slug",
                "text": "page write: missing id/slug",
            })
            return "rejected"
        evidence = op.get("evidence")
        # Anti-fabrication floor: any cited span must quote the immutable source.
        problems = verify_evidence(evidence, self.wiki_root)
        if problems:
            self._reject(on_event, op.get("op"), "; ".join(problems)[:400])
            return "rejected"
        if require_evidence and not evidence:
            self._reject(on_event, op.get("op"), "no evidence span for a learned page")
            return "rejected"
        card_type = str(op.get("card_type") or "technique").strip()
        existed = self._page_path(card_type, slug).exists()
        if not existed:
            # Independence floor: a NEW id must not be a near-duplicate of an
            # EXISTING page. A revision of the SAME id (``existed`` above) is
            # exempt — it is compared against nothing, never itself. LLM
            # judge ONLY (progressive disclosure) — no scored/lexical fallback,
            # and no silent pass-through when the judge is unavailable.
            title = str(op.get("title") or slug)
            body = str(op.get("body") or "")
            try:
                verdict = self._llm_duplicate_check(
                    title=title, body=body, card_type=card_type
                )
            except RuntimeError as exc:
                self._reject(on_event, op.get("op"), str(exc))
                return "rejected"
            if verdict is not None:
                is_dup, near, why = verdict
                if is_dup:
                    self._reject(on_event, op.get("op"),
                                 f"too similar to '{near}' (llm judge: {why})")
                    return "rejected"
        try:
            card = self._build_page(op, slug=slug, card_type=card_type)
            self.store.write_page(card)
        except (KeyError, ValueError) as exc:
            self._reject(on_event, op.get("op"), f"malformed page: {exc}")
            return "rejected"
        verb = "updated" if existed else "created"
        self._emit(on_event, {
            "type": EventType.WIKI_UPDATED if existed else EventType.WIKI_CREATED,
            "page_id": card.id,
            "card_type": card_type,
            "title": card.title,
            "status": card.status,
            "path": str(self._page_path(card_type, card.id)),
            "text": f"{verb} {card_type}/{card.id}",
        })
        return verb

    def _llm_duplicate_check(
        self, *, title: str, body: str, card_type: str,
    ) -> tuple[bool, str, str] | None:
        """Ask the judge runner whether the proposal duplicates an existing
        page, over COMPACT SUMMARIES only (progressive disclosure). Returns
        ``(is_duplicate, matched_title, why)``, or ``None`` when no judge
        comparison is needed. Missing or unusable judge infrastructure RAISES
        so the caller can reject the proposal explicitly; there is no
        scored/lexical fallback and no silent fail-open."""
        pages = self.store.iter_pages()
        if not pages:
            return False, "", "wiki is empty"
        if self.judge_runner is None:
            raise RuntimeError(
                "duplicate judge unavailable: configure a healthy judge_runner "
                "before create_page against a non-empty wiki"
            )
        prompt = build_duplicate_check_prompt(
            title=title, body=body, card_type=card_type, existing_pages=pages,
        )
        try:
            result = gateway_run_exec(
                self.judge_runner,
                prompt=prompt,
                options=RunnerOptions(
                    model=self.judge_model or None,
                    reasoning_effort=self.judge_reasoning_effort,
                    skip_git_repo_check=True,
                    full_auto=True,
                ),
                run_label="wiki.duplicate_check",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki duplicate judge failed (%s: %s)", type(exc).__name__, exc)
            raise RuntimeError(
                f"duplicate judge failed: {type(exc).__name__}: {exc}"
            ) from exc
        text = (getattr(result, "last_agent_message", "") or "").strip()
        if not text:
            raise RuntimeError("duplicate judge returned no text")
        try:
            left, right = text.find("{"), text.rfind("}")
            parsed = json.loads(text[left:right + 1]) if left >= 0 < right else json.loads(text)
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError("duplicate judge returned malformed JSON") from None
        if not isinstance(parsed, dict):
            raise RuntimeError("duplicate judge returned a non-object verdict")
        is_dup = bool(parsed.get("duplicate"))
        of_title = str(parsed.get("of", "") or "").strip()
        why = str(parsed.get("why", "") or "").strip()[:300]
        if is_dup and not of_title:
            raise RuntimeError(
                "duplicate judge returned duplicate=true without naming the "
                "existing conflicting page"
            )
        return is_dup, of_title, why

    def _retire_page(self, op: dict, on_event: EventSink | None) -> bool:
        slug = str(op.get("id") or op.get("slug") or "").strip()
        card_type = str(op.get("card_type") or "technique").strip()
        if not slug:
            self._emit(on_event, {
                "type": EventType.WIKI_OP_ERROR,
                "operation": "retire_page",
                "error": "missing id/slug",
                "text": "retire_page: missing id/slug",
            })
            return False
        reason = str(op.get("why") or op.get("rationale") or "").strip()
        try:
            self.store.retire_page(card_type, slug, reason=reason, retired_by=self.retired_by)
        except FileNotFoundError:
            self._emit(on_event, {
                "type": EventType.WIKI_OP_ERROR,
                "operation": "retire_page",
                "page_id": slug,
                "card_type": card_type,
                "error": "page not found",
                "text": f"retire: page {card_type}/{slug} not found",
            })
            return False
        except (KeyError, ValueError) as exc:
            self._emit(on_event, {
                "type": EventType.WIKI_OP_ERROR,
                "operation": "retire_page",
                "page_id": slug,
                "card_type": card_type,
                "error": f"{type(exc).__name__}: {exc}",
                "text": f"retire: {exc}",
            })
            return False
        self._emit(on_event, {
            "type": EventType.WIKI_RETIRED,
            "page_id": slug,
            "card_type": card_type,
            "reason": reason,
            "text": f"retired {card_type}/{slug}"
            + (f": {reason}" if reason else ""),
        })
        return True

    # ------------------------------------------------------------------
    def _build_page(self, op: dict, *, slug: str, card_type: str) -> PageCard:
        today = date.today()
        sources = [
            e.get("source_id")
            for e in (op.get("evidence") or [])
            if isinstance(e, dict) and e.get("source_id")
        ]
        return PageCard(
            id=slug,
            type=card_type,
            status=str(op.get("status") or "scratch").strip(),
            title=str(op.get("title") or slug).strip(),
            tags=list(op.get("tags") or []),
            sources=list(dict.fromkeys(str(s) for s in sources)),
            related_runs=list(op.get("related_runs") or []),
            related_projects=list(op.get("related_projects") or []),
            revisit_after=None,
            created_at=today,
            last_reviewed_at=today,
            reviewer_note=str(op.get("why") or ""),
            body=str(op.get("body") or ""),
        )

    def _page_path(self, card_type: str, slug: str) -> Path:
        return self.wiki_root / "pages" / _PAGE_SUBDIR[card_type] / f"{_validate_stem(slug)}.md"

    def _reject(self, on_event: EventSink | None, kind: Any, why: str) -> None:
        self._emit(on_event, {
            "type": EventType.WIKI_OP_REJECTED,
            "operation": str(kind or ""),
            "reason": why,
            "text": f"rejected {kind}: {why}",
        })

    @staticmethod
    def _emit(on_event: EventSink | None, event: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 — telemetry must never break the loop
            log.debug("wiki router emit failed", exc_info=True)


__all__ = ["WikiRouter"]
