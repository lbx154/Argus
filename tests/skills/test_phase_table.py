"""The phase table: where one step's time goes, from the run's own log.

s-efe9deac trained on native generation for 22 hours while its own phase logs
said generation took 88-97% of every step; the numbers only ever appeared as
prose with disclaimers. The fixtures are those logs trimmed to phase, seconds,
generated tokens and, where the trainer wrote it, start time: readiness attempts
002 (09:52) and 004, the CARE seed-0 training run, the shared-vLLM HF and parity
producers, and verl's step line from CARE attempt-007, which ran on vLLM. In the
parity producer engine_generation and weight_sync run inside generation, and in
verl generate_sequences and reshard run inside gen.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "argus" / "verticals" / "research" / "skills" / "engineer" / "training_scripts" / "phase_table.py"
)
LOGS = Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "phase_logs"


def _module():
    spec = importlib.util.spec_from_file_location("phase_table", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, check=False,
    )


def _profile(log: Path, *args: str) -> dict:
    run = _run(log, *args, "--json")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.parametrize(("log", "share", "tokens_per_second"), [
    ("readiness-candidate-attempt-002.jsonl", 0.968, 109.6),
    ("readiness-candidate-attempt-004.jsonl", 0.915, 78.9),
    ("shared-vllm-hf.jsonl", 0.889, 91.9),
    ("shared-vllm-parity-attempt-002.jsonl", 0.687, 495.7),
])
def test_generation_share_and_speed_of_the_real_runs(log, share, tokens_per_second) -> None:
    profile = _profile(LOGS / log)

    assert (profile["dominant"], profile["share"]) == ("generation", share)
    assert profile["tokens_per_second"] == tokens_per_second
    assert profile["kind_from_name"] is True
    assert str(SCRIPT) in profile["command"] and str(LOGS / log) in profile["command"]


def test_the_care_seed0_run_spent_93_percent_generating(tmp_path) -> None:
    log = tmp_path / "phases.jsonl"
    log.write_bytes(gzip.decompress((LOGS / "care-seed0-train.jsonl.gz").read_bytes()))

    profile = _profile(log)

    assert (profile["dominant"], profile["share"], profile["tokens_per_second"]) == ("generation", 0.933, 80.5)


def test_the_table_gives_shares_speed_and_the_bound_and_nothing_else() -> None:
    run = _run(LOGS / "readiness-candidate-attempt-002.jsonl")

    assert run.returncode == 0
    lines = run.stdout.splitlines()
    assert any(line.split()[:4] == ["generation", "generation*", "224.3s", "96.8%"] for line in lines)
    assert "* kind read from the phase name; phase() records it" in lines
    assert "generation: 109.6 tokens/s (engine: unstated)" in lines
    assert "generation: 96.8% of the step; the step is at most 31.2x faster if it took no time" in lines
    assert not any(word in run.stdout for word in ("usually", "calls for", "engine does not help", "advice"))


def test_a_phase_inside_another_is_summed_once() -> None:
    run = _run(LOGS / "shared-vllm-parity-attempt-002.jsonl")

    lines = run.stdout.splitlines()
    assert ("(not summed, their time is in other rows: engine_generation, optimizer_update_total, "
            "training_step_including_collection_if_any, weight_sync; --skip sets the names)") in lines
    assert "phases cover 96% of 48s wall" in lines
    assert "generation: 68.7% of the step; the step is at most 3.2x faster if it took no time" in lines


def test_a_nested_phase_call_is_summed_once(tmp_path) -> None:
    log = tmp_path / "phases.jsonl"
    module = _module()
    with module.phase("rollout", "generation", log):
        with module.phase("tool_call", "environment", log):
            pass

    profile = _profile(log)

    assert (profile["dominant"], profile["share"]) == ("generation", 1.0)


def test_on_vllm_care_generation_is_a_fifth_of_the_step() -> None:
    profile = _profile(LOGS / "care-attempt-007.log", "--engine", "vllm")
    lines = _run(LOGS / "care-attempt-007.log").stdout.splitlines()

    assert (profile["dominant"], profile["share"], profile["engine"]) == ("training compute", 0.456, "vllm")
    assert "tokens_per_second" not in profile
    assert "generation: 20.4% of the step; the step is at most 1.3x faster if it took no time" in lines
    assert "(not summed, their time is in other rows: generate_sequences, reshard, step; --skip sets the names)" in lines


@pytest.mark.parametrize(("name", "kind"), [
    ("agent_tool_exec", "environment"),
    ("agent_env_step", "environment"),
    ("agentToolExec", "environment"),
    ("prefill", "generation"),
    ("generate_sequences", "generation"),
    ("rollout_generation", "generation"),
    ("timing_s/gen", "generation"),
    ("ref", "training compute"),
    ("old_log_prob", "training compute"),
    ("save_checkpoint", "checkpoint and logging"),
    ("startup_and_model_load", "startup"),
])
def test_names_are_read_word_by_word(name, kind) -> None:
    assert _module().kind_of(name) == kind


def test_a_recorded_kind_wins_over_the_name(tmp_path) -> None:
    log = tmp_path / "phases.jsonl"
    with _module().phase("agent_turn", "generation", log) as counts:
        counts["generated_tokens"] = 10
    row = json.loads(log.read_text(encoding="utf-8"))
    assert (row["phase"], row["kind"], row["generated_tokens"]) == ("agent_turn", "generation", 10)

    log.write_text("".join(json.dumps(row) + "\n" for row in (
        {"phase": "agent_turn", "kind": "generation", "seconds": 3.0},
        {"phase": "score_turn", "kind": "environment", "seconds": 1.0},
    )), encoding="utf-8")
    profile = _profile(log)

    assert (profile["dominant"], profile["share"]) == ("generation", 0.75)
    assert "kind_from_name" not in profile


def test_it_only_reports_and_always_exits_zero(tmp_path) -> None:
    empty = tmp_path / "empty.log"
    empty.write_text("nothing timed here\n", encoding="utf-8")
    startup = tmp_path / "startup.jsonl"
    startup.write_text(json.dumps({"phase": "startup_and_model_load", "seconds": 2.0}) + "\n", encoding="utf-8")

    for log in (empty, startup, tmp_path / "missing.log"):
        run = _run(log)
        assert run.returncode == 0 and run.stderr == "" and str(log) in run.stdout
