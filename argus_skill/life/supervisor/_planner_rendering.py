"""Planner context and reviewer-feedback rendering mixin."""

from __future__ import annotations


class PlannerRenderingMixin:
    def _item_iteration_cycles(self) -> int:
        """Default iteration cycles for planner-generated tasks."""
        try:
            return max(1, int(self.config.planner_task_iteration_max_cycles))
        except (TypeError, ValueError):
            return 6

    def _item_iteration_budget(self) -> float:
        """Default iteration budget for planner-generated tasks."""
        try:
            return max(0.0, float(self.config.planner_task_iteration_budget_usd))
        except (TypeError, ValueError):
            return 30.0

    def _render_journal_for_planner(self) -> str:
        """Render recent event-backed history for the planner's context."""
        try:
            entries = self.memory.journal.tail(20)
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            from datetime import datetime
            ts = datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M")
            line = f"- [{ts}] {e.kind}: {e.title} — {e.summary}"
            extra = getattr(e, "extra", {}) or {}
            if isinstance(extra, dict):
                if extra.get("final_submission_certified"):
                    evidence = str(extra.get("completion_summary") or "").strip()
                    if evidence:
                        line += f" | final-submission evidence: {evidence[:500]}"
                if e.kind in (
                    "mission_complete",
                    "mission_failed",
                    "mission_replan_requested",
                ):
                    # Surface the L2 reviewer's own structured briefing so the
                    # planner attends to *what actually happened*, not just the
                    # `status=done` field. A mission can be marked done by being
                    # waved through a blocked/rollback/allowed-failure gate
                    # without resolving the underlying blocker; this report lets
                    # the planner avoid re-dispatching no-progress missions.
                    report = extra.get("planner_report")
                    if isinstance(report, dict) and report:
                        rendered = self._render_planner_report(report)
                        if rendered:
                            line += "\n" + rendered
                    feedback = extra.get("checklist_feedback")
                    if isinstance(feedback, dict) and feedback:
                        rendered_fb = self._render_checklist_feedback(feedback)
                        if rendered_fb:
                            line += "\n" + rendered_fb
                    step_back = extra.get("step_back")
                    if isinstance(step_back, dict) and step_back:
                        rendered_sb = self._render_step_back(step_back)
                        if rendered_sb:
                            line += "\n" + rendered_sb
                    claim_synthesis = extra.get("claim_synthesis")
                    if isinstance(claim_synthesis, dict) and claim_synthesis:
                        rendered_claim = self._render_claim_synthesis(claim_synthesis)
                        if rendered_claim:
                            line += "\n" + rendered_claim
            lines.append(line)
        return "\n".join(lines) or "(empty)"

    @staticmethod
    def _render_planner_report(report: dict) -> str:
        """Render the reviewer-authored planner briefing as plain lines.

        The reviewer authors a clean structured object; we only select and
        truncate its fields, never reformat free text or strip logs (the
        reviewer is instructed to emit clean content). Returns "" when the
        object carries no usable signal.
        """
        def _clean(value: object, limit: int) -> str:
            return str(value or "").strip()[:limit]

        forward = report.get("forward_progress")
        headline = _clean(report.get("headline"), 600)
        blocker = _clean(report.get("blocker"), 1200)
        recommended = _clean(report.get("recommended_next"), 1200)
        parts: list[str] = []
        if isinstance(forward, bool):
            parts.append(f"    reviewer→planner: forward_progress={forward}")
        if headline:
            parts.append(f"    headline: {headline}")
        if blocker:
            parts.append(f"    blocker: {blocker}")
        if recommended:
            parts.append(f"    recommended_next: {recommended}")
        evidence = report.get("evidence_files")
        if isinstance(evidence, list) and evidence:
            parts.append("    evidence_files the planner MUST open before replanning:")
            for entry in evidence[:8]:
                if not isinstance(entry, dict):
                    continue
                path = _clean(entry.get("path"), 400)
                if not path:
                    continue
                why = _clean(entry.get("why"), 600)
                parts.append(f"      - {path}" + (f"  — {why}" if why else ""))
        return "\n".join(parts)

    @staticmethod
    def _render_claim_synthesis(claim: dict) -> str:
        route = str(claim.get("route") or "").strip()
        action = str(claim.get("action") or "").strip()
        headline = str(claim.get("headline") or "").strip()[:1200]
        if not route or not action:
            return ""
        lines = [
            "    VALID_RESULT→CLAIM: "
            f"route={route} action={action} advance_to_analysis_or_report=true"
        ]
        if headline:
            lines.append(f"      strongest_supported_finding: {headline}")
        evidence = claim.get("evidence")
        if isinstance(evidence, list):
            for item in evidence[:6]:
                text = str(item or "").strip()[:500]
                if text:
                    lines.append(f"      evidence: {text}")
        return "\n".join(lines)

    @staticmethod
    def _render_step_back(step_back: dict) -> str:
        """Render the reviewer's STEP-BACK reflection for the Planner.

        This is the anti-plan-lock-in block: a fresh-skeptic critique of THIS
        round's measured result, authored even on a clean success. The planner
        is REQUIRED (rule 17d) to triage each alt_direction. Returns "" when the
        object carries no usable signal.
        """
        def _clean(value: object, limit: int) -> str:
            return str(value or "").strip()[:limit]

        supported = _clean(step_back.get("supported_by_results"), 16)
        surprises = _clean(step_back.get("surprises"), 1200)
        parts: list[str] = [
            "    reviewer→planner STEP_BACK (anti-plan-lock-in — you MUST triage,"
            " rule 17d):"
        ]
        if supported:
            parts.append(f"      supported_by_results: {supported}")
        if surprises:
            parts.append(f"      surprises: {surprises}")
        questions = step_back.get("new_questions")
        if isinstance(questions, list) and questions:
            parts.append("      new_questions:")
            for q in questions[:5]:
                text = _clean(q, 400)
                if text:
                    parts.append(f"        - {text}")
        alts = step_back.get("alt_directions")
        if isinstance(alts, list) and alts:
            parts.append("      alt_directions (triage EACH — branch or reject with reason):")
            for entry in alts[:4]:
                if not isinstance(entry, dict):
                    continue
                direction = _clean(entry.get("direction"), 500)
                if not direction:
                    continue
                why = _clean(entry.get("why"), 500)
                cheap = bool(entry.get("cheap_to_test"))
                tag = " [cheap_to_test]" if cheap else ""
                parts.append(f"        - {direction}{tag}" + (f"  — {why}" if why else ""))
        # Header-only render carries no signal worth showing the planner.
        if len(parts) == 1:
            return ""
        return "\n".join(parts)

    @staticmethod
    def _render_checklist_feedback(feedback: dict) -> str:
        """Render the reviewer's ADVISORY checklist feedback for the Planner.

        The reviewer is feedback-only — it never edits the checklist. This block
        tells the Planner (the checklist OWNER) what to fix via ``checklist_ops``
        next cycle. Returns "" when the object carries no usable signal.
        """
        def _clean(value: object, limit: int) -> str:
            return str(value or "").strip()[:limit]

        stage = _clean(feedback.get("stage"), 100)
        summary = _clean(feedback.get("summary"), 600)
        parts: list[str] = []
        head = "    reviewer→planner CHECKLIST_FEEDBACK (you own the checklist — fix via checklist_ops)"
        if stage:
            head += f" [stage={stage}]"
        parts.append(head)
        if summary:
            parts.append(f"      summary: {summary}")
        items = feedback.get("items")
        if isinstance(items, list):
            for entry in items[:20]:
                if not isinstance(entry, dict):
                    continue
                problem = _clean(entry.get("problem"), 600)
                if not problem:
                    continue
                iid = _clean(entry.get("id"), 200)
                fix = _clean(entry.get("suggested_fix"), 600)
                label = f"      - {iid}: " if iid else "      - "
                parts.append(label + problem + (f"  → {fix}" if fix else ""))
        return "\n".join(parts) if len(parts) > 1 else ""

__all__ = ["PlannerRenderingMixin"]
