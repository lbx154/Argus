"""What the host saw of the Engineer's round, rendered for the Reviewer.

One control project's Reviewer re-read 61 of the Engineer's files and still
never opened the script whose "perplexity" was a formula; the host had the
commands all along. The provider summarises them: counts, longest commands,
tests and checks with every run's exit code, the round's last commands, other
failures, and paths outside the workspace.

The host records only what the agent CLI's stream reported, which a command
can tamper with. The command text and output are the Engineer's, so the packet
shows each check whole, flags the shapes that change what an exit code means,
shows conflicting and unverified results as such, and reads only host memory
or the host's own event log at its exact path.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.command_record import CommandCapture
from argus.engineer.round_evidence import RoundEvidenceRequest
from argus.verticals.research import round_log as mod
from argus.verticals.research import spec_checks

T0 = 1_800_000_000.0
PYTEST = "cd /app && PYTHONPATH=/app/src:/app/lib timeout 900 /app/.venv/bin/python -m pytest tests/test_outputs.py -q"


def _event(ts: float, kind: str, text: str, *, layer: str = "engineer", tool: str = "bash", **extra) -> dict:
    return {"type": "engineer.progress", "ts": ts, "kind": kind, "agent_layer": layer,
            "tool_name": tool, "text": text, **extra}


def _start(ts: float, text: str, call_id: str) -> dict:
    return _event(ts, "command_execution", text, status="running", call_id=call_id)


def _result(ts: float, call_id: str, exit_code: int, excerpt: str) -> dict:
    return _event(ts, "tool_result", excerpt, status="completed", exit_code=exit_code,
                  call_id=call_id, output_excerpt=excerpt)


def _write_events(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _probe_rows() -> list[dict]:
    return [
        {"type": "round.start", "ts": T0, "round_index": 1},
        _start(T0 + 1, PYTEST + " 2>&1 | tail -5", "a"),
        _result(T0 + 2, "a", 1, "1 failed, 6 passed in 0.4s"),
        _start(T0 + 3, PYTEST + " -k 'not test_idle' 2>&1 | tail -5 || true", "b"),
        _result(T0 + 4, "b", 0, "6 passed, 1 deselected in 0.3s"),
        _start(T0 + 5, "sed -i 's/assert w == 3/assert w >= 0/' tests/test_watermark.py", "c"),
        _result(T0 + 6, "c", 0, ""),
        _start(T0 + 7, "echo 'check: 7 passed'", "d"),
        _result(T0 + 8, "d", 0, "check: 7 passed"),
    ]


def test_the_header_says_what_the_record_is_and_is_not(tmp_path: Path) -> None:
    events = _write_events(tmp_path / "project" / "events.jsonl", _probe_rows())
    text = mod.render_round_log(tmp_path / "ws", 1, events_path=events)

    assert text.startswith("Engineer's commands this round (host record since ")
    assert "what the agent CLI's output stream reported, including its exit code" in text
    assert "not tamper-proof" in text
    assert "the command text and output were produced by the Engineer's commands" in text


def test_without_any_reported_result_the_log_claims_no_exit_codes(tmp_path: Path) -> None:
    capture = CommandCapture(label="engineer-r1")
    capture.start("s1", "command", "pytest -q", ts=T0 + 1)
    capture.start("s2", "command", "python scripts/eval_ruler.py --config c.yaml", ts=T0 + 2)

    text = mod.render_round_log(tmp_path, 1, runs=capture.runs())

    assert mod.HEADER_WITHOUT_RESULTS in text
    assert "recorded what the agent CLI's output stream reported" not in text
    assert "  - `pytest -q`: no result recorded" in text
    assert "- evaluations, benchmarks and training runs invoked" in text
    assert "  - `python scripts/eval_ruler.py --config c.yaml`: no result recorded" in text


def test_checks_are_shown_whole_and_flagged(tmp_path: Path) -> None:
    events = _write_events(tmp_path / "project" / "events.jsonl", _probe_rows())
    text = mod.render_round_log(tmp_path / "ws", 1, events_path=events)

    # Two commands that share their first 90 characters stay two checks.
    assert f"  - `{PYTEST} 2>&1 | tail -5`: exit 1 (watch: piped: the exit code is the last stage's" in text
    masked = next(line for line in text.splitlines() if "not test_idle" in line)
    assert f"`{PYTEST} -k 'not test_idle' 2>&1 | tail -5 || true`: exit 0" in masked
    for flag in ("`|| true` hides a failure", "piped: the exit code is the last stage's",
                 "selects or deselects tests", "tests or checks were edited this round"):
        assert flag in masked
    # An edit and an echo are not checks; they appear as the round's last commands.
    checks = text.split("- the round's last commands:")[0]
    assert "sed -i" not in checks.split("tests and checks")[1]
    assert "echo 'check: 7 passed'" not in checks
    assert "  - `echo 'check: 7 passed'`: exit 0" in text


def test_every_run_is_shown_in_order_and_an_earlier_failure_stays_visible(tmp_path: Path) -> None:
    events = _write_events(tmp_path / "events.jsonl", [
        {"type": "round.start", "ts": T0, "round_index": 2},
        _start(T0 + 1, "pytest -q", "a"),
        _result(T0 + 2, "a", 1, "FAILED tests/test_a.py::test_x - AssertionError | 1 failed, 13 passed"),
        _start(T0 + 5, "pytest -q", "b"),
        _result(T0 + 6, "b", 0, "14 passed"),
    ])

    text = mod.render_round_log(tmp_path, 2, events_path=events)

    assert "  - `pytest -q` ×2: exit 1, then exit 0" in text
    assert "    output of the latest run: 14 passed" in text
    assert "    output of the last failing run: FAILED tests/test_a.py::test_x - AssertionError" in text


def test_a_long_command_keeps_its_head_and_tail() -> None:
    command = "python -m pytest " + " ".join(f"tests/test_{i}.py" for i in range(60)) + " -k 'not slow' | tail -3"
    shown = mod.show_command(command)
    assert shown.startswith("python -m pytest tests/test_0.py")
    assert shown.endswith("-k 'not slow' | tail -3")
    assert " … " in shown


def test_output_redirection_and_chains_are_flagged() -> None:
    assert "redirects output" in mod.command_flags("python check_output.py > results.txt")
    assert "`;` chain: the exit code is the last command's" in mod.command_flags("pytest -q; echo done")
    # Quoted text and heredoc bodies are not the command's shape.
    assert mod.command_flags('python -c "print(1 > 0); x = 1"') == []
    assert "redirects output" not in mod.command_flags("pytest -q 2>&1")


def test_wrappers_shell_bodies_and_compound_commands_are_seen_through() -> None:
    for command in (
        'bash -c "pytest || true"', "sh -c 'pytest -q'", "( pytest -q || true )", "{ pytest -q; } || :",
        "if pytest -q; then echo ok; fi", "sudo -u ci pytest", "nice -n 5 pytest", "env -i pytest",
        "stdbuf -oL pytest -q", "timeout -s KILL 900 pytest", "time pytest -q", "xargs pytest < list",
        "exec pytest", "PYTEST_ADDOPTS='-k fast' pytest",
    ):
        assert mod.is_check(command), command
    assert not mod.is_check("command -v pytest")
    assert "`|| true` hides a failure" in mod.command_flags('bash -c "pytest || true"')
    assert "`|| true` hides a failure" in mod.command_flags("{ pytest -q; } || :")
    assert "selects or deselects tests" in mod.command_flags("PYTEST_ADDOPTS='-k fast' pytest")
    assert "selects or deselects tests" in mod.command_flags("sh -c 'pytest -q -k \"not slow\"'")


def test_evaluations_are_named_by_the_script_they_run() -> None:
    for command in (
        "python eval.py", "python evaluate.py --split test", "python -m bench.run_bench",
        "bash scripts/benchmark.sh", "python train_lora.py", "python -m argus.tools.pptx_export deck.pptx",
    ):
        assert mod.is_evaluation(command), command
    for command in ("cat eval.py", "vim train.py", "python retrain.py", "pytest -q"):
        assert not mod.is_evaluation(command), command


def test_checks_are_classified_by_the_command_they_run() -> None:
    for command in (
        "pytest -q", "python -m pytest tests", "python3 -m unittest discover -s tests -v",
        "cd /app && timeout 900 /app/.venv/bin/python -m pytest -q", "npm test", "npm run test:unit",
        "yarn test", "cargo test", "go test ./...", "make check", "make -j4 test", "tox -e py312",
        "./run_tests.sh", "bash scripts/verify.sh", "/app/.venv/bin/python /app/.argus/check_notation.py",
        "uv run pytest", "node --test", "PYTHONPATH=src python tests/test_api.py",
    ):
        assert mod.is_check(command), command
    for command in (
        "sed -i 's/assert w == 3/assert w >= 0/' tests/test_watermark.py", "echo 'check: 7 passed'",
        "cat tests/test_api.py", "wc -l data/train.tsv", "git checkout tests/test_api.py",
        "python train.py", "python -c 'import pytest'", "ls tests/", "grep -rn check src",
        "python3 - <<'PY'\nimport unittest\nPY",
    ):
        assert not mod.is_check(command), command


def test_a_shadow_log_beside_the_mission_packet_is_never_read(tmp_path: Path) -> None:
    project = tmp_path / "project"
    life_dir = project / "handoffs" / "m1"
    _write_events(project / "events.jsonl", _probe_rows())
    _write_events(life_dir / "events.jsonl", [
        {"type": "round.start", "ts": T0, "round_index": 1},
        _start(T0 + 1, "python -m pytest -q", "z"),
        _result(T0 + 2, "z", 0, "7 passed in 0.2s"),
    ])
    request = RoundEvidenceRequest(
        workdir=tmp_path / "ws", life_dir=life_dir, round_index=1,
        events_path=project / "events.jsonl",
    )

    evidence = spec_checks.round_log_evidence(request)

    assert evidence is not None
    assert "7 passed in 0.2s" not in evidence.reviewer_text
    assert "1 failed, 6 passed" in evidence.reviewer_text
    # Without the host's own log path, the provider reads nothing at all.
    request = RoundEvidenceRequest(workdir=tmp_path / "ws", life_dir=life_dir, round_index=1)
    assert spec_checks.round_log_evidence(request) is None


def test_a_second_result_for_the_same_call_is_shown_as_a_conflict(tmp_path: Path) -> None:
    rows = _probe_rows()
    rows.append(_result(T0 + 2.5, "a", 0, "7 passed"))
    events = _write_events(tmp_path / "events.jsonl", rows)

    text = mod.render_round_log(tmp_path / "ws", 1, events_path=events)

    line = next(line for line in text.splitlines() if line.startswith(f"  - `{PYTEST} 2>&1 | tail -5`"))
    assert "exit 1 / exit 0 (conflicting results recorded)" in line
    assert "conflicting results recorded for one call" in line
    assert "output reported with result 1 (exit 1): 1 failed, 6 passed in 0.4s" in text
    assert "output reported with result 2 (exit 0): 7 passed" in text


def test_a_result_without_its_start_is_unverified(tmp_path: Path) -> None:
    rows = [*_probe_rows(), _result(T0 + 9, "ghost", 0, "42 passed")]
    events = _write_events(tmp_path / "events.jsonl", rows)
    text = mod.render_round_log(tmp_path / "ws", 1, events_path=events)
    assert "unverified: no start was reported for this call" in text

    capture = CommandCapture(label="engineer-r1")
    capture.start("k1", "command", "pytest -q", ts=T0 + 1)
    capture.finish("k1", exit_code=0, failed=False, output="7 passed", text="pytest -q -k fast")
    [run] = capture.runs()
    assert run.unverified == "the result names a different command than its start"
    capture.start("k1", "command", "pytest tests/other.py", ts=T0 + 2)
    assert capture.runs()[0].unverified == "the result names a different command than its start"


def test_the_record_keeps_the_newest_calls_and_says_how_many_it_dropped(tmp_path: Path, monkeypatch) -> None:
    from argus.core import command_record

    monkeypatch.setattr(command_record, "_MAX_RUNS", 3)
    capture = CommandCapture(label="engineer-r1")
    for index in range(5):
        capture.start(f"c{index}", "command", f"pytest -q tests/test_{index}.py", ts=T0 + index)
    assert [run.call_id for run in capture.runs()] == ["c2", "c3", "c4"]
    assert capture.dropped == 2

    text = mod.render_round_log(tmp_path, 1, runs=capture.runs(), dropped=capture.dropped)
    assert "- 2 earlier commands not shown; the record keeps the newest." in text


def test_round_log_names_longest_commands_and_outside_paths(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    (workdir / "results").mkdir(parents=True)
    rows = [
        _event(T0 - 100, "command_execution", "python old_round.py"),  # before this round
        {"type": "round.start", "ts": T0, "round_index": 1},
        _event(T0 + 1, "tool_use", 'read: {"path": "METHOD.md"}', tool="read"),
        _event(T0 + 2, "command_execution", ".venv/bin/python -m pytest tests/spec"),
        _event(T0 + 10, "command_execution", "CUDA_VISIBLE_DEVICES=2 .venv/bin/python scripts/eval_ruler.py --config c.yaml"),
        _event(T0 + 68, "command_execution", "ls -la /data/other_user/models--Llama-3-8B/snapshots"),
        _event(T0 + 70, "tool_use", 'write: {"path": "results/summary.json"}', tool="write"),
        _event(T0 + 71, "command_execution", "cat results/summary.json"),
        _event(T0 + 80, "command_execution", "ls -la", layer="manager"),  # another role, ignored
    ]
    events = _write_events(tmp_path / "project" / "events.jsonl", rows)

    text = mod.render_round_log(workdir, 1, events_path=events)

    assert "4 shell commands, 1 file reads, 1 writes over 1.2 min" in text
    assert "old_round.py" not in text
    assert "ran ≤58 s (time to the next action): `CUDA_VISIBLE_DEVICES=2 .venv/bin/python scripts/eval_ruler.py --config c.yaml`" in text
    assert "  - `.venv/bin/python -m pytest tests/spec`: no result recorded" in text
    assert "- evaluations, benchmarks and training runs invoked" in text
    assert "  - `CUDA_VISIBLE_DEVICES=2 .venv/bin/python scripts/eval_ruler.py --config c.yaml`: no result recorded" in text
    assert "/data/other_user/models--Llama-3-8B/snapshots" in text
    assert "manager" not in text


def test_an_older_log_without_call_ids_still_joins_a_failure(tmp_path: Path) -> None:
    events = _write_events(tmp_path / "events.jsonl", [
        {"type": "round.start", "ts": T0, "round_index": 1},
        _event(T0 + 1, "command_execution", "make build"),
        _event(T0 + 2, "command_execution", "make build", status="failed", exit_code=1),
        _event(T0 + 3, "command_execution", "python -m unittest", status="completed", exit_code=0, output_excerpt="OK"),
    ])

    text = mod.render_round_log(tmp_path, 1, events_path=events)

    assert "2 shell commands" in text
    assert "  - `python -m unittest`: exit 0" in text
    assert "  - `make build`: exit 1" in text


def test_provider_reads_host_memory_from_the_request(tmp_path: Path) -> None:
    capture = CommandCapture(label="engineer-r3")
    capture.start("p1", "command", "python scripts/run_positive_control.py", ts=T0 + 5)
    capture.finish("p1", exit_code=0, failed=False, output="positive control: 0.91")
    capture.start("p2", "command", "cat results/positive_control_results.json", ts=T0 + 62)
    request = RoundEvidenceRequest(
        workdir=tmp_path, life_dir=tmp_path / "x", round_index=3, command_runs=tuple(capture.runs()),
    )

    evidence = spec_checks.round_log_evidence(request)

    assert evidence is not None and evidence.engineer_note == ""
    assert "run_positive_control.py" in evidence.reviewer_text and "ran ≤57 s" in evidence.reviewer_text
    assert "positive control: 0.91" in evidence.reviewer_text


def test_provider_is_silent_without_a_round(tmp_path: Path) -> None:
    events = _write_events(tmp_path / "events.jsonl", [_event(1.0, "command_execution", "echo hi")])
    request = RoundEvidenceRequest(workdir=tmp_path, life_dir=tmp_path, round_index=1, events_path=events)
    assert spec_checks.round_log_evidence(request) is None  # no round.start or mission start to anchor the window


def test_the_research_vertical_registers_the_provider_and_the_renderer_knows_no_higher_layer() -> None:
    import argus.verticals.research  # noqa: F401 - importing the vertical registers its providers
    from argus.engineer.round_evidence import registered_round_evidence_providers

    assert spec_checks.round_log_evidence in registered_round_evidence_providers()
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "engineer.round_evidence" not in source and "register_round_evidence_provider" not in source
