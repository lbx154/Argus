"""Island workspace scaffolding for multi-island evolutionary search.

An "island" is an independent search lineage running the UNMODIFIED argus engine
in its OWN cwd. Because every per-lineage artifact (``attempts/``, ``research/``,
the altitude floor, the checkpoint, the daemon lock/backlog) is keyed on the
project root / cwd fingerprint, giving each island a distinct cwd isolates all of
it for free — no engine changes needed.

``setup_island`` builds that cwd as a faithful copy of the mission:

  * ENV-PARITY immutables are SYMLINKED so they stay byte-identical to the
    reference (``lib.py``, ``prepare.py``, the data/``code`` tree,
    ``reference/``, ``baseline/``). [[argus-env-parity-principle]] is a HARD
    rule — a drifted lib/data/scorer makes the val_bpb incomparable.
  * MUTABLE per-lineage files are COPIED (``train.py`` seed, ``AGENTS.md``,
    ``TASK.md`` …) so the island can diverge.
  * ``attempts/`` is seeded with ONE entry = the global-best parent (so the
    island starts from a known floor, not a cold re-baseline).
  * ``eval_solution.sh`` is RE-TEMPLATED so its remote ``/scratch`` paths are
    namespaced per island and the GPU is pinned — otherwise two parallel evals
    on the shared pod wipe each other's logs (the frozen ``/scratch/run_with_shim.py``
    shim stays shared, so eval semantics are unchanged).

Nothing here touches the live mission dir; islands live under a separate root.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

#: Files/dirs that DEFINE the frozen eval environment — symlinked byte-identical.
#: Drift here breaks comparability (env-parity), so we never copy/mutate them.
ENV_PARITY_LINKS = ("lib.py", "prepare.py", "code", "reference", "baseline")
#: Mutable scaffolding copied into the island (it may diverge from the mission).
COPY_FILES = (
    "AGENTS.md",
    "TASK.md",
    "MISSION.md",
    "B200_SETUP.md",
    "program.md",
)
#: The five regime axes islands are seeded toward (mirrors the meta-layer's
#: REGIME_AXES / the nanochat _CATEGORY_AXES taxonomy).
DEFAULT_AXES = (
    "optimizer",
    "architecture",
    "data",
    "numerics",
    "update_mechanics",
)


@dataclass
class IslandSpec:
    """Static identity + resources for one island."""

    island_id: str
    regime_axis: str
    gpu: int
    scratch_ns: str  # remote /scratch subdir, e.g. "island_0"
    cwd: Path
    seed_attempt: str = ""  # name of the parent seeded into attempts/
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "island_id": self.island_id,
            "regime_axis": self.regime_axis,
            "gpu": self.gpu,
            "scratch_ns": self.scratch_ns,
            "cwd": str(self.cwd),
            "seed_attempt": self.seed_attempt,
            **self.extra,
        }


def _template_eval_script(text: str, scratch_ns: str, gpu: int) -> str:
    """Rewrite the frozen eval script for one island: namespace ``/scratch`` and
    pin the GPU. The shared frozen shim ``/scratch/run_with_shim.py`` is left
    untouched (env-parity); only the per-candidate working dir + logs move.
    """
    ns = f"/scratch/{scratch_ns}"
    out = text
    # cwd + shipped candidate dir (the only "/scratch/autoresearch" occurrences).
    out = out.replace("/scratch/autoresearch", ns)
    # per-seed logs/sentinels.
    out = out.replace("/scratch/cand_s", f"{ns}/cand_s")
    # the node-side launcher file.
    out = out.replace("/scratch/run_seeds.sh", f"{ns}/run_seeds.sh")
    # ensure the namespaced dir exists before the candidate is shipped into it.
    out = out.replace(
        f"echo '$B' | base64 -d > {ns}/train_candidate.py",
        f"mkdir -p {ns} && echo '$B' | base64 -d > {ns}/train_candidate.py",
    )
    # pin the GPU (was: first idle GPU); keep the idle scan harmless above it.
    out = re.sub(r"g=\$\{GPUS\[0\]\}", f"g={int(gpu)}", out)
    return out


def _seed_attempt(mission_root: Path, island_cwd: Path, seed_train: Path) -> str:
    """Seed ``attempts/`` with the global-best parent so the island starts from a
    known floor. Copies the seed train.py + a minimal summary.json carrying the
    parent's recorded score (read from the mission's best attempt if available).
    Returns the seeded attempt name, or '' if no score could be carried.
    """
    # Find the mission's promoted floor score to stamp on the seed.
    score = None
    best_name = ""
    try:
        from ..verticals._base import load_vertical, vertical_search_altitude_facts

        facts = vertical_search_altitude_facts(load_vertical("nanochat"), mission_root)
        if isinstance(facts, dict) and facts.get("floor") is not None:
            score = float(facts["floor"])
            best_name = str(facts.get("floor_name") or "")
    except Exception:  # noqa: BLE001 — seeding score is best-effort
        pass
    name = "a000_seed_parent"
    adir = island_cwd / "attempts" / name
    adir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed_train, adir / "train.candidate.py")
    summary = {
        "candidate": name,
        "decision": "promote",  # this is the island's starting floor
        "score_valid": score is not None,
        "seeded_from": best_name,
        "note": "global-best parent seeded into this island (not re-scored)",
    }
    if score is not None:
        summary["mean_val_bpb"] = score
    (adir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return name if score is not None else ""


def setup_island(
    *,
    mission_root: Path | str,
    islands_root: Path | str,
    island_id: str,
    regime_axis: str,
    gpu: int,
    seed_train: Path | str | None = None,
) -> IslandSpec:
    """Create ``islands_root/island_id`` as an isolated mission copy. Idempotent:
    an existing island cwd is wiped and rebuilt (used by reset). Returns its spec.
    """
    mission_root = Path(mission_root).resolve()
    islands_root = Path(islands_root).resolve()
    cwd = islands_root / island_id
    if cwd.exists():
        shutil.rmtree(cwd)
    cwd.mkdir(parents=True)

    # 1. ENV-PARITY immutables — symlink byte-identical (skip missing ones).
    for name in ENV_PARITY_LINKS:
        src = mission_root / name
        if src.exists():
            (cwd / name).symlink_to(src.resolve())

    # 2. MUTABLE scaffolding — copy.
    for name in COPY_FILES:
        src = mission_root / name
        if src.exists():
            shutil.copy2(src, cwd / name)

    # 3. train.py seed = current global best (mission root train.py) unless given.
    seed = Path(seed_train).resolve() if seed_train else (mission_root / "train.py")
    shutil.copy2(seed, cwd / "train.py")

    # 4. Fresh research/ carrying ONLY the stage pointer (no GROUND_TRUTH / ledger).
    (cwd / "research").mkdir()
    ps = mission_root / "research" / "PIPELINE_STATE.json"
    if ps.exists():
        shutil.copy2(ps, cwd / "research" / "PIPELINE_STATE.json")

    # 5. Seed attempts/ with the global-best parent (known starting floor).
    seed_name = _seed_attempt(mission_root, cwd, cwd / "train.py")

    # 6. Templated, namespaced eval script + GPU pin.
    scratch_ns = f"island_{island_id}" if not island_id.startswith("island") else island_id
    eval_src = mission_root / "eval_solution.sh"
    if eval_src.exists():
        templated = _template_eval_script(
            eval_src.read_text(encoding="utf-8"), scratch_ns, gpu
        )
        dst = cwd / "eval_solution.sh"
        dst.write_text(templated, encoding="utf-8")
        dst.chmod(0o755)

    spec = IslandSpec(
        island_id=island_id,
        regime_axis=regime_axis,
        gpu=gpu,
        scratch_ns=scratch_ns,
        cwd=cwd,
        seed_attempt=seed_name,
    )
    (cwd / ".island.json").write_text(
        json.dumps(spec.to_dict(), indent=2), encoding="utf-8"
    )
    return spec


def verify_parity(spec: IslandSpec, mission_root: Path | str) -> list[str]:
    """Return a list of env-parity violations (empty = OK). A violation is an
    env-parity file that is NOT a symlink resolving to the mission's file with
    identical bytes — the HARD rule that keeps val_bpb comparable.
    """
    mission_root = Path(mission_root).resolve()
    problems: list[str] = []
    for name in ENV_PARITY_LINKS:
        src = mission_root / name
        if not src.exists():
            continue
        dst = spec.cwd / name
        if not dst.is_symlink():
            problems.append(f"{name}: not a symlink (parity risk)")
            continue
        if dst.resolve() != src.resolve():
            problems.append(f"{name}: symlink resolves to {dst.resolve()}, not {src.resolve()}")
    return problems
