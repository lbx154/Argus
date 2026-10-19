"""Tests for argus.skills.run_contract (RUN_CONTRACT + feasibility packet)."""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.file_facts import file_facts
from argus.skills.run_contract import (
    DEFAULT_RUN_CONTRACT_PATH,
    LaunchKnobs,
    RunContract,
    build_feasibility_packet_from_run,
    build_supervised_feasibility_packet_from_run,
    check_full_run_launch,
    diff_launch_against_contract,
    distinct_task_ids,
    load_feasibility_packet,
    load_run_contract,
    main,
    validate_feasibility_packet,
)


TASKS = sorted(f"math_{i}" for i in range(1200))
FEW_TASKS = sorted(f"math_{i}" for i in range(50))


def _contract(**over) -> RunContract:
    base = dict(
        model_id="Qwen/Qwen3-14B-Instruct",
        lr=5e-6,
        group_size=8,
        total_steps=1200,
        batch_size=1,
        curriculum_slice_id="math1200",
        curriculum_task_ids=TASKS,
        distinct_tasks=1200,
        seed=42,
        scale="full",
    )
    base.update(over)
    return RunContract(**base)


def _curriculum(tmp_path: Path, task_ids: list[str] = TASKS) -> str:
    path = tmp_path / "slice.json"
    path.write_text(json.dumps({"task_ids": task_ids}), encoding="utf-8")
    return str(path)


def _good_packet(**over) -> dict:
    base = dict(
        curriculum_task_ids=TASKS,
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


# --- contract loading -------------------------------------------------------

def test_load_contract_round_trips_the_admitted_task_ids(tmp_path):
    p = tmp_path / "RUN_CONTRACT.json"
    p.write_text(json.dumps(_contract().to_dict()), encoding="utf-8")
    loaded, issues = load_run_contract(p)
    assert issues == []
    assert loaded == _contract()


def test_a_contract_frozen_before_task_ids_were_kept_must_be_refrozen(tmp_path):
    data = _contract().to_dict()
    del data["curriculum_task_ids"]
    data["curriculum_hash"] = "c" * 64
    p = tmp_path / "RUN_CONTRACT.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    loaded, issues = load_run_contract(p)
    assert loaded is None
    assert issues[0].code == "contract_incomplete"
    assert "re-freeze" in issues[0].detail


def test_load_contract_missing_and_incomplete(tmp_path):
    loaded, issues = load_run_contract(tmp_path / "nope.json")
    assert loaded is None and issues[0].code == "contract_missing"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"model_id": "x"}), encoding="utf-8")
    loaded, issues = load_run_contract(bad)
    assert loaded is None and any(i.code == "contract_incomplete" for i in issues)


# --- curriculum identity ----------------------------------------------------

def test_curriculum_identity_is_the_distinct_task_set(tmp_path):
    assert distinct_task_ids(Path(_curriculum(tmp_path, ["t2", "t1", "t1"]))) == ["t1", "t2"]
    assert distinct_task_ids(Path(_curriculum(tmp_path, ["t1", "t3"]))) == ["t1", "t3"]


# --- launch drift -----------------------------------------------------------

def test_diff_launch_clean(tmp_path):
    c = _contract()
    knobs = LaunchKnobs(lr=5e-6, group_size=8, total_steps=1200, batch_size=1,
                        model_id="/models/Qwen3-14B-Instruct/snap",
                        curriculum=_curriculum(tmp_path))
    assert diff_launch_against_contract(knobs, c) == []


def test_diff_launch_lr_drift(tmp_path):
    c = _contract()
    knobs = LaunchKnobs(lr=3e-5, curriculum=_curriculum(tmp_path))
    codes = {i.code for i in diff_launch_against_contract(knobs, c)}
    assert "launch_lr_drift" in codes


def test_diff_launch_missing_curriculum_and_model_drift():
    c = _contract()
    knobs = LaunchKnobs(model_id="Qwen/Qwen3-14B-Base")
    codes = {i.code for i in diff_launch_against_contract(knobs, c)}
    assert "launch_no_curriculum" in codes
    assert "launch_model_drift" in codes


def test_diff_launch_on_a_different_curriculum_drifts(tmp_path):
    c = _contract()
    knobs = LaunchKnobs(curriculum=_curriculum(tmp_path, FEW_TASKS))
    codes = {i.code for i in diff_launch_against_contract(knobs, c)}
    assert "launch_curriculum_drift" in codes


def test_diff_launch_step_and_group_drift(tmp_path):
    c = _contract()
    knobs = LaunchKnobs(group_size=16, total_steps=200, curriculum=_curriculum(tmp_path))
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
    p.write_text(json.dumps(_good_packet(curriculum_task_ids=FEW_TASKS)), encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    codes = {i.code for i in validate_feasibility_packet(packet, c)}
    assert "packet_curriculum_mismatch" in codes


def test_packet_low_diversity_memorisation(tmp_path):
    # 1200 steps * batch 1 = 1200 prompt draws over only 50 distinct tasks => 24x repeat.
    c = _contract(distinct_tasks=50, curriculum_task_ids=FEW_TASKS)
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(_good_packet(curriculum_task_ids=FEW_TASKS, distinct_tasks=50)),
                 encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    codes = {i.code for i in validate_feasibility_packet(packet, c)}
    assert "curriculum_low_diversity" in codes


def test_packet_low_diversity_waived_when_smoke_only(tmp_path):
    c = _contract(distinct_tasks=50, curriculum_task_ids=FEW_TASKS)
    p = tmp_path / "packet.json"
    p.write_text(json.dumps(_good_packet(curriculum_task_ids=FEW_TASKS, distinct_tasks=50,
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
                        model_id="Qwen/Qwen3-14B-Instruct",
                        curriculum=_curriculum(tmp_path))
    reject, concern = check_full_run_launch(contract_path=cpath, packet_path=ppath, knobs=knobs)
    assert reject is False and concern == ""


def test_check_launch_rejects_missing_contract(tmp_path):
    knobs = LaunchKnobs(curriculum=_curriculum(tmp_path))
    reject, concern = check_full_run_launch(
        contract_path=tmp_path / "nope.json", packet_path=None, knobs=knobs)
    assert reject is True and "RUN_CONTRACT" in concern


def test_check_launch_rejects_missing_packet(tmp_path):
    cpath = _write_contract(tmp_path)
    knobs = LaunchKnobs(lr=5e-6, curriculum=_curriculum(tmp_path))
    reject, concern = check_full_run_launch(contract_path=cpath, packet_path=None, knobs=knobs)
    assert reject is True and "feasibility packet" in concern


def test_check_launch_rejects_lr_drift(tmp_path):
    cpath = _write_contract(tmp_path)
    ppath = tmp_path / "packet.json"
    ppath.write_text(json.dumps(_good_packet()), encoding="utf-8")
    knobs = LaunchKnobs(lr=3e-5, group_size=8, total_steps=1200, batch_size=1,
                        model_id="Qwen/Qwen3-14B-Instruct",
                        curriculum=_curriculum(tmp_path))
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
        run, curriculum_task_ids=TASKS, total_steps=1200, batch_size=1,
        group_size=8, distinct_tasks=1200)
    assert packet.probe_steps == 10
    assert abs(packet.reward_mean - 0.4) < 1e-9
    assert abs(packet.advantage_span_max - 1.9) < 1e-9
    assert packet.max_repetition == 1.0


def _write_supervised_probe(tmp_path: Path, contract: RunContract) -> Path:
    run = tmp_path / "sft-probe"
    run.mkdir()
    rows_path = tmp_path / "structural-study-v1.jsonl"
    unique_count = contract.total_steps * contract.batch_size
    rows_path.write_text(
        "".join(
            json.dumps({"unique_example_id": f"example-{index}"}) + "\n"
            for index in range(unique_count)
        ),
        encoding="utf-8",
    )
    (run / "stdout.log").write_text(
        "\n".join(
            repr({"loss": str(3.0 - step / 10), "grad_norm": str(step / 2)})
            for step in range(1, 7)
        ),
        encoding="utf-8",
    )
    (run / "metrics.json").write_text(json.dumps({
        "training_loss": 2.0,
        "finite_update": True,
        "parameter_delta_norm": 0.37,
    }), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({
        "steps": contract.total_steps,
        "effective_batch_size": contract.batch_size,
        "terminal_state": "completed",
        "independent_task_family_count": contract.distinct_tasks,
        "unique_example_count": unique_count,
        "execution_example_count": unique_count,
        "repeat_policy": "without-replacement",
        "maximum_occurrences_per_unique_example": 1,
        "materialized_rows_path": str(rows_path),
    }), encoding="utf-8")
    return run


def test_supervised_packet_validates_recorded_sft_evidence(tmp_path):
    contract = _contract()
    run = _write_supervised_probe(tmp_path, contract)
    packet = build_supervised_feasibility_packet_from_run(run, contract=contract)
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet.to_dict()), encoding="utf-8")
    loaded, issues = load_feasibility_packet(packet_path)
    assert loaded is not None and issues == []
    assert validate_feasibility_packet(loaded, contract) == []
    assert loaded.probe_steps == 6
    assert loaded.finite_update is True


def test_supervised_packet_rejects_non_numeric_manifest_facts(tmp_path):
    contract = _contract()
    run = _write_supervised_probe(tmp_path, contract)
    packet = build_supervised_feasibility_packet_from_run(run, contract=contract)
    manifest_path = Path(packet.probe_manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    packet.probe_manifest_facts = file_facts(manifest_path)

    issues = validate_feasibility_packet(packet, contract)

    assert any(issue.code == "probe_manifest_malformed" for issue in issues)


def test_supervised_packet_accepts_three_families_with_12288_unique_examples(
    tmp_path,
):
    contract = _contract(
        total_steps=256,
        batch_size=64,
        distinct_tasks=3,
    )
    run = tmp_path / "sft-probe"
    run.mkdir()
    rows_path = tmp_path / "structural-study-v1.jsonl"
    rows_path.write_text(
        "".join(
            json.dumps({"unique_example_id": f"example-{index % 12_288}"}) + "\n"
            for index in range(16_384)
        ),
        encoding="utf-8",
    )
    (run / "stdout.log").write_text(
        "\n".join(
            repr({"loss": str(3.0 - step / 10), "grad_norm": str(step / 2)})
            for step in range(1, 7)
        ),
        encoding="utf-8",
    )
    (run / "metrics.json").write_text(json.dumps({
        "finite_update": True,
        "parameter_delta_norm": 0.37,
    }), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({
        "steps": contract.total_steps,
        "effective_batch_size": contract.batch_size,
        "terminal_state": "completed",
        "independent_task_family_count": 3,
        "unique_example_count": 12_288,
        "execution_example_count": 16_384,
        "repeat_policy": "balanced-round-robin",
        "maximum_occurrences_per_unique_example": 2,
        "materialized_rows_path": str(rows_path),
    }), encoding="utf-8")

    packet = build_supervised_feasibility_packet_from_run(run, contract=contract)

    assert packet.distinct_tasks == 3
    assert packet.unique_example_count == 12_288
    assert packet.max_repetition == 2
    assert validate_feasibility_packet(packet, contract) == []


def test_supervised_packet_rejects_missing_example_counts(tmp_path):
    contract = _contract()
    run = _write_supervised_probe(tmp_path, contract)
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["unique_example_count"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        build_supervised_feasibility_packet_from_run(run, contract=contract)
    except ValueError as exc:
        assert "unique_example_count" in str(exc)
    else:
        raise AssertionError("missing unique_example_count must fail closed")


def test_supervised_packet_rejects_forged_example_count(tmp_path):
    contract = _contract()
    run = _write_supervised_probe(tmp_path, contract)
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["unique_example_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        build_supervised_feasibility_packet_from_run(run, contract=contract)
    except ValueError as exc:
        assert "do not match materialized rows" in str(exc)
    else:
        raise AssertionError("forged unique_example_count must fail closed")


def test_supervised_packet_rejects_tampered_trace(tmp_path):
    contract = _contract()
    run = _write_supervised_probe(tmp_path, contract)
    packet = build_supervised_feasibility_packet_from_run(run, contract=contract)
    (run / "stdout.log").write_text(
        "{'loss': '0.0', 'grad_norm': '0.0'}\n", encoding="utf-8")
    codes = {issue.code for issue in validate_feasibility_packet(packet, contract)}
    assert "probe_trace_changed" in codes


def test_supervised_packet_rejects_missing_parameter_update(tmp_path):
    contract = _contract()
    run = _write_supervised_probe(tmp_path, contract)
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    metrics["finite_update"] = False
    metrics["parameter_delta_norm"] = 0.0
    (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    packet = build_supervised_feasibility_packet_from_run(run, contract=contract)
    codes = {issue.code for issue in validate_feasibility_packet(packet, contract)}
    assert "probe_no_parameter_update" in codes


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
    assert issues == [] and loaded is not None
    assert loaded.curriculum_task_ids == TASKS and loaded.distinct_tasks == 1200


def test_packet_string_false_smoke_only_does_not_waive(tmp_path):
    # Regression: bool("false") is True in Python. A packet that records the
    # *string* "false" must NOT waive the diversity/saturation anti-fraud
    # checks (fail-closed).
    c = _contract(distinct_tasks=50, curriculum_task_ids=FEW_TASKS)
    p = tmp_path / "packet.json"
    raw = _good_packet(curriculum_task_ids=FEW_TASKS, distinct_tasks=50)
    raw["smoke_only"] = "false"
    p.write_text(json.dumps(raw), encoding="utf-8")
    packet, _ = load_feasibility_packet(p)
    assert packet is not None
    assert packet.smoke_only is False
    # low-diversity check still fires (not waived).
    assert validate_feasibility_packet(packet, c) != []


def test_packet_bool_true_variants_waive(tmp_path):
    c = _contract(distinct_tasks=50, curriculum_task_ids=FEW_TASKS)
    for truthy in (True, "true", "True", 1):
        p = tmp_path / "packet.json"
        raw = _good_packet(curriculum_task_ids=FEW_TASKS, distinct_tasks=50)
        raw["smoke_only"] = truthy
        p.write_text(json.dumps(raw), encoding="utf-8")
        packet, _ = load_feasibility_packet(p)
        assert packet is not None and packet.smoke_only is True
        assert validate_feasibility_packet(packet, c) == []
