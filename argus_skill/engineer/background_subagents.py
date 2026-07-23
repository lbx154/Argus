"""Fold supervised-subagent usage into the mission cost stream."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from .external_work import _read_record, _registry_files

_REGISTRY_DIRNAME = ".argus_subagents"
_SUPERVISOR_USAGE_BASELINE_FIELD = "supervisor_cost_folded_totals"


def _usage_tuple_from_record(record: dict[str, Any]) -> tuple[int, int, int, int]:
    def _coerce(value: object) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    return (
        _coerce(record.get("supervisor_input_tokens", record.get("input_tokens"))),
        _coerce(
            record.get(
                "supervisor_cached_input_tokens",
                record.get("cached_input_tokens"),
            )
        ),
        _coerce(record.get("supervisor_output_tokens", record.get("output_tokens"))),
        _coerce(
            record.get(
                "supervisor_reasoning_output_tokens",
                record.get("reasoning_output_tokens"),
            )
        ),
    )


def _baseline_tuple_from_record(record: dict[str, Any]) -> tuple[int, int, int, int]:
    raw = record.get(_SUPERVISOR_USAGE_BASELINE_FIELD)
    if not isinstance(raw, dict):
        return (0, 0, 0, 0)
    return _usage_tuple_from_record(
        {
            "input_tokens": raw.get("input_tokens", raw.get("supervisor_input_tokens", 0)),
            "cached_input_tokens": raw.get(
                "cached_input_tokens",
                raw.get("supervisor_cached_input_tokens", 0),
            ),
            "output_tokens": raw.get(
                "output_tokens",
                raw.get("supervisor_output_tokens", 0),
            ),
            "reasoning_output_tokens": raw.get(
                "reasoning_output_tokens",
                raw.get("supervisor_reasoning_output_tokens", 0),
            ),
        }
    )


def _delta_from_totals(
    current: tuple[int, int, int, int],
    baseline: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    delta = tuple(current[i] - baseline[i] for i in range(4))
    return current if any(value < 0 for value in delta) else delta


def _write_registry_record(path: Path, record: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _persist_folded_baseline(
    path: Path,
    *,
    folded_totals: tuple[int, int, int, int],
) -> None:
    current = _read_record(path)
    if current is None:
        return
    existing = _baseline_tuple_from_record(current)
    merged = tuple(max(existing[i], folded_totals[i]) for i in range(4))
    current[_SUPERVISOR_USAGE_BASELINE_FIELD] = {
        "input_tokens": merged[0],
        "cached_input_tokens": merged[1],
        "output_tokens": merged[2],
        "reasoning_output_tokens": merged[3],
    }
    try:
        _write_registry_record(path, current)
    except OSError:
        return


def emit_subagent_cost_events(
    workdir: Path | str,
    on_event: Callable[[dict[str, Any]], None] | None,
) -> None:
    """Emit only supervisor-token deltas not folded by an earlier mission."""
    if on_event is None:
        return
    for path in _registry_files(workdir, _REGISTRY_DIRNAME):
        record = _read_record(path)
        if record is None:
            continue
        totals = _usage_tuple_from_record(record)
        baseline = _baseline_tuple_from_record(record)
        delta = _delta_from_totals(totals, baseline)
        if not any(delta):
            continue
        try:
            on_event(
                {
                    "type": "codex.util.completed",
                    "agent_layer": "subagent",
                    "model": str(record.get("supervisor_usage_model") or ""),
                    "run_label": str(record.get("task_id") or path.stem),
                    "session_id": str(record.get("task_id") or path.stem),
                    "input_tokens": delta[0],
                    "cached_input_tokens": delta[1],
                    "output_tokens": delta[2],
                    "reasoning_output_tokens": delta[3],
                    "premium_requests": 0.0,
                    "usage_scope": "delta",
                }
            )
        except Exception:
            continue
        _persist_folded_baseline(path, folded_totals=totals)
