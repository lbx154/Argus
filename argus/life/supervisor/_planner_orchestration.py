"""Planner runtime gates and context helpers."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._config import LifeSupervisorConfig

if TYPE_CHECKING:
    from ._config import _MemoryView

log = logging.getLogger(__name__)


class PlannerOrchestrationMixin:
    if TYPE_CHECKING:
        # Provided by LifeSupervisor (_core.py); same shapes as the sibling mixins.
        memory: _MemoryView
        config: LifeSupervisorConfig

        def _project_state_root(self) -> Path: ...

    def _planner_cycle_gate_reason(self) -> str:
        gate = self.config.planner_cycle_gate
        if gate is None:
            return ""
        try:
            reason = gate()
        except Exception:  # noqa: BLE001
            log.exception("planner cycle gate raised; continuing with planner")
            return ""
        return str(reason or "").strip()

    def _planner_runtime_notes(self) -> str:
        """The runtime notes the Planner reads before the journal."""
        return "\n\n".join(
            part
            for part in (
                self._manager_planner_feedback_runtime_note(),
                self._planner_dropped_dependency_runtime_note(),
                self._planner_dropped_parallel_runtime_note(),
                self._planner_current_reality_note(),
            )
            if part
        )

    def _operator_map_note_lines(self) -> list[str]:
        """Render the operator's pinned Atlas map notes, newest last.

        The notes file is written by the web layer
        (``webapi.map_notes`` — one JSONL row per note). A campaign without
        notes pays one existence probe and no read; any failure renders
        nothing because the digest is advisory.
        """
        root = getattr(self.memory, "root", None)
        if not root:
            return []
        path = Path(root) / "map_notes.jsonl"
        try:
            if not path.is_file():
                return []
            from ..memory import _read_jsonl_tail

            rows = _read_jsonl_tail(path, 5)
        except Exception:  # noqa: BLE001 - the digest is advisory
            return []
        from ...core.secret_guard import redact_secrets_text

        lines: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            node_id = str(row.get("node_id") or "").strip()
            text = " ".join(str(row.get("text") or "").split())
            if not node_id or not text:
                continue
            text = redact_secrets_text(text)[:200]
            lines.append(f"  - on task {node_id}: {text}")
        if not lines:
            return []
        return ["- operator_map_notes:", *lines]

    def _planner_current_reality_note(self) -> str:
        """Render host-read state so Planner does not rediscover bookkeeping."""
        from ...core.pipeline_state import read_pipeline_state

        artifact_root = self._artifact_root()
        project_root = self._project_workdir()
        try:
            pipeline = read_pipeline_state(artifact_root)
        except (OSError, ValueError):
            pipeline = {}

        stage_rows: list[str] = []
        stages = pipeline.get("stages")
        if isinstance(stages, dict):
            for name, value in sorted(stages.items())[:12]:
                status = value.get("status") if isinstance(value, dict) else value
                stage_rows.append(f"{name}:{status or 'unknown'}")

        backlog_rows: list[Any] = []
        try:
            backlog_rows = list(self.memory.backlog.history())
        except Exception:  # noqa: BLE001 - digest is advisory
            pass
        # Rendered because the supervisor no longer stops the campaign for an
        # unanswered question. Without the text of what is waiting, the Planner
        # can reword the blocked work and slip past exact-signature dedupe.
        awaiting = [
            f"{getattr(item, 'id', '')}: {str(item.pending_question).strip()}"
            for item in backlog_rows
            if str(getattr(item, "pending_question", "") or "").strip()
        ]
        from ..memory import operator_answers

        answered = operator_answers(self.memory.backlog, str(self.config.continuous_objective or ""))
        backlog_counts: dict[str, int] = {}
        for item in backlog_rows:
            status = str(getattr(item, "status", "") or "unknown")
            backlog_counts[status] = backlog_counts.get(status, 0) + 1

        time_line = _planner_time_line(
            pipeline,
            backlog_rows,
            project_created_ts=_project_created_ts(self._project_state_root()),
        )
        # Who owns the current idea decides what a refuted claim allows; the
        # Planner never saw it, so s-a43d1018 treated its own pick as locked.
        direction_mode = str(pipeline.get("research_direction_mode") or "").strip()
        direction_lines = (
            [
                f"- research_direction_mode: {direction_mode} — "
                + (
                    "Argus selected the current idea: a working hypothesis you may "
                    "revise inside Experiment as the experiment.repair item states"
                    if direction_mode == "broad"
                    else "the operator named the idea: only the operator changes its claim"
                )
            ]
            if direction_mode
            else []
        )

        # Width-2 daemons spent months running serially because the Planner
        # could not see the second slot: admission needs every co-running
        # task to declare disjoint owned paths, and nothing ever said so.
        # Rendered only for multi-slot campaigns so serial ones pay nothing.
        mission_slots = int(getattr(self.config, "mission_slots", 1) or 1)
        slot_lines: list[str] = []
        if mission_slots > 1:
            running_rows = [
                item for item in backlog_rows if item.status == "running"
            ]
            free_slots = max(0, mission_slots - len(running_rows))
            slot_lines.append(
                f"- mission_slots: {mission_slots} total; "
                f"{len(running_rows)} running; {free_slots} free"
            )
            gpu_summary = self.memory.backlog.gpu_summary()
            if gpu_summary is not None:
                slot_lines.append(
                    f"- gpus: {gpu_summary['total']} on this machine; "
                    f"{gpu_summary['busy']} busy now; "
                    f"{gpu_summary['reserved']} reserved by active tasks; "
                    f"{gpu_summary['free']} claimable. TASK_GPUS=<n> is how many "
                    "GPUs a task holds while it runs; it is claimed only when that "
                    "many are free. Arms that can run side by side (model sizes, "
                    "training methods, seeds) belong in separate parallel-safe tasks "
                    "with their own TASK_GPUS, not in one task that runs them in turn."
                )
            from ..memory import shares_slot

            unowned = [item for item in running_rows if not shares_slot(item)]
            if free_slots and unowned:
                slot_lines.append(
                    "- parallel_slot: a spare mission slot sits idle while "
                    f"task {unowned[0].id} runs without declared "
                    "path ownership. Tasks share slots only when every "
                    "co-running task sets parallel_safe with disjoint "
                    "owns_paths; stage-closing and framework-maintenance "
                    "work always runs alone."
                )
            elif free_slots:
                slot_lines.append(
                    f"- parallel_slot: {free_slots} free; independent tasks "
                    "declared parallel_safe with disjoint owns_paths "
                    "can run now."
                )

        # A task that asks for more GPUs than this machine has is failed at
        # claim time; say so here so the Planner re-plans it smaller instead
        # of re-emitting it. Rendered only while such a row exists.
        from ..memory import GPU_UNFITTABLE_PREFIX

        for item in [
            row for row in backlog_rows
            if row.status == "failed"
            and str(getattr(row, "last_error", "") or "").startswith(
                GPU_UNFITTABLE_PREFIX
            )
        ][-3:]:
            slot_lines.append(
                f"- gpu_unfittable: task {item.id} ({item.title}) failed: "
                f"{item.last_error[len(GPU_UNFITTABLE_PREFIX):]}"
            )

        def _active_item_line(item: Any) -> str:
            base = (
                f"- {item.status} task {item.id}: {item.title}; "
                f"deps={item.deps}"
            )
            if mission_slots <= 1:
                return base
            owns = list(getattr(item, "owns_paths", None) or [])
            safe = "true" if getattr(item, "parallel_safe", False) else "false"
            return (
                f"{base}; parallel_safe={safe}; "
                f"owns_paths={'|'.join(owns) or 'none'}; "
                f"gpus={int(getattr(item, 'gpu_count', 0) or 0)}; "
                f"cpus={int(getattr(item, 'cpu_count', 0) or 0)}"
            )

        # Parks that nothing resumes automatically and no operator question
        # owns. The digest used to show them only as a count while their
        # pending dependents waited on them forever; the Planner may retire
        # one, which releases those dependents through the cascade.
        from ..memory import park_reason, planner_may_retire_park

        pending_rows = [row for row in backlog_rows if row.status == "pending"]
        parked_lines: list[str] = []
        for park in [row for row in backlog_rows if planner_may_retire_park(row)][:8]:
            blocked = {park.id}
            grew = True
            while grew:
                grew = False
                for row in pending_rows:
                    if row.id not in blocked and blocked.intersection(row.deps or ()):
                        blocked.add(row.id)
                        grew = True
            blocked.discard(park.id)
            parked_lines.append(
                f"- parked task {park.id} ({park_reason(park) or 'reason unknown'}): {park.title}; "
                f"blocks pending: [{', '.join(sorted(blocked))}]; nothing resumes "
                f"it automatically. `RETIRE_TASK={park.id} | <reason>` retires it, "
                "releases those dependents and frees the work to be re-planned."
            )

        changed_paths: list[str] = []
        try:
            status_result = subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if status_result.returncode == 0:
                changed_paths = [
                    line[3:].strip()
                    for line in status_result.stdout.splitlines()
                    if len(line) >= 4
                ]
        except (OSError, subprocess.SubprocessError):
            pass

        blockers: list[str] = []
        checkpoint_paths = list(
            dict.fromkeys(
                [
                    project_root / "CHECKPOINT.md",
                    artifact_root / "CHECKPOINT.md",
                ]
            )
        )
        for checkpoint_path in checkpoint_paths:
            try:
                lines = checkpoint_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            in_blockers = False
            for line in lines:
                if line.startswith("#"):
                    in_blockers = (
                        line.lstrip("# ").strip().casefold()
                        == "open questions / blockers"
                    )
                    continue
                if in_blockers and line.strip():
                    blockers.append(line.strip())
                    if len(blockers) >= 8:
                        break
            if len(blockers) >= 8:
                break

        changed_preview = ", ".join(changed_paths[:12]) or "(clean or unavailable)"
        if len(changed_paths) > 12:
            changed_preview += f", +{len(changed_paths) - 12} more"
        # The machine's time, beside its space. A Planner that knows it is
        # two in the morning and that nobody has written for seven hours
        # sizes the night's work differently from one that thinks a reply is
        # a minute away.
        presence_lines: list[str] = []
        try:
            from ...core.operator_presence import operator_presence

            presence = operator_presence(getattr(self.memory, "root", None))
            presence_lines.append(f"- time_and_presence: {presence.describe()}")
        except Exception:  # noqa: BLE001 - presence is advisory
            pass
        return "\n".join(
            [
                "## Host current-reality digest",
                *presence_lines,
                time_line,
                f"- vertical: {pipeline.get('vertical') or '(unresolved)'}",
                f"- workflow_mode: {pipeline.get('workflow_mode') or '(unset)'}",
                *direction_lines,
                f"- current_stage: {pipeline.get('current_stage') or self._current_pipeline_stage() or '(unset)'}",
                f"- stage_statuses: {', '.join(stage_rows) or '(none)'}",
                f"- backlog_counts: {json.dumps(backlog_counts, sort_keys=True)}",
                *slot_lines,
                *(
                    _active_item_line(item)
                    for item in backlog_rows
                    if item.status in {"pending", "running"}
                ),
                *parked_lines,
                (
                    "- awaiting_operator_answer: "
                    + "; ".join(awaiting)
                    + " — these stay the operator's to decide. Plan work that "
                    "does not depend on the answer rather than a reworded "
                    "version of the same question."
                    if awaiting
                    else "- awaiting_operator_answer: (none)"
                ),
                *(
                    [
                        "- operator_answers (their later word wherever the objective "
                        "or the Manager's strategic context says otherwise): "
                        + "; ".join(answered)
                    ]
                    if answered
                    else []
                ),
                *self._operator_map_note_lines(),
                f"- git_changed_paths ({len(changed_paths)}): {changed_preview}",
                f"- checkpoint_blockers: {'; '.join(blockers) or '(none declared)'}",
                "The host already read pipeline state, backlog, checkpoint blockers, "
                "and Git status for this digest. Do not spend tools rereading those "
                "sources unless a named contradiction requires exact content.",
            ]
        )


    def _post_mission_hook(self, outcome: dict[str, Any]) -> str:
        hook = self.config.post_mission_hook
        if hook is None:
            return ""
        try:
            return str(hook(outcome) or "").strip()
        except Exception:  # noqa: BLE001
            log.exception("post mission hook raised; continuing")
            return ""


__all__ = ["PlannerOrchestrationMixin"]


def _deadline_ts(pipeline: dict[str, Any]) -> float | None:
    """Read an operator deadline (epoch seconds or ISO 8601) from pipeline state."""
    from datetime import datetime

    for key in ("deadline", "deadline_ts"):
        raw = pipeline.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.timestamp()
    timeout_h = pipeline.get("time_budget_hours")
    started = pipeline.get("started_ts") or pipeline.get("created_ts")
    try:
        if timeout_h is not None and started is not None:
            return float(started) + float(timeout_h) * 3600.0
    except (TypeError, ValueError):
        pass
    return None


def _project_created_ts(state_root: Any) -> float | None:
    """Project creation time from the project's session record, if any."""
    import json

    if state_root is None:
        return None
    try:
        payload = json.loads((Path(state_root) / "session.json").read_text("utf-8"))
        created = float(payload.get("created") or 0.0)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return created if created > 0 else None


def _row_kind(item: Any) -> str:
    for tag in getattr(item, "tags", None) or []:
        if str(tag).startswith("stage:"):
            return str(tag)[len("stage:"):]
    return "other"


def _fmt_hours(hours: float) -> str:
    return f"{hours * 60:.0f}m" if hours < 1 else f"{hours:.1f}h"


def _planner_time_line(
    pipeline: dict[str, Any],
    rows: list[Any],
    *,
    project_created_ts: float | None = None,
    now: float | None = None,
) -> str:
    """Remaining time and measured run durations, shown on every digest.

    A Planner given a one-week window scheduled about six weeks of runs: it
    could see neither the clock nor how long finished runs had really taken.
    This is information for its judgement, not a gate on the plan.
    """
    import statistics

    now = time.time() if now is None else now
    deadline = _deadline_ts(pipeline)
    started = pipeline.get("started_ts") or project_created_ts
    parts: list[str] = []
    if deadline is None:
        parts.append(
            "no deadline recorded; if the operator's words state a deadline or "
            "time budget, plan against it"
        )
    else:
        hours = (deadline - now) / 3600.0
        parts.append(
            f"{_fmt_hours(hours)} remaining before the deadline"
            if hours >= 0
            else f"deadline passed {_fmt_hours(-hours)} ago"
        )
    try:
        if started:
            parts.append(f"project started {_fmt_hours((now - float(started)) / 3600.0)} ago")
    except (TypeError, ValueError):
        pass
    # Missions of different kinds (a paper draft, a code cleanup, a crash after
    # seconds) are not one population. Only completed runs measure throughput,
    # grouped by stage; failures are counted, not averaged in.
    by_kind: dict[str, list[float]] = {}
    unfinished = 0
    seen: set[str] = set()
    for item in rows:
        item_id = str(getattr(item, "id", "") or "")
        status = getattr(item, "status", "")
        started_ts = getattr(item, "started_ts", None)
        finished_ts = getattr(item, "finished_ts", None)
        if status not in {"done", "failed", "aborted"} or item_id in seen:
            continue
        seen.add(item_id)
        if status != "done":
            unfinished += 1
            continue
        if finished_ts is None or started_ts is None:
            continue
        try:
            span = float(finished_ts) - float(started_ts)
        except (TypeError, ValueError):
            continue
        if span > 0:
            by_kind.setdefault(_row_kind(item), []).append(span / 3600.0)
    medians = {kind: statistics.median(v) for kind, v in by_kind.items()}
    if medians:
        parts.append(
            "completed missions by stage: "
            + ", ".join(
                f"{kind} median {_fmt_hours(medians[kind])} over {len(by_kind[kind])}"
                for kind in sorted(by_kind)
            )
        )
    else:
        parts.append("no completed missions measured yet")
    if unfinished:
        parts.append(f"{unfinished} failed/aborted not counted")
    running = []
    for item in rows:
        started_ts = getattr(item, "started_ts", None)
        if getattr(item, "status", "") != "running" or not started_ts:
            continue
        elapsed = (now - float(started_ts)) / 3600.0
        median = medians.get(_row_kind(item))
        text = f"{item.id} running {_fmt_hours(elapsed)}"
        if median is not None:
            left = median - elapsed
            text += (
                f", ~{_fmt_hours(left)} left at its stage median"
                if left >= 0
                else ", past its stage median"
            )
        running.append(text)
    if running:
        parts.append("in flight: " + "; ".join(running[:4]))
    return "- time: " + "; ".join(parts)
