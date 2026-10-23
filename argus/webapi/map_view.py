"""Read-only projection of mission records for the map, independent of scheduling."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..agent_cli._env import _SYNCHRONOUS_MANAGER_TURN_LABELS
from ..core.json_codec import loads_finite_json
from ..core.mission_view import operator_objective
from ..core.operator_decision import card_answer
from ..core.progress_step import describe_progress_step, plain_step_label
from ..core.secret_guard import redact_secrets_text
from ..core.session import read_session_meta
from ..core.stop_kinds import NON_FAILURE_STOP_KINDS
from ..life.memory import LifeMemory, _jsonl_history_paths, park_reason
from .map_outcomes import project_map_outcomes, public_outcome

TASK_FIELDS = (
    "id",
    "ts",
    "title",
    "objective",
    "status",
    "deps",
    "notes",
    "last_error",
    "pending_question",
    "plan_id",
    "plan_version",
    "attempt",
    "started_ts",
    "finished_ts",
    "superseded_by_plan_id",
    "superseded_reason",
    "node_key",
    "parallel_safe",
    "owns_paths",
    "acceptance_check",
    "goal_contribution",
    "plan_hypothesis",
    "non_goals",
    "outcome",
)
EVENT_PREFIXES = (
    "life.mission.",
    "life.phase.",
    "life.planner.task_added",
    # Low-frequency plan-identity transitions; at most one per retired node.
    "life.plan.node.superseded",
    "round.start",
    "round.main.completed",
    "round.review.",
    "agent.message",
    # What the operator was asked and answered, the Observer's remarks and the
    # Manager's supervision: why work waited and what it went on with.
    "life.operator_question.",
    "life.observer.note",
    "life.manager.supervision.applied",
)
# Records that name no task belong to the one mission running when they were
# written, when exactly one is.
WINDOW_PREFIXES = (
    "round.", "life.phase.", "agent.message", "engineer.progress",
    "life.observer.note", "life.manager.supervision.applied",
)

# The agent's own words while it works. A narration opens a work segment; the
# tool calls that follow belong to it, until the next narration or the end of
# the round. This is what lets any task's process appear on the map without
# the map knowing what kind of task it is.
NARRATION_KINDS = frozenset({"agent_message", "assistant_message", "message"})
STEP_KINDS = frozenset({"tool_use", "command_execution", "file_change"})
SEGMENT_CLOSERS = (
    "round.main.completed",
    "round.review.",
    "life.mission.completed",
    "life.mission.failed",
    "life.mission.orphaned",
)
# The Planner and the Manager work for the whole project: their progress is
# not the work of the task that happens to be running.
PROJECT_LAYERS = frozenset({"planner", "manager"})
SEGMENT_STEP_LIMIT = 40
SEGMENT_LIMIT_PER_ITEM = 80
SEGMENT_TEXT_LIMIT = 1200
STEP_LABEL_LIMIT = 160
TURN_LIMIT = 200
_ANSWER_CALL_LABELS = frozenset({"simple-1", "chat-1"})


def facts(value) -> str:
    """``value`` as canonical JSON: equal exactly when the values are equal."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_id(row: dict) -> str:
    """The row's own id; older rows without one are named by type, time, owner and round."""
    if row.get("event_id"):
        return str(row["event_id"])
    owner = row.get("item_id") or row.get("mission_id") or ""
    number = row.get("round_index", row.get("round", ""))
    return f"{row.get('type', '')}@{row.get('ts', 0)}:{owner}:{number}"


def text(value, limit=6000):
    return redact_secrets_text(str(value or ""))[:limit]


def task_content_revision(task: dict) -> str:
    return facts({k: task.get(k) for k in (
        "title", "objective", "acceptance_check", "goal_contribution", "plan_hypothesis", "non_goals",
    )})


def with_revisions(value: dict) -> dict:
    return {
        **value,
        "tasks": [
            {**t, "revision": t.get("revision") or facts(t),
             "content_revision": task_content_revision(t)}
            for t in value.get("tasks", [])
        ],
        "events": [
            {**e, "revision": facts({k: v for k, v in e.items() if k != "revision"})}
            for e in value.get("events", [])
        ],
    }


def _timestamp(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _step_from_progress(row: dict, workspace: str = "") -> dict:
    kind = str(row.get("kind") or "")
    label = row.get("text") or row.get("action_summary")
    if kind in {"tool_use", "tool_call"}:
        # The plain "verb + object" label, never ``view: {"path": "/abs"}``;
        # a label that already reads plainly (a manager turn step) is kept.
        label = plain_step_label(
            redact_secrets_text(str(label or "")), str(row.get("tool_name") or ""),
            redact_secrets_text(str(row.get("detail") or "")), workspace,
        ) or label
    elif kind == "command_execution" and not row.get("turn_step"):
        # "$ <command>" as a turn step says it, Argus' own paths cut short.
        label = describe_progress_step(row, workspace)[0]
    step = {
        "kind": kind,
        "label": text(label, STEP_LABEL_LIMIT),
        "ts": _timestamp(row.get("ts")),
    }
    for key in ("tool_name", "status", "call_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            step["tool" if key == "tool_name" else key] = text(value, 120)
    return step


def _close_segment(segments: dict, owner: str, record: str = "") -> None:
    segment = segments.get("open", {}).pop(owner, None)
    # Words with no work after them that the closing record repeats are the
    # agent's last message: the map shows them once, as that record.
    if segment is not None and (segment["steps"] or segment["text"] not in record):
        segments.setdefault("closed", []).append(segment)


def fold_progress(
    segments: dict, owner: str, row: dict, association: str, workspace: str = "",
) -> None:
    """Fold one ``engineer.progress`` row into the owner's open work segment.

    ``segments`` is ``{"open": {owner: segment}, "count": {owner: n},
    "closed": [segment, ...]}`` and persists between incremental reads, so a
    segment that is still being worked on keeps growing (same id, new
    revision) instead of being cut at the read boundary.
    """
    kind = str(row.get("kind") or "")
    if row.get("transient"):
        return
    if kind not in NARRATION_KINDS and kind not in STEP_KINDS:
        return
    opened = segments.setdefault("open", {})
    counts = segments.setdefault("count", {})
    segment = opened.get(owner)
    ts = _timestamp(row.get("ts"))
    role = str(row.get("agent_layer") or "engineer")
    if kind in NARRATION_KINDS:
        narration = text(row.get("text"), SEGMENT_TEXT_LIMIT).strip()
        if not narration:
            return
        if segment is not None and segment["steps"]:
            _close_segment(segments, owner)
            segment = None
        if segment is not None:
            # Two narrations with no work in between are one thought.
            segment["text"] = text(f"{segment['text']}\n\n{narration}", SEGMENT_TEXT_LIMIT)
            segment["ts_end"] = ts
            return
    if segment is None:
        index = counts.get(owner, 0)
        if index >= SEGMENT_LIMIT_PER_ITEM:
            return
        counts[owner] = index + 1
        segment = opened[owner] = {
            "id": f"seg:{owner}:{index + 1}",
            "item_id": owner,
            "type": "work.segment",
            "ts": ts,
            "ts_end": ts,
            "association": association,
            "role": role,
            "text": "",
            "steps": [],
            "overflow": 0,
        }
    segment["ts_end"] = ts
    if kind in NARRATION_KINDS:
        segment["text"] = text(row.get("text"), SEGMENT_TEXT_LIMIT).strip()
        return
    if len(segment["steps"]) >= SEGMENT_STEP_LIMIT:
        segment["overflow"] += 1
        return
    step = _step_from_progress(row, workspace)
    if not step["label"]:
        return
    last = segment["steps"][-1] if segment["steps"] else None
    if last is not None and _same_call(last, step):
        # A later report about the call just listed (started, then failed):
        # one step whose status is the latest word, not a second step.
        last.update({key: step[key] for key in ("status", "call_id") if key in step})
        return
    segment["steps"].append(step)


def _same_call(previous: dict, step: dict) -> bool:
    if previous.get("status") != "running" or step.get("status") in (None, "running"):
        return False
    if previous.get("call_id") and step.get("call_id"):
        return previous["call_id"] == step["call_id"]
    if previous.get("call_id") or step.get("call_id"):
        return False
    return all(previous.get(key) == step.get(key) for key in ("kind", "label", "tool"))


def segment_events(segments: dict) -> list[dict]:
    """Closed segments so far plus every open segment with work in it, oldest
    first. Words with no work after them yet wait: they may be the agent's last
    message, which its round's record repeats."""
    closed = segments.pop("closed", []) if segments else []
    opened = (segment for segment in segments.get("open", {}).values() if segment["steps"])
    return [*closed, *sorted(opened, key=lambda s: s["ts"])]


def progress_workspace(life_dir: Path, meta=None) -> str:
    """The project root the agents work in, from the session meta.

    Progress rows do not carry it; without it every absolute path outside the
    project (skill libraries, session stores) would read like project work.
    """
    for source in (meta, None):
        if source is None:
            try:
                source = json.loads((Path(life_dir) / "session.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        get = source.get if isinstance(source, dict) else lambda key: getattr(source, key, "")
        for key in ("workdir", "cwd"):
            value = get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def normalize_events(
    rows: list[dict], task_ids: set[str], active: set[str] | None = None,
    segments: dict | None = None, workspace: str = "",
) -> list[dict]:
    if active is None:
        active = set()
    result = []
    for row in rows:
        # Summary calls are accounted for, but are not research steps to summarize again.
        if row.get("run_label") == "map-summary":
            continue
        kind = str(row.get("type", ""))
        owner = str(row.get("item_id") or row.get("mission_id") or "")
        if kind == "life.mission.started" and owner:
            active.add(owner)
        association = "explicit"
        if not owner and kind.startswith(WINDOW_PREFIXES) and len(active) == 1:
            owner = next(iter(active))
            association = "single_active_window"
        collected = owner in task_ids and kind.startswith(EVENT_PREFIXES)
        said = text(
            row.get("summary")
            or row.get("reason")
            or row.get("text")
            or row.get("last_message")
            or row.get("message")
            or row.get("manager_decision")
            or row.get("question")
        ) if collected else ""
        if segments is not None and owner in task_ids:
            if kind == "engineer.progress":
                if row.get("agent_layer") not in PROJECT_LAYERS:
                    fold_progress(segments, owner, row, association, workspace)
            elif kind.startswith(SEGMENT_CLOSERS):
                _close_segment(segments, owner, said)
        if collected:
            # A task the Reviewer accepted closes in the Reviewer's words. A
            # handoff or self-check the Engineer wrote closes a round as a
            # review would, but no Reviewer took part: it is the Engineer's.
            accepted = (kind == "life.mission.completed" and row.get("success") is True
                        and row.get("review_source") == "reviewer")
            e = {
                "id": event_id(row),
                "item_id": owner,
                "type": kind,
                "ts": row.get("ts", 0),
                "association": association,
                "role": "reviewer" if accepted else str(
                    row.get("agent_layer")
                    or row.get("role")
                    or row.get("decided_by")
                    or ("reviewer" if "review" in kind
                        and not str(row.get("review_source") or "").startswith("engineer_") else "engineer")
                ),
                "text": said,
                "status": text(row.get("status"), 40),
                "next_action": text(row.get("next_action")),
            }
            number = row.get("round_index", row.get("round"))
            if isinstance(number, int) and 0 <= number < 10000:
                e["round_index"] = number
            for key in ("success", "review_skipped", "backend_unavailable", "overall_complete", "campaign_continues"):
                if isinstance(row.get(key), bool):
                    e[key] = row[key]
            if isinstance(row.get("attempt"), int):
                e["attempt"] = row["attempt"]
            if isinstance(row.get("review_source"), str):
                e["review_source"] = text(row["review_source"], 120)
            if row.get("park_reason"):
                e["park_reason"] = text(row["park_reason"], 80)
            if row.get("operator_question"):
                # A handoff to the operator settles ``blocked`` and asks this.
                e["operator_question"] = text(row["operator_question"])
            if row.get("stop_kind") in NON_FAILURE_STOP_KINDS:
                # Stopped, not failed: by whom or what, as the record names it.
                e["stop_kind"] = row["stop_kind"]
            if isinstance(row.get("outcome"), dict):
                e["outcome"] = public_outcome(row["outcome"])
            outcome = row.get("outcome")
            certification = row.get("stage_certification") or (
                outcome.get("stage_certification") if isinstance(outcome, dict) else None
            )
            if certification:
                e["stage_certification"] = text(certification, 80)
            if kind == "life.operator_question.pending" and row.get("options"):
                # The question with the answers it offered, numbered as the operator saw them.
                e["text"] = "\n".join([said, *(
                    f"{number}. {text(option, 160)}" for number, option in enumerate(row["options"], 1)
                )])
            if kind == "life.operator_question.answered" and row.get("option"):
                e["option"] = text(row["option"], 160)
            if kind == "life.plan.node.superseded":
                # Keep the shape lean: structured keys only when present.
                if row.get("reason"):
                    e["reason"] = text(row["reason"])
                if row.get("superseded_by_plan_id"):
                    e["superseded_by_plan_id"] = text(row["superseded_by_plan_id"], 160)
            result.append(e)
        if kind in ("life.mission.completed", "life.mission.failed", "life.mission.orphaned"):
            active.discard(owner)
    if segments is not None:
        result.extend(segment_events(segments))
    return list({e["id"]: e for e in result}.values())


# Inline lines ``map_references._inline_line`` writes in place of a quoted-card
# marker, in both locales: ``（引用：《title》· step）`` and ``(Referenced: "title" · step)``.
_REFERENCE_LINE = re.compile(
    r'^(?:（引用：《[^》]*》(?:\s*·[^）]*)?）|\(Referenced:\s*"[^"]*"(?:\s*·[^)]*)?\))\s*'
)


def ask_title(asked: str) -> str:
    """Name a turn card after the operator's own words.

    Quoted cards are expanded server-side into inline reference lines *before*
    the ask is persisted, so the first line is often a quote rather than the
    question. Skip leading reference lines (and reference prefixes on the same
    line); when the message is nothing but quotes, keep the first line.
    """
    lines = [line.strip() for line in str(asked or "").splitlines() if line.strip()]
    for line in lines:
        rest = line
        while True:
            stripped = _REFERENCE_LINE.sub("", rest, count=1)
            if stripped == rest:
                break
            rest = stripped.strip()
        if rest:
            return rest
    return lines[0] if lines else ""


def _turn_steps(steps: list[dict], workspace: str) -> list[dict]:
    """A turn's journaled steps as its work segment lists them."""
    return [
        {
            "kind": str(step.get("kind") or "tool_use"),
            "label": text(
                plain_step_label(
                    str(step.get("label") or ""), str(step.get("tool") or ""),
                    str(step.get("detail") or ""), workspace,
                ) if str(step.get("kind") or "tool_use") in {"tool_use", "tool_call"}
                else step.get("label"),
                STEP_LABEL_LIMIT,
            ),
            "ts": _timestamp(step.get("started_ts")),
            **({"tool": text(step["tool"], 120)} if step.get("tool") else {}),
            **({"status": text(step["status"], 40)} if step.get("status") else {}),
        }
        for step in steps[:SEGMENT_STEP_LIMIT]
    ]


def turn_records(
    rows: list[dict], turns: dict | None = None, asks: dict | None = None,
    workspace: str = "",
) -> dict:
    """Single-agent turns as map cards: what was asked, the work, the answer.

    A chat turn that used tools journals its steps on the ``ui.argus`` event;
    the matching ``ui.operator`` row (same ``web-<n>`` id) supplies the ask.
    ``turns`` and ``asks`` persist between incremental reads, so cards derived
    earlier survive a read that only sees new rows, and an ask read on one
    page still meets its reply on the next. Older non-streaming replies can
    recover execution-level evidence from a uniquely associated SELF receipt;
    missing per-tool details are never invented.
    """
    turns = turns if turns is not None else {}
    asks = asks if asks is not None else {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        message_id = str(row.get("message_id") or "")
        if row.get("type") == "manager.turn.started":
            ask = asks.setdefault(message_id, {"ts": row.get("ts"), "text": text(row.get("text"), 4000)})
            # The turn states the task it carries out. When the operator's
            # message only picked an option on a choice card ("直接做"), the task
            # is the request made earlier, and that request names the card.
            if objective := text(row.get("text"), 4000).strip():
                ask["text"] = objective
            ask["task"] = True
            ask["started_ts"] = _timestamp(row.get("ts"))
            card_id = f"turn:{message_id}"
            asked = text(ask.get("text"), 4000)
            qa = row.get("turn_kind") == "qa"
            ask["turn_kind"] = row.get("turn_kind", "")
            turns[card_id] = {
                "card": {"id": card_id, "kind": "turn", "ts": _timestamp(ask.get("ts")),
                         **({"turn_kind": "qa"} if qa else {}),
                         "title": text(ask_title(asked), 120),
                         "objective": asked, "status": "running", "deps": [], "role": "manager",
                         "summary": "", "started_ts": _timestamp(row.get("ts")), "finished_ts": None},
                "events": [] if qa else [{"id": f"{card_id}:work", "item_id": card_id, "type": "work.segment",
                            "ts": _timestamp(row.get("ts")), "association": "explicit", "role": "manager",
                            "status": "running", "text": "", "steps": [], "tool_details_recorded": False}],
            }
            ask["active_turn"] = turns[card_id]
            continue
        if row.get("type") == "engineer.progress" and row.get("turn_step"):
            # A running turn's steps as they are journaled, opened and then
            # closed under one message id; its reply carries the final list.
            turn = turns.get(str(row.get("item_id") or ""))
            if turn and turn["card"]["status"] == "running" and turn["events"]:
                journaled = turn.setdefault("steps", {})
                journaled.setdefault(message_id, {"started_ts": row.get("ts")}).update(
                    kind=row.get("kind"), label=row.get("text"), tool=row.get("tool_name"),
                    detail=row.get("detail"), status=row.get("status"),
                )
                work = turn["events"][0]
                work.pop("tool_details_recorded", None)
                work["steps"] = _turn_steps(list(journaled.values()), workspace)
            continue
        if row.get("type") == "manager.turn.cancelled":
            ask = asks.pop(message_id, {})
            if turn := turns.get(f"turn:{message_id}") or ask.get("active_turn"):
                turn["card"].update(status="cancelled", finished_ts=_timestamp(row.get("ts")))
                for event in turn["events"]:
                    event.update(status="cancelled", ts_end=_timestamp(row.get("ts")))
                turns[f"turn:{message_id}"] = turn
            continue
        if row.get("type") == "ui.operator" and message_id.endswith("-operator"):
            asks[message_id[: -len("-operator")]] = {
                "ts": row.get("ts"), "text": text(row.get("text"), 4000),
            }
            while len(asks) > 50:
                asks.pop(next(iter(asks)))
            continue
        run_label = str(row.get("run_label") or "")
        if run_label in _SYNCHRONOUS_MANAGER_TURN_LABELS | _ANSWER_CALL_LABELS:
            call_id = str(row.get("call_id") or "")
            if row.get("type") == "agent.io.start" and call_id and len(asks) == 1:
                ask = next(iter(asks.values()))
                ask.setdefault("calls", {})[call_id] = {
                    "started_ts": _timestamp(row.get("ts")),
                    "answer": run_label in _ANSWER_CALL_LABELS,
                }
            elif row.get("type") == "agent.io.complete" and call_id:
                for ask in asks.values():
                    call = ask.get("calls", {}).get(call_id)
                    if call is None:
                        continue
                    exit_code = row.get("exit_code")
                    failed = (
                        row.get("turn_failed") is True
                        or row.get("turn_completed") is False
                        or bool(row.get("fatal_error"))
                        or isinstance(exit_code, int) and exit_code != 0
                    )
                    call.update(
                        ended_ts=_timestamp(row.get("ts")),
                        observed=row.get("tool_activity_observed") is True,
                        status="failed" if failed else (
                            "completed" if exit_code == 0 else "unknown"
                        ),
                        error=text(row.get("fatal_error"), SEGMENT_TEXT_LIMIT),
                    )
                    break
        if row.get("type") != "ui.argus" or not message_id.endswith("-argus"):
            continue
        turn_id = message_id[: -len("-argus")]
        ask = asks.pop(turn_id, {})
        steps = [step for step in row.get("steps") or [] if isinstance(step, dict) and step.get("label")]
        calls = [call for call in ask.get("calls", {}).values() if call.get("ended_ts")]
        observed = [call for call in calls if call.get("observed")]
        # SELF delivery is itself durable execution evidence.  Argus-Pi can
        # legitimately report no per-tool steps, unlike an ordinary chat; an
        # item-bound receipt belongs to its queued task instead of a turn card.
        direct_result = (row.get("mission_result") is True or row.get("task_turn") is True) and not row.get("item_id")
        qa = row.get("turn_kind") == "qa" or ask.get("turn_kind") == "qa" or (
            not direct_result and not row.get("item_id") and not row.get("decision_card")
            and any(call.get("answer") for call in calls)
        )
        if not steps and not observed and not direct_result and not qa:
            continue
        recovered = not steps
        latest = calls[-1] if calls else {}
        failed = row.get("success") is False or latest.get("status") == "failed"
        status = "failed" if failed else (
            "unknown" if recovered and not qa and latest.get("status") != "completed"
            and row.get("success") is not True else "done"
        )
        asked = text(ask.get("text"), 4000).strip()
        reply = text(row.get("text"), 64_000 if qa else 4000).strip()
        timing = steps or (calls if qa else observed)
        started = min((_timestamp(step.get("started_ts")) for step in timing), default=0.0) or _timestamp(ask.get("started_ts"))
        finished = max((_timestamp(step.get("ended_ts")) for step in timing), default=0.0)
        error = latest.get("error") if failed else ""
        replied_at = _timestamp(row.get("ts"))
        card_id = f"turn:{turn_id}"
        title = ask_title(asked) if asked else (reply.splitlines()[0] if reply else turn_id)
        turns[card_id] = {
            "card": {
                "id": card_id,
                "kind": "turn",
                **({"turn_kind": "qa"} if qa else {}),
                "ts": _timestamp(ask.get("ts")) or started or replied_at,
                "title": text(title, 120),
                "objective": asked,
                "status": status,
                "deps": [],
                "role": "manager",
                "summary": text(error or reply, 400),
                "started_ts": started or None,
                "finished_ts": replied_at or finished or None,
            },
            "events": [
                {
                    "id": f"{card_id}:work",
                    "item_id": card_id,
                    "type": "work.segment",
                    "ts": started or replied_at,
                    "ts_end": finished or replied_at,
                    "association": "explicit" if steps or direct_result else "single_active_window",
                    "role": "manager",
                    "text": error or "",
                    "status": "failed" if failed else "recorded",
                    **({"tool_details_recorded": False} if recovered else {}),
                    "steps": _turn_steps(steps, workspace),
                    "overflow": max(0, len(steps) - SEGMENT_STEP_LIMIT),
                },
                {
                    "id": f"{card_id}:reply",
                    "item_id": card_id,
                    "type": "turn.replied",
                    "ts": replied_at,
                    "association": "explicit",
                    "role": "manager",
                    "text": reply,
                    "status": status,
                },
            ],
        }
        if qa and not steps and not observed:
            # Answering is not an invented execution/review stage. Keep the
            # original answer, which is already the user-facing deliverable.
            turns[card_id]["events"] = turns[card_id]["events"][1:]
    while len(turns) > TURN_LIMIT:
        turns.pop(next(iter(turns)))
    return turns


def read_map(
    sid: str, root: Path, life_dir: Path, *, event_state: dict | None = None,
    include_events: bool = True,
) -> dict:
    memory = LifeMemory.open(life_dir)
    tasks = []
    for item in memory.backlog.history():
        raw = item.to_jsonable()
        task = {k: raw[k] for k in TASK_FIELDS if k in raw}
        if reason := park_reason(item):
            task["park_reason"] = reason
        task["objective"] = operator_objective(raw)
        if answered := card_answer(item.operator_decision):
            # The objective stays the operator's request; what they answered
            # since is part of what the task now asks.
            task["answered"] = answered
        for k, v in list(task.items()):
            if k == "outcome":
                task[k] = public_outcome(v)
            elif isinstance(v, str):
                task[k] = text(v)
            elif isinstance(v, list):
                # owns_paths (and deps) are operator-visible strings too.
                task[k] = [text(x) if isinstance(x, str) else x for x in v]
        task["summary"] = task.get("last_error") or task.get("notes") or ""
        tasks.append(task)
    tasks.sort(key=lambda t: (t.get("ts") or 0, t["id"]))
    rows = []
    path = life_dir / "events.jsonl"
    truncated = False
    task_ids = {t["id"] for t in tasks}
    state = event_state if event_state is not None else {}
    previous = []
    active: set[str] = set()
    if include_events and path.is_file():
        with path.open("rb") as f:
            stat = path.stat()
            size = stat.st_size
            identity = (stat.st_dev, stat.st_ino)
            offset = state.get("offset", 0)
            append = (
                state.get("identity") == identity and size >= offset
                and state.get("task_ids") == task_ids
            )
            if append and offset:
                f.seek(max(0, offset - 128))
                append = f.read(min(128, offset)) == state.get("anchor")
            retained_previous = False
            if not append and state.get("identity") and state["identity"] != identity:
                for archived in _jsonl_history_paths(path):
                    if archived == path:
                        continue
                    archived_stat = archived.stat()
                    if (archived_stat.st_dev, archived_stat.st_ino) == state["identity"]:
                        retained_previous = True
                        break
            segments: dict = {}
            turns: dict = {}
            asks: dict = {}
            if append:
                previous = state.get("events", [])
                active = set(state.get("active", ()))
                segments = state.get("segments") or {}
                turns = dict(state.get("turns") or {})
                asks = dict(state.get("turn_asks") or {})
            start = max(0, size - 8 * 1024 * 1024)
            if append:
                start = max(start, offset)
            truncated = start > 0 and (
                not append or start > offset or state.get("truncated", False)
            )
            f.seek(start)
            if start and (not append or start != offset):
                f.readline()
                active.clear()
                segments = {}
                turns = {}
                asks = {}
            consumed = f.tell()
            for line in f.read(8 * 1024 * 1024).splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    continue
                consumed += len(line)
                try:
                    row = loads_finite_json(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    continue
            workspace = progress_workspace(life_dir, read_session_meta(root, sid))
            turns = turn_records(rows, turns, asks, workspace)
            # In the order things happened: a segment is only complete when
            # its round closes, and a turn's records when it is answered.
            events = sorted({e["id"]: e for e in [
                *previous,
                *normalize_events(rows, task_ids, active, segments, workspace),
                *(event for turn in turns.values() for event in turn["events"]),
            ]}.values(), key=lambda e: _timestamp(e["ts"]))
            f.seek(max(0, consumed - 128))
            state.update(
                identity=identity, offset=consumed, anchor=f.read(min(128, consumed)),
                task_ids=task_ids, events=events[-2000:], active=active,
                segments=segments, turns=turns, turn_asks=asks,
                truncated=truncated or len(events) > 2000,
                reset=bool(state) and not append and not retained_previous
                and state.get("task_ids") == task_ids,
            )
    else:
        events = []
        was_present = bool(state)
        state.clear()
        state["reset"] = was_present
    for turn in (state.get("turns") or {}).values():
        card = dict(turn["card"])
        card["revision"] = facts(card)
        tasks.append(card)
    tasks.sort(key=lambda t: (t.get("ts") or 0, t["id"]))
    meta = read_session_meta(root, sid)
    visible_events = events[-2000:]
    # A task is in the hands of the role that wrote its latest record.
    roles = {event["item_id"]: event["role"] for event in visible_events if event["association"] == "explicit"}
    for task in tasks:
        if task["id"] in roles:
            task["role"] = roles[task["id"]]
    tasks = project_map_outcomes(tasks, visible_events)
    for task in tasks:
        # An outcome binding can change when a lifecycle event arrives even if
        # the backlog row is unchanged. Its response revision must change too.
        task["revision"] = facts({key: value for key, value in task.items() if key != "revision"})
    return with_revisions({
        "id": f"live:{sid}",
        "title": meta.display_name if meta else sid,
        "kind": "live",
        "description": "",
        "read_only": False,
        "tasks": tasks,
        "events": visible_events,
        "coverage": {"truncated": truncated or len(events) > 2000},
    })
