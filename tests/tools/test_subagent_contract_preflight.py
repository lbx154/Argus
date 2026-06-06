"""Tests for the deterministic RUN_CONTRACT launch interlock in subagent."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.run_contract import RunContract, compute_curriculum_hash
from argus_skill.tools.subagent import (
    _flag,
    _is_full_scale_rl,
    _parse_launch_flags,
    _run_contract_preflight,
)

CUR_HASH = compute_curriculum_hash([f"math_{i}" for i in range(1200)], seed=42)


def _write_contract(root: Path) -> None:
    contract = RunContract(
        model_id="Qwen/Qwen3-14B-Instruct",
        lr=5e-6,
        group_size=8,
        total_steps=1200,
        batch_size=1,
        curriculum_slice_id="math1200",
        curriculum_hash=CUR_HASH,
        distinct_tasks=1200,
        seed=42,
        scale="full",
    ).with_hash()
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "RUN_CONTRACT.json").write_text(
        json.dumps(contract.to_dict()), encoding="utf-8")


def _write_packet(root: Path, **over) -> None:
    packet = dict(
        curriculum_hash=CUR_HASH, distinct_tasks=1200, total_steps=1200,
        batch_size=1, group_size=8, reward_mean=0.45, reward_std=0.5,
        per_group_reward_std_mean=0.4, advantage_span_max=1.3,
        frac_reward_zero_std=0.2, probe_steps=20,
    )
    packet.update(over)
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "PACKET.json").write_text(json.dumps(packet), encoding="utf-8")


def _cmd(**flag_over) -> str:
    flags = {
        "--scale": "full", "--num-generations": "8", "--reward": "math",
        "--lr": "5e-6", "--total-training-steps": "1200", "--train-batch-size": "1",
        "--run-contract": "research/RUN_CONTRACT.json",
        "--feasibility-packet": "research/PACKET.json",
        "--curriculum-hash": CUR_HASH,
    }
    flags.update(flag_over)
    parts = ["python", "code/run.py"]
    for k, v in flags.items():
        if v is None:
            continue
        parts += [k, v]
    return " ".join(parts)


# --- detection --------------------------------------------------------------

def test_is_full_scale_rl():
    assert _is_full_scale_rl(_cmd()) is True
    assert _is_full_scale_rl(_cmd(**{"--scale": "pilot"})) is False
    assert _is_full_scale_rl("python code/eval.py --tasks math") is False


def test_flag_alias_resolution():
    flags = _parse_launch_flags(_cmd())
    assert _flag(flags, "group_size") == "8"        # via --num-generations alias
    assert _flag(flags, "total_steps") == "1200"    # via --total-training-steps
    assert _flag(flags, "batch_size") == "1"        # via --train-batch-size


# --- interlock --------------------------------------------------------------

def test_preflight_ok_when_contract_and_packet_match(tmp_path):
    _write_contract(tmp_path)
    _write_packet(tmp_path)
    reject, concern = _run_contract_preflight(_cmd(), str(tmp_path))
    assert reject is False and concern == ""


def test_preflight_rejects_missing_contract(tmp_path):
    reject, concern = _run_contract_preflight(_cmd(), str(tmp_path))
    assert reject is True and "RUN_CONTRACT" in concern


def test_preflight_rejects_lr_drift(tmp_path):
    _write_contract(tmp_path)
    _write_packet(tmp_path)
    reject, concern = _run_contract_preflight(_cmd(**{"--lr": "3e-5"}), str(tmp_path))
    assert reject is True and "lr" in concern.lower()


def test_preflight_rejects_curriculum_drift(tmp_path):
    _write_contract(tmp_path)
    _write_packet(tmp_path)
    reject, concern = _run_contract_preflight(
        _cmd(**{"--curriculum-hash": "f" * 64}), str(tmp_path))
    assert reject is True and "curriculum" in concern.lower()


def test_preflight_rejects_missing_packet(tmp_path):
    _write_contract(tmp_path)
    reject, concern = _run_contract_preflight(
        _cmd(**{"--feasibility-packet": None}), str(tmp_path))
    assert reject is True and "feasibility packet" in concern


def test_preflight_rejects_low_diversity_packet(tmp_path):
    _write_contract(tmp_path)
    _write_packet(tmp_path, distinct_tasks=50)  # 1200 prompts / 50 = 24x repeat
    reject, concern = _run_contract_preflight(_cmd(), str(tmp_path))
    assert reject is True and "diversity" in concern.lower()


def test_preflight_failsoft_on_internal_error(tmp_path, monkeypatch):
    # An unexpected exception in the contract checker must never wedge a launch.
    import argus_skill.skills.run_contract as rc

    def _boom(**_kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(rc, "check_full_run_launch", _boom)
    _write_contract(tmp_path)
    _write_packet(tmp_path)
    reject, concern = _run_contract_preflight(_cmd(), str(tmp_path))
    assert reject is False and concern == ""
