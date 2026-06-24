"""Tests for the multi-island workspace + migration mechanics.

Uses a tiny synthetic mission fixture (not the real 486 MB mission) so the tests
are fast and portable. Pins the invariants that keep islands correct: env-parity
files are SYMLINKED byte-identical, per-island state is isolated, the eval script
is namespaced + GPU-pinned (shim stays shared), and migration/reset rank islands
by their OWN floors / frozen counters.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.islands import migration
from argus_skill.islands.workspace import (
    ENV_PARITY_LINKS,
    IslandSpec,
    setup_island,
    verify_parity,
    _template_eval_script,
)

_EVAL = """set -uo pipefail
read -r -d '' RUNSEEDS <<'NODE' || true
cd /scratch/autoresearch
rm -f /scratch/cand_s*.log /scratch/cand_s*.done
g=${GPUS[0]}
for s in $(seq 0 $((N-1))); do CUDA_VISIBLE_DEVICES=$g python /scratch/run_with_shim.py train_candidate.py > /scratch/cand_s$s.log; touch /scratch/cand_s$s.done; done
NODE
sshr "echo '$B' | base64 -d > /scratch/autoresearch/train_candidate.py && echo OK"
sshr "bash /scratch/run_seeds.sh $N"
"""


def _mini_mission(root: Path) -> Path:
    """Build a minimal mission dir that setup_island can scaffold from."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "lib.py").write_text("# frozen harness\nMARKER=1\n", encoding="utf-8")
    (root / "prepare.py").write_text("# frozen prepare\n", encoding="utf-8")
    (root / "code").mkdir(exist_ok=True)
    (root / "code" / "run_with_shim.py").write_text("# shim\n", encoding="utf-8")
    (root / "train.py").write_text("# candidate v0 (global best)\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (root / "eval_solution.sh").write_text(_EVAL, encoding="utf-8")
    (root / "research").mkdir(exist_ok=True)
    (root / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "optimize", "vertical": "nanochat"}), encoding="utf-8"
    )
    # a promoted floor attempt so the seed carries a known score.
    a = root / "attempts" / "a275_floor"
    a.mkdir(parents=True, exist_ok=True)
    (a / "train.candidate.py").write_text("# floor\n", encoding="utf-8")
    (a / "summary.json").write_text(
        json.dumps({"candidate": "a275_floor", "mean_val_bpb": 0.965364, "decision": "promote"}),
        encoding="utf-8",
    )
    return root


# --------------------------------------------------------------------------- #
# eval templating
# --------------------------------------------------------------------------- #
def test_eval_template_namespaces_scratch_and_pins_gpu():
    out = _template_eval_script(_EVAL, "island_3", 5)
    assert "cd /scratch/island_3" in out
    assert "/scratch/island_3/cand_s" in out
    assert "/scratch/island_3/run_seeds.sh" in out
    assert "mkdir -p /scratch/island_3 && echo" in out
    assert "g=5" in out and "GPUS[0]" not in out
    # the frozen shim is SHARED (env-parity) — never namespaced.
    assert "/scratch/run_with_shim.py" in out
    assert "/scratch/island_3/run_with_shim.py" not in out


# --------------------------------------------------------------------------- #
# workspace isolation + env-parity
# --------------------------------------------------------------------------- #
def test_setup_island_symlinks_parity_and_isolates_state(tmp_path):
    mission = _mini_mission(tmp_path / "mission")
    islands = tmp_path / "islands"
    spec = setup_island(
        mission_root=mission, islands_root=islands, island_id="0",
        regime_axis="optimizer", gpu=7,
    )
    # env-parity files are symlinks resolving byte-identical.
    for name in ENV_PARITY_LINKS:
        src = mission / name
        if not src.exists():
            continue
        dst = spec.cwd / name
        assert dst.is_symlink(), f"{name} must be symlinked for parity"
        assert dst.resolve() == src.resolve()
    assert (spec.cwd / "lib.py").read_text() == (mission / "lib.py").read_text()
    assert verify_parity(spec, mission) == []
    # train.py copied (mutable, may diverge), not symlinked.
    assert not (spec.cwd / "train.py").is_symlink()
    # seeded floor + namespaced eval + island marker.
    assert spec.seed_attempt == "a000_seed_parent"
    seed_summary = json.loads((spec.cwd / "attempts" / "a000_seed_parent" / "summary.json").read_text())
    assert seed_summary["mean_val_bpb"] == 0.965364 and seed_summary["decision"] == "promote"
    assert "/scratch/island_0" in (spec.cwd / "eval_solution.sh").read_text()
    assert json.loads((spec.cwd / ".island.json").read_text())["regime_axis"] == "optimizer"
    # fresh research/ carries only the stage pointer, no GROUND_TRUTH/ledger.
    assert (spec.cwd / "research" / "PIPELINE_STATE.json").exists()
    assert not (spec.cwd / "research" / "GROUND_TRUTH.md").exists()


def test_setup_island_is_idempotent_for_reset(tmp_path):
    mission = _mini_mission(tmp_path / "mission")
    islands = tmp_path / "islands"
    s1 = setup_island(mission_root=mission, islands_root=islands, island_id="0",
                      regime_axis="optimizer", gpu=7)
    (s1.cwd / "attempts" / "a001_junk").mkdir(parents=True)
    # rebuild (as reset does) into a different axis → old lineage wiped.
    s2 = setup_island(mission_root=mission, islands_root=islands, island_id="0",
                      regime_axis="data", gpu=7)
    assert not (s2.cwd / "attempts" / "a001_junk").exists()
    assert s2.regime_axis == "data"


# --------------------------------------------------------------------------- #
# migration / reset selection
# --------------------------------------------------------------------------- #
def _fake_island(tmp_path: Path, iid: str, axis: str, *, floor, frozen):
    """Build an island cwd whose attempts/ yield the given floor + frozen count."""
    cwd = tmp_path / "islands" / iid
    cwd.mkdir(parents=True, exist_ok=True)
    # a000 = promoted floor; then `frozen` worse non-promoted attempts after it.
    f = cwd / "attempts" / "a000_floor"
    f.mkdir(parents=True, exist_ok=True)
    (f / "train.candidate.py").write_text(f"# island {iid} best\n", encoding="utf-8")
    (f / "summary.json").write_text(
        json.dumps({"mean_val_bpb": floor, "decision": "promote"}), encoding="utf-8"
    )
    for j in range(frozen):
        d = cwd / "attempts" / f"a{j+1:03d}_x"
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(
            json.dumps({"mean_val_bpb": floor + 0.01, "decision": "reject"}), encoding="utf-8"
        )
    return IslandSpec(island_id=iid, regime_axis=axis, gpu=0, scratch_ns=f"island_{iid}", cwd=cwd)


def test_global_best_and_reset_target(tmp_path):
    specs = [
        _fake_island(tmp_path, "0", "optimizer", floor=0.97, frozen=20),   # stalest, worst
        _fake_island(tmp_path, "1", "architecture", floor=0.95, frozen=2),  # best, fresh
        _fake_island(tmp_path, "2", "data", floor=0.96, frozen=15),         # stale
    ]
    statuses = [migration.read_status(s) for s in specs]
    gb = migration.global_best(statuses)
    assert gb is not None and gb.spec.island_id == "1" and gb.floor == 0.95
    tgt = migration.reset_target(statuses, min_frozen=12, protect_id=gb.spec.island_id)
    assert tgt is not None and tgt.spec.island_id == "0"  # most frozen, not the global best


def test_reset_skips_when_none_stale(tmp_path):
    specs = [
        _fake_island(tmp_path, "0", "optimizer", floor=0.97, frozen=3),
        _fake_island(tmp_path, "1", "architecture", floor=0.95, frozen=1),
    ]
    statuses = [migration.read_status(s) for s in specs]
    assert migration.reset_target(statuses, min_frozen=12, protect_id="1") is None


def test_starved_axis_prefers_unassigned_then_least_covered(tmp_path):
    specs = [
        _fake_island(tmp_path, "0", "optimizer", floor=0.97, frozen=5),
        _fake_island(tmp_path, "1", "architecture", floor=0.95, frozen=5),
    ]
    statuses = [migration.read_status(s) for s in specs]
    ax = migration.starved_axis(statuses, ("optimizer", "architecture", "data", "numerics"))
    assert ax == "data"  # first axis no active island holds


def test_migrate_best_seeds_inspirations(tmp_path):
    specs = [
        _fake_island(tmp_path, "0", "optimizer", floor=0.97, frozen=5),
        _fake_island(tmp_path, "1", "architecture", floor=0.95, frozen=5),
    ]
    statuses = [migration.read_status(s) for s in specs]
    gb = migration.global_best(statuses)
    n = migration.migrate_best(gb, statuses)
    assert n == 1  # copied into the one OTHER island
    insp = specs[0].cwd / "inspirations"
    assert insp.is_dir() and any("global_best_from_1" in p.name for p in insp.iterdir())
