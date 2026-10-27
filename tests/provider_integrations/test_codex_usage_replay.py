"""Real codex calls of s-efe9deac replayed through the usage ledger.

``turn.completed`` reports a thread's lifetime totals and leaves out the
subagents a turn spawns; codex writes each model request's own usage to its
session files. The ledger recorded what the totals implied: a restarted
daemon's first Planner turn as 9.94M tokens and $7.63 (the Observer reported
that figure to the Manager), and a 277-request Engineer turn of at most 240K
tokens per request at the long-context price, $12.68.
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.agent_cli.models import AgentRunResult
from argus.core.models import RunnerOptions
from argus.core.sandbox import private_path
from argus.core.session_ledger import _ledger_path
from argus.provider_integrations.codex_usage import read_codex_requests

DATA = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "codex_usage.json").read_text()
)
CALLS = DATA["calls"]


def _session_file(working_dir: Path, thread_id: str) -> Path:
    """Where codex keeps a thread: the local day and second its UUIDv7 id began."""
    began = datetime.fromtimestamp(int(thread_id.replace("-", "")[:12], 16) / 1000)
    sessions = private_path(Path(os.environ["CODEX_HOME"]) / "sessions", working_dir)
    path = sessions / f"{began:%Y/%m/%d}" / f"rollout-{began:%Y-%m-%dT%H-%M-%S}-{thread_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write(working_dir: Path, thread_id: str, at: float, payloads: list[dict[str, Any]], kind: str) -> None:
    stamp = datetime.fromtimestamp(at, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with _session_file(working_dir, thread_id).open("a", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps({"timestamp": stamp, "type": kind, "payload": payload}) + "\n")


def _record(working_dir: Path, session_id: str, rows: list[list[int]], *, at: float, thread_id: str = "") -> None:
    """Append rows as codex's ``token_usage_record`` lines."""
    _write(working_dir, thread_id or session_id, at, [{
        "thread_id": thread_id or session_id, "session_id": session_id,
        "usage": dict(zip(DATA["columns"], row, strict=True)),
    } for row in rows], "token_usage_record")


def _activity(working_dir: Path, thread_id: str, agent_thread_id: str, *, at: float) -> None:
    """Append codex's event of ``thread_id`` starting or messaging another agent."""
    _write(working_dir, thread_id, at, [{"type": "item_completed", "thread_id": thread_id, "item": {
        "type": "SubAgentActivity", "kind": "started", "agent_thread_id": agent_thread_id,
    }}], "event_msg")


def _run(working_dir: Path, call: dict[str, Any]) -> None:
    """What codex writes during ``call``: its requests, and its subagent's
    under the parent session in the subagent's own thread."""
    _record(working_dir, call["thread_id"], call["requests"], at=time.time())
    if subagent := call.get("subagent_thread_id"):
        _activity(working_dir, call["thread_id"], subagent, at=time.time())
        _record(working_dir, call["thread_id"], call["subagent_requests"], at=time.time(), thread_id=subagent)
        _activity(working_dir, subagent, call["thread_id"], at=time.time())


def _replay(
    backend: AgentCliBackend, working_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str, *, sessions: bool,
) -> dict[str, Any]:
    """Run one recorded call; with ``sessions`` codex writes its request rows meanwhile."""
    call = CALLS[name]

    def run_exec(self: Any, **kwargs: Any) -> AgentRunResult:
        if sessions:
            _run(working_dir, call)
        return AgentRunResult(
            command=["codex", "exec", "resume"], exit_code=0, thread_id=call["thread_id"],
            json_events=[{"type": "turn.completed", "usage": call["turn_completed"]}],
            turn_completed=True,
        )

    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_LOG", str(working_dir / "events.jsonl"))
    monkeypatch.setattr(backend._runner.__class__, "run_exec", run_exec, raising=True)
    backend.run_exec(
        prompt="replay", options=RunnerOptions(model=DATA["model"], working_dir=str(working_dir)),
        run_label=call["run_label"], resume_thread_id=call["thread_id"],
    )
    return json.loads((working_dir / "usage.jsonl").read_text().splitlines()[-1])


def _unknown(row: dict[str, Any]) -> bool:
    return row["input_tokens"] is None and row["cost_usd"] is None and row["pricing_status"] == "partial"


def test_a_resumed_thread_without_a_baseline_records_unknown_not_its_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No session files: only the thread totals of turn.completed are known.
    first = AgentCliBackend(backend="codex")
    assert _unknown(_replay(first, tmp_path, monkeypatch, "3a115110", sessions=False))
    assert _replay(first, tmp_path, monkeypatch, "0b6a7eb7", sessions=False)["input_tokens"] == 1_740_554

    _ledger_path(CALLS["0b6a7eb7"]["thread_id"]).unlink()
    restarted = AgentCliBackend(backend="codex")
    row = _replay(restarted, tmp_path, monkeypatch, "0b6a7eb7", sessions=False)
    assert row["input_tokens"] != 8_631_342
    assert _unknown(row)


@pytest.mark.parametrize("sessions", [True, False], ids=["session-files", "totals-only"])
def test_the_restarted_planner_turn_is_charged_only_its_own_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sessions: bool,
) -> None:
    call = CALLS["7d1cc6dc"]
    # The thread's previous turn (71ff3f0c) ran in the process the upgrade stopped.
    _record(tmp_path, call["thread_id"], CALLS["71ff3f0c"]["requests"], at=time.time() - 300)
    row = _replay(AgentCliBackend(backend="codex"), tmp_path, monkeypatch, "7d1cc6dc", sessions=sessions)
    assert call["ledger"] == {"input_tokens": 9_936_618, "cost_usd": pytest.approx(7.629569)}
    if not sessions:
        assert _unknown(row)
        return
    assert (row["input_tokens"], row["cached_input_tokens"], row["cache_write_tokens"], row["output_tokens"]) == (
        625_497, 316_718, 308_773, 647,
    )
    # Two requests of 309K and 316K: both long-context; reasoning is part of output.
    assert row["pricing_tier"] == "long_context"
    assert row["cost_usd"] == pytest.approx(1.616938)


def test_a_call_reads_only_its_own_and_its_subagents_session_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ~/.codex/sessions held 3.1G. Other roles write theirs meanwhile, on the
    # call's days and others, one half-way through a Chinese line.
    call = CALLS["480d969f"]
    own = _session_file(tmp_path, call["thread_id"])
    subagent = _session_file(tmp_path, call["subagent_thread_id"])
    since = time.time()
    for day in range(20, 28):
        for n in range(40):
            other = own.parent.parent / f"{day}" / f"rollout-2026-10-{day}T07-29-{n:02}-01a17c47-2ff5-7cc3-{n:04}.jsonl"
            other.parent.mkdir(exist_ok=True)
            other.write_bytes(f'{{"session_id":"{call["thread_id"]}","message":"观察员'.encode()[:-1])
    _run(tmp_path, call)
    listed: list[Path] = []
    opened: list[Path] = []
    scandir, path_open = os.scandir, Path.open
    monkeypatch.setattr(os, "scandir", lambda path=".": listed.append(Path(path)) or scandir(path))
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: opened.append(self) or path_open(self, *a, **k))
    requests = read_codex_requests(session_id=call["thread_id"], since=since, working_dir=tmp_path)
    assert len(requests) == len(call["requests"]) + len(call["subagent_requests"])
    assert sorted(opened) == sorted([own, subagent])
    # No walk: of each thread only its day, local and UTC, is listed.
    assert len(listed) <= 4 and {path.parent for path in listed} == {own.parent.parent}


def test_an_agent_line_naming_no_thread_leaves_the_calls_own_requests(tmp_path: Path) -> None:
    call = CALLS["7d1cc6dc"]
    _record(tmp_path, call["thread_id"], call["requests"], at=time.time())
    _activity(tmp_path, call["thread_id"], None, at=time.time())
    requests = read_codex_requests(session_id=call["thread_id"], since=0, working_dir=tmp_path)
    assert len(requests) == len(call["requests"])


def test_a_long_engineer_turn_is_priced_per_request_with_its_subagents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = CALLS["480d969f"]  # 96a7237f3242, engineer-r1, 04:52-07:26
    row = _replay(AgentCliBackend(backend="codex"), tmp_path, monkeypatch, "480d969f", sessions=True)
    assert call["ledger"] == {"input_tokens": 43_905_781, "cost_usd": pytest.approx(12.680396)}
    assert max(request[0] for request in call["requests"]) == 240_366
    assert row["input_tokens"] == 43_905_781 + 987_676
    assert row["pricing_tier"] == "default"
    # The turn's 277 requests $6.28 (cache writes $0.11 of it), its 16 subagent requests $0.34.
    assert row["cost_usd"] == pytest.approx(6.277554 + 0.343441)
