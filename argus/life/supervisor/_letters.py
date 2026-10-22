"""The letter to the operator.

Between the first message of a campaign and its last, Argus has never written
to the person who started it. This mixin gives it a habit: every few hours,
at a mission boundary or while waiting on a long run, it gathers what happened
since the last letter, has the Manager write it up as a colleague would, and
leaves it where the operator will find it: the conversation transcript, which
every cockpit shows, and ``LETTERS.md`` in the project directory.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType

log = logging.getLogger(__name__)

LETTER_INTERVAL_KNOB = "ARGUS_SKILL_LETTER_INTERVAL_HOURS"
DEFAULT_LETTER_INTERVAL_HOURS = 8.0
LETTER_STATE_FILENAME = "letters.json"
LETTERS_FILENAME = "LETTERS.md"
_EVENT_TAIL_BYTES = 4_000_000


def _read_letter_excerpt(path: Path, *, limit: int) -> str:
    """Read a bounded excerpt without creating or changing an agent's file."""
    try:
        with path.open(encoding="utf-8") as handle:
            text = handle.read(limit + 1).strip()
    except (OSError, UnicodeError):
        return ""
    if len(text) > limit:
        return text[:limit].rstrip() + "\n[Excerpt; the saved file continues.]"
    return text


def _letter_interval_seconds() -> float:
    from ...core.knobs import resolve_knob

    raw = resolve_knob(LETTER_INTERVAL_KNOB, str(DEFAULT_LETTER_INTERVAL_HOURS)).value
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        hours = DEFAULT_LETTER_INTERVAL_HOURS
    return max(0.0, hours) * 3600.0


def _read_events_tail(life_dir: Path, *, since: float, types: set[str]) -> list[dict[str, Any]]:
    path = life_dir / "events.jsonl"
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _EVENT_TAIL_BYTES:
                handle.seek(size - _EVENT_TAIL_BYTES)
                handle.readline()
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in data.splitlines():
        if not line or not any(f'"{name}"' in line for name in types):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or str(row.get("type") or "") not in types:
            continue
        try:
            if float(row.get("ts") or 0.0) < since:
                continue
        except (TypeError, ValueError):
            continue
        rows.append(row)
    return rows


class LettersMixin:
    def _letter_state_path(self) -> Path | None:
        root = getattr(self.memory, "root", None)
        return Path(root) / LETTER_STATE_FILENAME if root else None

    def _read_letter_state(self) -> dict[str, Any]:
        path = self._letter_state_path()
        if path is None:
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_letter_state(self, state: dict[str, Any]) -> None:
        path = self._letter_state_path()
        if path is None:
            return
        try:
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.debug("letters: could not persist state at %s", path, exc_info=True)

    def _maybe_write_letter(self, *, force: bool = False) -> bool:
        """Write a letter when the interval has passed; never raise."""
        try:
            return self._write_letter_if_due(force=force)
        except Exception:  # noqa: BLE001 - a letter must never stop the campaign
            log.exception("letters: failed; continuing without one")
            return False

    def _write_letter_if_due(self, *, force: bool) -> bool:
        interval = _letter_interval_seconds()
        if interval <= 0 and not force:
            return False
        now = time.time()
        state = self._read_letter_state()
        started = float(state.get("started_at") or 0.0)
        if not started:
            state["started_at"] = now
            self._write_letter_state(state)
            if not force:
                return False
            started = now
        last_letter = float(state.get("last_at") or 0.0)
        last = last_letter or started
        attempted = float(state.get("attempted_at") or 0.0)
        if not force and (now - max(last, attempted)) < interval:
            return False
        # The first letter looks back over about one interval, so the missions
        # that finished just before the clock started are not lost.
        since = last_letter or max(0.0, started - max(interval, 3600.0))
        # Only news earns a letter: a campaign where nothing settled and
        # nothing was decided has nothing to tell, and a letter the Manager
        # failed to write on the same facts would fail again. Every try is
        # recorded, so neither case is retried before the next interval.
        news_since = max(since, attempted)
        facts = self._letter_facts(since=news_since, now=now)
        state["attempted_at"] = now
        self._write_letter_state(state)
        if not force and not (facts.get("missions") or facts.get("decisions")):
            return False
        if news_since != since:
            facts = self._letter_facts(since=since, now=now)
        from ...roles.prompts.letter import build_letter_prompt

        prompt = build_letter_prompt(facts=facts)
        text = self._bound_manager().write_prose(
            prompt,
            on_event=getattr(self.sink, "handle_event", None),
            run_label="manager-letter",
        )
        text = str(text or "").strip()
        if not text:
            return False

        life_dir = Path(self.memory.root)
        stage = str(facts.get("stage") or "")
        message_id = f"letter-{int(now)}"
        from ...core.operator_messages import publish_operator_message

        publish_operator_message(
            life_dir,
            text=text,
            message_id=message_id,
            event_fields={"letter": True, "current_stage": stage},
        )
        letters_path = self._append_letter_file(text, now=now)
        self._emit({
            "type": EventType.LIFE_LETTER_WRITTEN,
            "agent_layer": "manager",
            "stage": stage,
            "text": text,
            "message_id": message_id,
            "letters_path": str(letters_path or ""),
            "hours_since_last": max(0.0, (now - last) / 3600.0),
        })
        state["last_at"] = now
        state["count"] = int(state.get("count") or 0) + 1
        self._write_letter_state(state)
        self._emit_status("Wrote a letter to the operator")
        return True

    def _append_letter_file(self, text: str, *, now: float) -> Path | None:
        try:
            workdir = Path(self._project_workdir())
        except Exception:  # noqa: BLE001
            return None
        path = workdir / LETTERS_FILENAME
        stamp = time.strftime("%Y-%m-%d %H:%M %A", time.localtime(now))
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            header = "" if existing.startswith("# Letters") else "# Letters from Argus\n\n"
            with path.open("a", encoding="utf-8") as handle:
                if not existing:
                    handle.write(header)
                handle.write(f"## {stamp}\n\n{text.strip()}\n\n")
        except OSError:
            log.debug("letters: could not append to %s", path, exc_info=True)
            return None
        return path

    def _letter_facts(self, *, since: float, now: float) -> dict[str, Any]:
        from ...core.mission_view import uses_cjk
        from ...core.operator_presence import operator_presence
        from ...core.transcript import operator_request

        life_dir = Path(self.memory.root)
        objective = str(getattr(self.config, "continuous_objective", "") or "").strip()
        workdir: Path | None
        try:
            workdir = Path(self._project_workdir())
        except Exception:  # noqa: BLE001
            workdir = None
        if not objective and workdir is not None:
            try:
                objective = (workdir / "OBJECTIVE.md").read_text(encoding="utf-8").strip()
            except OSError:
                objective = ""
        presence = operator_presence(life_dir, now=now)

        stage = str(self._current_pipeline_stage() or "")
        tally = ""
        try:
            tally = str(self._render_campaign_tally() or "")
        except Exception:  # noqa: BLE001
            pass
        stage_line = f"Stage: {stage or '(unset)'}." + (f" {tally}." if tally else "")

        notes_head = ""
        if workdir is not None:
            try:
                from ...verticals.research_bridge import read_research_notes

                notes_head = "\n".join(read_research_notes(workdir).splitlines()[:40])[:3000]
            except Exception:  # noqa: BLE001
                notes_head = ""

        missions: list[str] = []
        cost = 0.0
        try:
            for entry in self.memory.journal.tail_settlements(80):
                try:
                    ts = float(getattr(entry, "ts", 0.0) or 0.0)
                except (TypeError, ValueError):
                    ts = 0.0
                if ts < since:
                    continue
                kind = str(getattr(entry, "kind", "") or "").removeprefix("mission_")
                title = str(getattr(entry, "title", "") or "").strip()
                summary = str(getattr(entry, "summary", "") or "").strip()
                entry_cost = float(getattr(entry, "cost_usd", 0.0) or 0.0)
                cost += entry_cost
                line = f"- {title} ({kind}"
                line += f", ${entry_cost:.2f})" if entry_cost else ")"
                if summary:
                    line += f": {summary[:500]}"
                missions.append(line)
        except Exception:  # noqa: BLE001
            pass

        # A settlement can be an old infrastructure pause or plan challenge;
        # it is not the latest scientific review. The saved report belongs to
        # Reviewer, and can precede the current unreviewed revision below.
        latest_review = (
            _read_letter_excerpt(workdir / "paper" / "REVIEW.md", limit=16_000)
            if workdir is not None
            else ""
        )

        decisions: list[str] = []
        for row in _read_events_tail(
            life_dir,
            since=since,
            types={
                "life.manager.stage_decision",
                "life.plan.node.superseded",
                "life.manager.plan_challenge.decided",
            },
        ):
            kind = str(row.get("type") or "")
            if kind == "life.manager.stage_decision":
                decisions.append(
                    f"- Stage decision: {row.get('action')} at {row.get('target_stage')}: "
                    f"{str(row.get('reason') or '')[:400]}"
                )
            elif kind == "life.plan.node.superseded":
                decisions.append(f"- Let go of a planned task: {str(row.get('reason') or '')[:300]}")
            elif kind == "life.manager.plan_challenge.decided":
                decisions.append(
                    f"- Plan challenge: {row.get('manager_action')}: {str(row.get('challenge') or '')[:300]}"
                )

        running: list[str] = []
        planned: list[str] = []
        questions: list[str] = []
        current_work: list[str] = []
        try:
            for item in self.memory.backlog.active():
                status = str(getattr(item, "status", "") or "")
                title = str(getattr(item, "title", "") or "")
                if status == "running":
                    running.append(f"- {title} (running)")
                elif status == "pending":
                    planned.append(f"- {title}")
                question = str(getattr(item, "pending_question", "") or "").strip()
                if question:
                    questions.append(f"- {question}")
                if len(current_work) < 6:
                    task_goal = str(getattr(item, "objective", "") or "").strip()
                    acceptance = str(getattr(item, "acceptance_check", "") or "").strip()
                    checkpoint = _read_letter_excerpt(
                        life_dir / "handoffs" / str(item.id) / "CHECKPOINT.md",
                        limit=5000,
                    )
                    current_work.append("\n".join(
                        part for part in (
                            f"Task: {title}",
                            f"Current goal: {task_goal[:1200]}" if task_goal else "",
                            f"Completion requirement: {acceptance[:800]}" if acceptance else "",
                            f"Current checkpoint (not a review verdict):\n{checkpoint}"
                            if checkpoint else "",
                        ) if part
                    ))
        except Exception:  # noqa: BLE001
            pass

        hours_window = max(0.0, (now - since) / 3600.0)
        cost_line = f"About ${cost:.2f} of model use across {len(missions)} finished missions in {hours_window:.1f} hours."
        silence = presence.silence_seconds
        return {
            "chinese": uses_cjk(operator_request(life_dir) or objective),
            "objective": objective[:4000],
            "stage": stage,
            "stage_line": stage_line,
            "notes_head": notes_head,
            "current_work": "\n\n".join(current_work),
            "missions": "\n".join(missions[:24]),
            "latest_review": latest_review,
            "decisions": "\n".join(decisions[:16]),
            "running": "\n".join(running[:12]),
            "planned": "\n".join(planned[:12]),
            "questions": "\n".join(questions[:6]),
            "cost": cost_line,
            "hours_since_operator_wrote": (silence / 3600.0) if silence is not None else None,
            "hours_since_last_letter": hours_window,
        }


__all__ = [
    "DEFAULT_LETTER_INTERVAL_HOURS",
    "LETTERS_FILENAME",
    "LETTER_INTERVAL_KNOB",
    "LettersMixin",
]
