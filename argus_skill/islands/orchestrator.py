"""Island orchestrator — owns the island population (setup, spawn, migrate, reset).

This is NOT the main daemon; it is a separate supervisor process that runs K
island daemons as supervised subagents and periodically applies FunSearch
migration + island-reset. Each island is a stock ``argus-skill --daemon-fg`` in
its own cwd (own life_dir/backlog/floor), seeded toward a regime axis. The
orchestrator never edits an island's candidates — it only copies the
population-best across islands and reseeds the stalest island into a starved axis.

Run:  ``python -m argus_skill.islands --mission <dir> --islands <dir> -n 3 [--dry-run]``
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .migration import (
    global_best,
    migrate_best,
    read_status,
    reset_target,
    starved_axis,
)
from .workspace import DEFAULT_AXES, IslandSpec, setup_island, verify_parity

#: Heavy-reasoning env each island daemon needs (mirrors run-daemon.sh) plus a
#: lower jump threshold so a saturated island reacts faster than the main mission.
ISLAND_ENV = {
    "ARGUS_SKILL_ENGINEER_REASONING_EFFORT": "xhigh",
    "ARGUS_SKILL_REVIEWER_REASONING_EFFORT": "xhigh",
    "ARGUS_SKILL_SCIENTIST_REASONING_EFFORT": "xhigh",
    "ARGUS_SKILL_SKIP_VAULT_PREFLIGHT": "1",
    "ARGUS_META_JUMP_FROZEN_THRESHOLD": "8",
}
ARGUS_BIN = "/home/argustest/miniconda3/bin/argus-skill"
PYBIN = "/home/argustest/miniconda3/bin/python"


def _global_root() -> Path:
    return Path.home() / ".argus-skill"


def _life_dir(cwd: Path) -> Path | None:
    try:
        from ..core.project import project_fingerprint

        fp = project_fingerprint(cwd).fingerprint
        return _global_root() / "projects" / fp
    except Exception:  # noqa: BLE001
        return None


def _objective(regime: str) -> str:
    return (
        "Minimize the nanochat validation bits-per-byte (val_bpb) of a small GPT "
        "trained from scratch under the FIXED 300s single-B200 scorer; edit ONLY "
        "train.py, keep lib.py/data/scorer frozen. This island is SEEDED toward "
        f"the `{regime}` regime — bias candidates toward it; the orchestrator "
        "handles cross-island diversity and will reseed this island if it stalls."
    )


def _write_continuous(cwd: Path, objective: str) -> Path | None:
    """Enable continuous mode for this island's daemon (writes continuous.json in
    its life_dir). Returns the path written, or None on failure."""
    ld = _life_dir(cwd)
    if ld is None:
        return None
    try:
        ld.mkdir(parents=True, exist_ok=True)
        p = ld / "continuous.json"
        p.write_text(
            json.dumps(
                {"enabled": True, "objective": objective, "done_reason": "", "done_at": ""},
                indent=2,
            ),
            encoding="utf-8",
        )
        return p
    except Exception:  # noqa: BLE001
        return None


def _render_launch_script(spec: IslandSpec) -> Path:
    """Write a per-island launcher (env + cd + exec daemon), like run-daemon.sh."""
    lines = ["#!/usr/bin/env bash", "set -u"]
    for k, v in ISLAND_ENV.items():
        lines.append(f"export {k}={v}")
    lines.append(f"export ARGUS_ISLAND_REGIME={spec.regime_axis}")
    lines.append(f"export ARGUS_ISLAND_ID={spec.island_id}")
    lines.append(f'cd {spec.cwd} || exit 1')
    lines.append(f"exec {ARGUS_BIN} --daemon-fg")
    p = spec.cwd / "run-island.sh"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    p.chmod(0o755)
    return p


@dataclass
class IslandOrchestrator:
    mission_root: Path
    islands_root: Path
    n_islands: int = 3
    axes: tuple[str, ...] = DEFAULT_AXES
    gpus: tuple[int, ...] = (7, 6, 5, 4, 3)  # high indices: avoid the main daemon's greedy low picks
    period_s: int = 1200
    min_frozen_reset: int = 12
    specs: list[IslandSpec] = field(default_factory=list)

    def _task_id(self, island_id: str) -> str:
        return f"island_{island_id}"

    def setup(self) -> list[IslandSpec]:
        self.islands_root.mkdir(parents=True, exist_ok=True)
        self.specs = []
        for i in range(self.n_islands):
            spec = setup_island(
                mission_root=self.mission_root,
                islands_root=self.islands_root,
                island_id=str(i),
                regime_axis=self.axes[i % len(self.axes)],
                gpu=self.gpus[i % len(self.gpus)],
            )
            problems = verify_parity(spec, self.mission_root)
            if problems:
                raise RuntimeError(f"island {i} env-parity violation: {problems}")
            self.specs.append(spec)
        return self.specs

    def spawn(self, spec: IslandSpec) -> None:
        _write_continuous(spec.cwd, _objective(spec.regime_axis))
        script = _render_launch_script(spec)
        cmd = [
            PYBIN,
            "-m",
            "argus_skill.tools.subagent",
            "submit",
            "--task-id",
            self._task_id(spec.island_id),
            "--mode",
            "supervised",
            "--monitor-interval",
            "300",
            "--description",
            f"Island {spec.island_id} ({spec.regime_axis} regime, gpu {spec.gpu})",
            "--command",
            f"bash {script}",
        ]
        subprocess.run(cmd, cwd=str(self.islands_root), check=False)

    def kill(self, spec: IslandSpec) -> None:
        subprocess.run(
            [PYBIN, "-m", "argus_skill.tools.subagent", "stop", "--task-id", self._task_id(spec.island_id)],
            cwd=str(self.islands_root),
            check=False,
        )

    def _log(self, event: dict) -> None:
        event["ts"] = time.time()
        try:
            with (self.islands_root / "ORCHESTRATOR.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def tick(self) -> dict:
        """One migration+reset pass over the population. Returns a summary."""
        statuses = [read_status(s) for s in self.specs]
        gb = global_best(statuses)
        migrated = migrate_best(gb, statuses) if gb else 0
        reset_info = None
        if gb is not None:
            target = reset_target(
                statuses, min_frozen=self.min_frozen_reset, protect_id=gb.spec.island_id
            )
            if target is not None:
                new_axis = starved_axis(statuses, self.axes)
                self.kill(target.spec)
                new_spec = setup_island(
                    mission_root=self.mission_root,
                    islands_root=self.islands_root,
                    island_id=target.spec.island_id,
                    regime_axis=new_axis,
                    gpu=target.spec.gpu,
                    seed_train=gb.best_candidate,  # reseed from the global best
                )
                # update the tracked spec + relaunch
                self.specs = [
                    new_spec if s.island_id == new_spec.island_id else s for s in self.specs
                ]
                self.spawn(new_spec)
                reset_info = {
                    "island": target.spec.island_id,
                    "was_axis": target.spec.regime_axis,
                    "new_axis": new_axis,
                    "was_frozen": target.since_improve,
                    "reseeded_from": gb.spec.island_id,
                }
        summary = {
            "event": "tick",
            "floors": {s.spec.island_id: s.floor for s in statuses},
            "frozen": {s.spec.island_id: s.since_improve for s in statuses},
            "global_best": (gb.spec.island_id, gb.floor) if gb else None,
            "migrated": migrated,
            "reset": reset_info,
        }
        self._log(summary)
        return summary

    def run(self, max_cycles: int | None = None) -> None:
        self.setup()
        for spec in self.specs:
            self.spawn(spec)
        self._log({"event": "spawned", "islands": [s.to_dict() for s in self.specs]})
        cycle = 0
        while max_cycles is None or cycle < max_cycles:
            time.sleep(self.period_s)
            self.tick()
            cycle += 1


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="argus_skill.islands")
    ap.add_argument("--mission", default="/home/argustest/nanochat-mission-b200")
    ap.add_argument("--islands", default="/home/argustest/nanochat-islands")
    ap.add_argument("-n", "--n-islands", type=int, default=3)
    ap.add_argument("--period", type=int, default=1200)
    ap.add_argument("--dry-run", action="store_true", help="setup + verify parity; do NOT spawn")
    args = ap.parse_args(argv)

    orch = IslandOrchestrator(
        mission_root=Path(args.mission),
        islands_root=Path(args.islands),
        n_islands=args.n_islands,
        period_s=args.period,
    )
    specs = orch.setup()
    print(f"Set up {len(specs)} islands under {orch.islands_root}:")
    for s in specs:
        problems = verify_parity(s, orch.mission_root)
        print(
            f"  island {s.island_id}: regime={s.regime_axis} gpu={s.gpu} "
            f"scratch=/scratch/{s.scratch_ns} seed={s.seed_attempt or '(none)'} "
            f"parity={'OK' if not problems else problems}"
        )
    if args.dry_run:
        print("--dry-run: not spawning daemons.")
        return 0
    print("Spawning island daemons as supervised subagents + entering migrate/reset loop…")
    for s in specs:
        orch.spawn(s)
    orch._log({"event": "spawned", "islands": [s.to_dict() for s in specs]})
    while True:
        time.sleep(orch.period_s)
        summary = orch.tick()
        print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
