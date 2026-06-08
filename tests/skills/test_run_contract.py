"""Tests for argus_skill.skills.run_contract (RUN_CONTRACT + feasibility packet)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.run_contract import (
    DEFAULT_RUN_CONTRACT_PATH,
    LaunchKnobs,
    RunContract,
    build_feasibility_packet_from_run,
    check_full_run_launch,
    compute_contract_hash,
    compute_curriculum_hash,
    diff_launch_against_contract,
    load_feasibility_packet,
    load_run_contract,
    main,
    validate_feasibility_packet,
)


def _contract(**over) -> RunContract:
    base = dict(
        model_id="Qwen/Qwen3-14B-Instruct",
        lr=5e-6,
        group_size=8,
        total_steps=1200,
        batch_size=1,
        curriculum_slice_id="math1200",
        curriculum_hash="c" * 64,
        distinct_tasks=1200,
        seed=42,
        scale="full",
    )
    base.update(over)
    return RunContract(**base).with_hash()


def _good_packet(**over) -> dict:
    base = dict(
        curriculum_hash="c" * 64,
        distinct_tasks=1200,
        total_steps=1200,
        batch_size=1,
        group_size=8,
        reward_mean=0.45,
        reward_std=0.5,
        per_group_reward_std_mean=0.4,
        advantage_span_max=1.3,
        frac_reward_zero_std=0.2,
        probe_steps=20,
    )
    base.update(over)
    return base


# --- contract hashing -------------------------------------------------------

def test_contract_hash_stable_and_order_independent_on_floats():
    c = _contract()
    assert c.contract_hash == compute_contract_hash(c.to_dict())
    # 5e-6 and 0.000005 are the same locked value -> same hash.
    assert compute_contract_hash(_contract(lr=0.000005).to_dict()) == c.contract_hash


def test_contract_hash_changes_when_locked_field_changes():
    base = _contract().contract_hash
    assert _contract(lr=3e-5).contract_hash != base
    assert _contract(group_size=16).contract_hash != base
    assert _contract(curriculum_hash="d" * 64).contract_hash != base


def test_load_contract_detects_tamper(tmp_path):
    c = _contract()
    p = tmp_path / "RUN_CONTRACT.json"
    data = c.to_dict()
    data["lr"] = 3e-5  # edit a locked field without re-hashing
    p.write_text(json.dumps(data), encoding="utf-8")
    loaded, issues = load_run_contract(p)
    assert loaded is not None
    assert any(i.code == "contract_hash_mismatch" for i in issues)


def test_load_contract_missing_and_incomplete(tmp_path):
    loaded, issues = load_run_contract(tmp_path / "nope.json")
    assert loaded is None and issues[0].code == "contract_missing"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"model_id": "x"}), encoding="utf-8")
    loaded, issues = load_run_contract(bad)
    assert loaded is None and any(i.code == "contract_incomplete" for i in issues)


# --- curriculum hashing -----------------------------------------------------

def test_curriculum_hash_set_order_independent_seed_sensitive():
    a = compute_curriculum_hash(["t2", "t1", "t1"], seed=42)
    b = compute_curriculum_hash(["t1", "t2"], seed=42)
    assert a == b  # set-based, dup/order independent
    assert compute_curriculum_hash(["t1", "t2"], seed=7) != b
    assert compute_curriculum_hash(["t1", "t3"], seed=42) != b


# --- launch drift -----------------------------------------------------------

def test_diff_launch_clean():
    c = _contract()
    knobs = LaunchKnobs(lr=5e-6, group_size=8, total_steps=1200, batch_size=1,
                        model_id="/models/Qwen3-14B-Instruct/snap", curriculum_hash="c" * 64)
    assert diff_launch_against_contract(knobs, c) == []


def test_diff_launch_lr_drift():
    c = _contract()
    knobs = LaunchKnobs(lr=3e-5, curriculum_hash="c" * 64)
    codes = {i.code for i in diff_launch_against_contract(knobs, c)}
    assert "launch_lr_drift" in codes


def test_diff_launch_missing_curriculum_hash_and_model_drift():
    c = _contract()
    knobs = LaunchKnobs(model_id="Qwen/Qwen3-14B-Base")
    codes = {i.code for i in diff_launch_against_contract(knobs, c)}
    assert "launch_no_curriculum_hash" in codes
    assert "launch_model_drift" in codes


def test_diff_launch_step_and_group_drift():
    c = _contract()
    knobs = LaunchKnobs(group_size=16, total_steps=200, curriculum_hash="c" * 64)
    codes = {i.code for i in diff_launch_against_contract(knobs, c)}
    assert "launch_group_size_drift" in codes
    assert "launch_total_steps_drift" in codes


# --- feasibility packet -----------------------------------------------------

def test_packet_valid(tmp_path):
    c = _contract()
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(_good_packet()), encoding="utf-8")
    packet, issues = load_feasibility_packet(p)
    assert packet is not None and issues == []
    assert validate_feasibility_packet(packet, c) == []


def test_packet_curriculum_mismatch(tmp_path):
    c = _contract()
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(_good_packet(curriculum_hash="z" * 64)), encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    codes = {i.code for i in validate_feasibility_packet(packet, c)}
    assert "packet_curriculum_mismatch" in codes


def test_packet_low_diversity_memorisation(tmp_path):
    # 1200 steps * batch 1 = 1200 prompt draws over only 50 distinct tasks => 24x repeat.
    c = _contract(distinct_tasks=50, curriculum_hash="e" * 64)
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(_good_packet(curriculum_hash="e" * 64, distinct_tasks=50)),
                 encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    codes = {i.code for i in validate_feasibility_packet(packet, c)}
    assert "curriculum_low_diversity" in codes


def test_packet_low_diversity_waived_when_smoke_only(tmp_path):
    c = _contract(distinct_tasks=50, curriculum_hash="e" * 64)
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(_good_packet(curriculum_hash="e" * 64, distinct_tasks=50,
                                         smoke_only=True)), encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    assert validate_feasibility_packet(packet, c) == []


def test_packet_zero_advantage_and_ceiling(tmp_path):
    c = _contract()
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(_good_packet(advantage_span_max=0.0, reward_mean=1.0)),
                 encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    codes = {i.code for i in validate_feasibility_packet(packet, c)}
    assert "probe_zero_advantage" in codes
    assert "probe_reward_ceiling" in codes


def test_packet_probe_too_short(tmp_path):
    c = _contract()
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(_good_packet(probe_steps=2)), encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    codes = {i.code for i in validate_feasibility_packet(packet, c)}
    assert "packet_probe_too_short" in codes


# --- end-to-end launch interlock -------------------------------------------

def _write_contract(tmp_path) -> Path:
    c = _contract()
    p = tmp_path / "RUN_CONTRACT.json"
    p.write_text(json.dumps(c.to_dict()), encoding="utf-8")
    return p


def test_check_launch_ok(tmp_path):
    cpath = _write_contract(tmp_path)
    ppath = tmp_path / "packet.json"
    ppath.write_text(json.dumps(_good_packet()), encoding="utf-8")
    knobs = LaunchKnobs(lr=5e-6, group_size=8, total_steps=1200, batch_size=1,
                        model_id="Qwen/Qwen3-14B-Instruct", curriculum_hash="c" * 64)
    reject, concern = check_full_run_launch(contract_path=cpath, packet_path=ppath, knobs=knobs)
    assert reject is False and concern == ""


def test_check_launch_rejects_missing_contract(tmp_path):
    knobs = LaunchKnobs(curriculum_hash="c" * 64)
    reject, concern = check_full_run_launch(
        contract_path=tmp_path / "nope.json", packet_path=None, knobs=knobs)
    assert reject is True and "RUN_CONTRACT" in concern


def test_check_launch_rejects_missing_packet(tmp_path):
    cpath = _write_contract(tmp_path)
    knobs = LaunchKnobs(lr=5e-6, curriculum_hash="c" * 64)
    reject, concern = check_full_run_launch(contract_path=cpath, packet_path=None, knobs=knobs)
    assert reject is True and "feasibility packet" in concern


def test_check_launch_rejects_lr_drift(tmp_path):
    cpath = _write_contract(tmp_path)
    ppath = tmp_path / "packet.json"
    ppath.write_text(json.dumps(_good_packet()), encoding="utf-8")
    knobs = LaunchKnobs(lr=3e-5, group_size=8, total_steps=1200, batch_size=1,
                        model_id="Qwen/Qwen3-14B-Instruct", curriculum_hash="c" * 64)
    reject, concern = check_full_run_launch(contract_path=cpath, packet_path=ppath, knobs=knobs)
    assert reject is True and "lr" in concern.lower()


# --- packet builder ---------------------------------------------------------

def test_build_packet_from_run(tmp_path):
    run = tmp_path / "probe"
    run.mkdir()
    rows = []
    for step in range(1, 11):
        rows.append({
            "event": "optimizer_step", "step": step,
            "reward_mean": 0.4, "reward_std": 0.49,
            "frac_reward_zero_std": 0.2,
            "raw_verl_metrics": {
                "critic/advantages/max": 1.2, "critic/advantages/min": -0.7,
            },
        })
    (run / "progress.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    packet = build_feasibility_packet_from_run(
        run, curriculum_hash="c" * 64, total_steps=1200, batch_size=1,
        group_size=8, distinct_tasks=1200)
    assert packet.probe_steps == 10
    assert abs(packet.reward_mean - 0.4) < 1e-9
    assert abs(packet.advantage_span_max - 1.9) < 1e-9
    assert packet.max_repetition == 1.0


# --- CLI --------------------------------------------------------------------

def test_cli_freeze_and_check(tmp_path, capsys):
    curriculum = tmp_path / "slice.json"
    curriculum.write_text(json.dumps(
        {"task_ids": [f"math_{i}" for i in range(1200)]}), encoding="utf-8")
    rc = main([
        "--project-root", str(tmp_path), "freeze",
        "--model", "Qwen/Qwen3-14B-Instruct", "--lr", "5e-6",
        "--group-size", "8", "--total-steps", "1200", "--batch-size", "1",
        "--curriculum", str(curriculum), "--seed", "42", "--scale", "full",
    ])
    assert rc == 0
    assert (tmp_path / DEFAULT_RUN_CONTRACT_PATH).exists()
    loaded, issues = load_run_contract(tmp_path / DEFAULT_RUN_CONTRACT_PATH)
    assert loaded is not None and not any(i.code.startswith("contract_hash") for i in issues)


def test_packet_string_false_smoke_only_does_not_waive(tmp_path):
    # Regression: bool("false") is True in Python. A packet that records the
    # *string* "false" must NOT waive the diversity/saturation anti-fraud
    # checks (fail-closed).
    c = _contract(distinct_tasks=50, curriculum_hash="e" * 64)
    p = tmp_path / "packet.json"
    raw = _good_packet(curriculum_hash="e" * 64, distinct_tasks=50)
    raw["smoke_only"] = "false"
    p.write_text(json.dumps(raw), encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    assert packet is not None
    assert packet.smoke_only is False
    # low-diversity check still fires (not waived).
    assert validate_feasibility_packet(packet, c) != []


def test_packet_bool_true_variants_waive(tmp_path):
    c = _contract(distinct_tasks=50, curriculum_hash="e" * 64)
    for truthy in (True, "true", "True", 1):
        p = tmp_path / "packet.json"
        raw = _good_packet(curriculum_hash="e" * 64, distinct_tasks=50)
        raw["smoke_only"] = truthy
        p.write_text(json.dumps(raw), encoding="utf-8")
        packet, _ = load_feasibility_packet(p)
        assert packet is not None and packet.smoke_only is True
        assert validate_feasibility_packet(packet, c) == []
