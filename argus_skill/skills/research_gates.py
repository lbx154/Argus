"""Generic research-gate contract: machine-readable RESULT / FAILURES / REPAIR_TASKS
/ REVIEW outputs plus a per-gate repair state with failure-id-level progress
tracking and stall detection.

This generalises the proven ``skills/manuscript_repair.py`` pattern to ANY stage
gate (literature positioning, and — in later phases — theory / numerical / novelty
/ paper-type). A gate runs a deterministic *artifact* check (never a network call),
emits the four documents below, and updates a repair state so the next round's
agent prompt gets the exact failures and can resolve them one by one; if the
failure count does not drop for two consecutive rounds the gate is marked stalled.

Per gate (``gate_id`` e.g. ``"literature"`` → file prefix ``LITERATURE_GATE``) the
outputs live under ``research/``:

* ``<PREFIX>_RESULT.json``      — machine-readable gate result (status + summary)
* ``<PREFIX>_FAILURES.json``    — the failure list (each with ``required_action``)
* ``<PREFIX>_REPAIR_TASKS.md``  — human-readable per-failure repair checklist
* ``<PREFIX>_REVIEW.md``        — human-readable gate review
* ``<PREFIX>_STATE.json``       — repair progress + stall bookkeeping
* ``<PREFIX>_STALLED.md``       — written only when the gate stalls

Advisory gates never block a stage advance; they still write these artifacts and
feed the repair block into the next prompt (see :func:`render_active_repair_blocks`,
injected by the physics ``role_banner``). The manuscript stage remains the only
hard completion gate.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

#: Consecutive rounds without a drop in the failure count before "stalled".
STALL_THRESHOLD = 2

#: Allowed severities for a :data:`GateFailure`.
SEVERITIES = ("blocker", "major", "minor", "warning")

#: A single machine-readable gate failure. Required keys:
#:   failure_id, severity, stage, artifact, field, message, required_action, blocks_progress
GateFailure = dict

_TRUTHY = {"true", "yes", "y", "1", "done", "used", "advanced", "basic"}


def is_truthy(value: object) -> bool:
    """Loose truthiness for gate CSV cells ('true'/'yes'/'used'/... -> True)."""
    return str(value or "").strip().lower() in _TRUTHY


def read_csv_rows(path: Path) -> tuple[list[str], list[dict]]:
    """Read a gate artifact CSV -> ``(header, rows)``; ``([], [])`` if absent/empty."""
    if not path.is_file() or path.stat().st_size == 0:
        return [], []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            rows = [dict(r) for r in reader]
    except OSError:
        return [], []
    return header, rows


def gate_file_prefix(gate_id: str) -> str:
    """``"literature"`` -> ``"LITERATURE_GATE"`` (the on-disk artifact prefix)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", str(gate_id)).strip("_").upper() + "_GATE"


def _research_dir(root: object) -> Path:
    return Path(str(root or ".")) / "research"


def _artifact(root: object, gate_id: str, suffix: str) -> Path:
    return _research_dir(root) / f"{gate_file_prefix(gate_id)}_{suffix}"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Repair state (failure-id-level progress + stall).                            #
# --------------------------------------------------------------------------- #
def read_gate_state(root: object, gate_id: str) -> dict | None:
    try:
        data = json.loads(_artifact(root, gate_id, "STATE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def update_gate_state(
    root: object,
    gate_id: str,
    failures: list[GateFailure],
    *,
    now_iso: str | None = None,
) -> dict:
    """Record a failing gate round and return the new state.

    Tracks ``round``, ``prev_failure_count``/``failure_count``, the resolved /
    persistent / new ``failure_id`` sets versus the previous round, a
    ``no_drop_streak`` and a ``stalled`` flag (set once the failure count fails to
    drop for :data:`STALL_THRESHOLD` consecutive rounds). Writes ``<PREFIX>_STALLED.md``
    when stalled.
    """
    prev = read_gate_state(root, gate_id)
    ids = [str(f.get("failure_id", "")) for f in failures if f.get("failure_id")]
    count = len(failures)
    if prev is None:
        rnd, prev_count, no_drop, prev_ids = 1, None, 0, []
    else:
        rnd = int(prev.get("round", 0) or 0) + 1
        prev_count = prev.get("failure_count")
        no_drop = int(prev.get("no_drop_streak", 0) or 0)
        prev_ids = list(prev.get("failure_ids", []) or [])
        if isinstance(prev_count, int) and count >= prev_count:
            no_drop += 1
        else:
            no_drop = 0
    resolved = [i for i in prev_ids if i not in ids]
    persistent = [i for i in ids if i in prev_ids]
    new = [i for i in ids if i not in prev_ids]
    stalled = no_drop >= STALL_THRESHOLD
    state: dict = {
        "gate_id": gate_id,
        "round": rnd,
        "prev_failure_count": prev_count,
        "failure_count": count,
        "failure_ids": ids,
        "resolved_failure_ids": resolved,
        "persistent_failure_ids": persistent,
        "new_failure_ids": new,
        "no_drop_streak": no_drop,
        "stalled": stalled,
        "status": f"{gate_id}_stalled" if stalled else f"{gate_id}_repair_required",
        "failures": list(failures),
    }
    if now_iso:
        state["updated_utc"] = now_iso
    _atomic_write(_artifact(root, gate_id, "STATE.json"), json.dumps(state, indent=2, sort_keys=True))
    stalled_path = _artifact(root, gate_id, "STALLED.md")
    if stalled:
        _atomic_write(stalled_path, _render_stalled_md(state))
    else:
        try:
            stalled_path.unlink()
        except OSError:
            pass
    return state


def clear_gate_state(root: object, gate_id: str) -> None:
    """Remove the repair state (call once the gate passes)."""
    for suffix in ("STATE.json", "STALLED.md"):
        try:
            _artifact(root, gate_id, suffix).unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Output documents.                                                            #
# --------------------------------------------------------------------------- #
def write_gate_outputs(
    root: object,
    gate_id: str,
    *,
    result: dict,
    failures: list[GateFailure],
    human_review: str,
) -> None:
    """Write the RESULT/FAILURES/REPAIR_TASKS/REVIEW artifacts for a gate run."""
    _atomic_write(_artifact(root, gate_id, "RESULT.json"), json.dumps(result, indent=2, sort_keys=True))
    _atomic_write(_artifact(root, gate_id, "REVIEW.md"), human_review.rstrip() + "\n")
    if failures:
        _atomic_write(_artifact(root, gate_id, "FAILURES.json"), json.dumps(list(failures), indent=2))
        _atomic_write(_artifact(root, gate_id, "REPAIR_TASKS.md"), _render_repair_tasks_md(gate_id, failures))
    else:
        for suffix in ("FAILURES.json", "REPAIR_TASKS.md"):
            try:
                _artifact(root, gate_id, suffix).unlink()
            except OSError:
                pass


def _render_repair_tasks_md(gate_id: str, failures: list[GateFailure]) -> str:
    lines = [f"# {gate_file_prefix(gate_id)} repair tasks", "",
             "Resolve EVERY failure below, one concrete action per `failure_id`, then "
             "re-run the gate and confirm it passes. Do not rewrite unrelated prose in "
             "place of clearing these.", ""]
    for f in failures:
        lines.append(f"## {f.get('failure_id', '?')} [{f.get('severity', 'major')}] "
                     f"(stage: {f.get('stage', '?')}, artifact: {f.get('artifact', '?')})")
        lines.append(f"- Problem: {f.get('message', '')}")
        lines.append(f"- Required action: {f.get('required_action', '')}")
        lines.append(f"- Blocks progress: {bool(f.get('blocks_progress', False))}")
        lines.append("")
    return "\n".join(lines)


def _render_stalled_md(state: dict) -> str:
    return (
        f"# {gate_file_prefix(state.get('gate_id', 'gate'))} STALLED\n\n"
        f"The deterministic failure count has not dropped for {state.get('no_drop_streak')} "
        f"consecutive rounds (currently {state.get('failure_count')} failures, "
        f"round {state.get('round')}). Persistent failures: "
        f"{', '.join(state.get('persistent_failure_ids', [])) or '(none)'}.\n\n"
        "This gate is BLOCKED for automated repair. Report the specific obstacle and "
        "the human / stronger-model intervention needed — do not keep re-submitting the "
        "same artifact.\n"
    )


# --------------------------------------------------------------------------- #
# Repair-block rendering (injected into the next stage prompt).                #
# --------------------------------------------------------------------------- #
def render_gate_repair_block(state: dict | None) -> str:
    if not state or not state.get("failures"):
        return ""
    prefix = gate_file_prefix(state.get("gate_id", "gate"))
    lines = [
        f"## {prefix} REPAIR REQUIRED ({state.get('failure_count')} failure(s), "
        f"round {state.get('round')})",
        f"The {state.get('gate_id')} gate failed its deterministic artifact check. Resolve "
        "EVERY failure below — one concrete edit per failure_id — then re-run the gate "
        f"(see research/{prefix}_REPAIR_TASKS.md) and confirm it passes. Persistent across "
        f"rounds: {', '.join(state.get('persistent_failure_ids', [])) or '(none)'}.",
    ]
    for f in state.get("failures", []):
        lines.append(
            f"  - {f.get('failure_id', '?')} [{f.get('severity', 'major')}]: "
            f"{f.get('message', '')} -> {f.get('required_action', '')}"
        )
    if state.get("stalled"):
        lines.append(
            "STALL DETECTED: the failure count has not dropped across rounds. If you cannot "
            "clear these deterministically, STOP and report BLOCKED with the specific obstacle."
        )
    return "\n".join(lines)


def render_active_repair_blocks(root: object) -> str:
    """Render the repair blocks for EVERY active (failing) research gate found under
    ``research/*_GATE_STATE.json``. Empty string when none are active. Injected into
    the next stage prompt by the physics ``role_banner``."""
    rdir = _research_dir(root)
    if not rdir.is_dir():
        return ""
    blocks: list[str] = []
    for path in sorted(rdir.glob("*_GATE_STATE.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(state, dict) and state.get("failures"):
            block = render_gate_repair_block(state)
            if block:
                blocks.append(block)
    return "\n\n".join(blocks)


__all__ = [
    "STALL_THRESHOLD",
    "SEVERITIES",
    "GateFailure",
    "is_truthy",
    "read_csv_rows",
    "gate_file_prefix",
    "read_gate_state",
    "update_gate_state",
    "clear_gate_state",
    "write_gate_outputs",
    "render_gate_repair_block",
    "render_active_repair_blocks",
]
