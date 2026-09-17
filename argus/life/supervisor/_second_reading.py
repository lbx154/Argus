"""Commission a second reading of the evidence when doubt has accumulated.

The Reviewer can say, round after round, that the current line of work should
be reconsidered, and the Planner can keep scheduling the next variant of the
same idea. This mixin notices the pattern in the research vertical's
Experiment stage and, once the doubt has repeated and enough time has passed,
asks the Manager to read the evidence afresh with one question in mind: what
claim does it actually support? The answer is placed at the top of the
research notes and handed to the Planner as a revision request, so that the
rest of the project is re-planned around what the evidence shows and the tasks
that belonged to the refuted idea are let go.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ...core.event_catalog import EventType

log = logging.getLogger(__name__)


class SecondReadingMixin:
    def _maybe_second_reading(self, outcome: dict[str, Any]) -> dict[str, Any] | None:
        """Return a revision request carrying a fresh reading, or ``None``."""
        try:
            return self._second_reading_or_none(outcome)
        except Exception:  # noqa: BLE001 - a reading must never stop the campaign
            log.exception("second reading: failed; continuing without it")
            return None

    def _second_reading_or_none(self, outcome: dict[str, Any]) -> dict[str, Any] | None:
        from ...skills.vertical_select import resolve_vertical
        from ...verticals.research_bridge import (
            build_second_reading_prompt,
            insert_second_reading_into_notes,
            note_reconsider_signal,
            parse_second_reading,
            record_second_reading,
            second_reading_due,
        )

        state_root = self._artifact_root()
        if resolve_vertical(state_root) != "research":
            return None
        stage = str(self._current_pipeline_stage() or "").strip().lower()
        if stage != "experiment":
            return None
        report = outcome.get("planner_report")
        report = report if isinstance(report, dict) else {}
        prior = outcome.get("plan_challenge")
        prior = dict(prior) if isinstance(prior, dict) else {}
        signal = str(report.get("plan_signal") or "").strip().lower()
        if signal != "reconsider" and not prior.get("challenge"):
            return None
        signals = note_reconsider_signal(state_root)
        if not second_reading_due(state_root):
            return None

        project_root = self._project_workdir()
        from ...verticals.research_bridge import read_research_notes

        notes = read_research_notes(project_root)
        objective = str(getattr(self.config, "continuous_objective", "") or "").strip()
        if not objective:
            try:
                objective = (project_root / "OBJECTIVE.md").read_text(encoding="utf-8")
            except OSError:
                objective = ""
        pending: list[tuple[str, str]] = []
        try:
            for item in self.memory.backlog.history():
                if str(getattr(item, "status", "") or "") == "pending":
                    pending.append((str(item.id), str(item.title)))
        except Exception:  # noqa: BLE001
            pass
        reviews: list[str] = []
        try:
            for entry in self.memory.journal.tail_settlements(6):
                summary = str(getattr(entry, "summary", "") or "").strip()
                if summary:
                    reviews.append(summary[:600])
        except Exception:  # noqa: BLE001
            pass
        challenge = str(
            prior.get("challenge")
            or report.get("challenge")
            or outcome.get("review_reason")
            or ""
        )
        alternative = str(
            prior.get("alternative") or report.get("alternative") or ""
        )
        prompt = build_second_reading_prompt(
            objective=objective,
            stage=stage,
            notes=notes,
            challenge=challenge,
            alternative=alternative,
            pending_tasks=pending,
            recent_reviews=reviews,
        )
        self._emit_status(
            "The Reviewer has asked more than once to reconsider this line; "
            "commissioning a second reading of the evidence"
        )
        text = self._bound_manager().write_prose(
            prompt,
            on_event=getattr(self.sink, "handle_event", None),
            run_label="manager-second-reading",
        )
        if not str(text or "").strip():
            self._emit_status("The second reading returned nothing; continuing without it")
            return None
        parsed = parse_second_reading(text)
        now = time.time()
        notes_path = insert_second_reading_into_notes(
            project_root,
            body=parsed["body"],
            supported=parsed["supported"],
            next_step=parsed["next"],
            when=now,
        )
        record_second_reading(state_root, now=now)
        self._emit({
            "type": EventType.LIFE_RESEARCH_SECOND_READING,
            "agent_layer": "manager",
            "stage": stage,
            "refuted": parsed["refuted"],
            "supported": parsed["supported"],
            "next": parsed["next"],
            "text": parsed["body"],
            "notes_path": str(notes_path),
            "signals": int(signals),
        })
        self._emit_status(
            "A second reading of the evidence was added to the research notes; "
            "re-planning around it"
        )
        refuted = parsed["refuted"].strip()
        supported = parsed["supported"].strip()
        pieces = []
        if refuted and refuted.lower() not in {"nothing", "nothing yet", "none"}:
            pieces.append(f"The evidence has refuted: {refuted}")
        if supported:
            pieces.append(f"What it supports today: {supported}")
        if not pieces:
            pieces.append(challenge or "A second reading asks for the plan to follow the evidence")
        revised = dict(outcome)
        revised["plan_challenge"] = {
            "manager_action": str(prior.get("manager_action") or "revise"),
            "manager_reason": (
                "A second reading of the evidence, now at the top of the research "
                "notes, asks the plan to follow what the evidence supports"
            ),
            "challenge": " ".join(pieces),
            "alternative": parsed["next"].strip() or alternative,
            "authority_impact": "technical",
            "source": "second_reading",
            "raised_at": now,
            "adjudicated_at": now,
            "revision_latency_seconds": 0.0,
        }
        return revised


__all__ = ["SecondReadingMixin"]
