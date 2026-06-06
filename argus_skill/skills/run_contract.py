"""Frozen RUN CONTRACT + curriculum feasibility packet (anti-drift / anti-saturation).

Two recurring, expensive failure modes in long-horizon RL research pipelines —
both observed burning multi-hour full-scale runs before being caught post-hoc:

1. **plan <-> execution hyperparameter drift.** The frozen experiment plan locks
   one set of knobs (LR, ``num_generations`` / group size, total steps, the
   curriculum slice), but the actual launch command uses different ones — copied
   from a reference doc, a stale spec, or re-derived after a context roll. The
   drift is discovered only after the run is live (a run launched at ``lr=3e-5``
   while the plan locked ``2e-6..5e-6`` got retired at optimizer step 333).

2. **curriculum saturation.** A full run launches on a curriculum that is too
   small / too repeated / too easy relative to the planned rollout volume, so the
   reward pins at the ceiling, the per-group advantage collapses to ~0, and there
   is no gradient. The agent's readiness screen often validated a *different*
   slice than the full run consumed, so the saturation only surfaced mid-run.

This module turns both into **mechanically checkable provenance facts**, NOT
scientific judgments. Consistent with the harness philosophy ("the harness is
not smarter than the agent"), it does not decide whether the science is good —
only that *what launches is the thing that was frozen and feasibility-probed*.
Whether the evidence is *sufficient* stays with the L2 reviewer.

Artifacts:

* :class:`RunContract` — the frozen, hashed set of locked knobs (the single
  source of truth), emitted at plan freeze to ``research/RUN_CONTRACT.json``.
* :class:`FeasibilityPacket` — per-full-run evidence that the EXACT curriculum
  the run will consume was probed and is non-degenerate: its ``curriculum_hash``
  matches the contract, a static distinct-task-vs-rollout-volume diversity bound
  holds, and the probe's reward/advantage stats are not already saturated — OR
  the run is explicitly labelled ``smoke_only`` (a memorisation/wiring run that
  may NOT be cited as general-learning evidence).

A ``scale=full`` RL launch must cite a matching contract hash + a valid packet;
the :mod:`argus_skill.tools.subagent` pre-launch interlock refuses otherwise
(see :func:`check_full_run_launch`).

CLI::

    python -m argus_skill.skills.run_contract freeze --project-root . \\
        --model Qwen/Qwen3-14B-Instruct --lr 5e-6 --group-size 8 \\
        --total-steps 1200 --batch-size 1 --curriculum experiments/<slice>.json \\
        --seed 42 --scale full
    python -m argus_skill.skills.run_contract build-packet --project-root . \\
        --run-dir experiments/runs/<probe> --curriculum experiments/<slice>.json \\
        --total-steps 1200 --batch-size 1 --group-size 8 --out <packet.json>
    python -m argus_skill.skills.run_contract check-launch --project-root . \\
        --contract research/RUN_CONTRACT.json --packet paper_or_run/<packet>.json \\
        --lr 5e-6 --group-size 8 --total-steps 1200 --batch-size 1 \\
        --model <id> --curriculum-hash <h>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypeGuard

CONTRACT_SCHEMA_VERSION = 1
PACKET_SCHEMA_VERSION = 1

DEFAULT_RUN_CONTRACT_PATH = "research/RUN_CONTRACT.json"

# --- thresholds (provenance arithmetic, not scientific verdicts) -------------
# A distinct task seen more than this many times across the run is a
# memorisation regime, not general learning. Generous on purpose; the L2
# reviewer still judges whether the curriculum is *good*.
MAX_PROMPT_REPETITION = 8.0
# A probe must run at least this many optimizer steps to count as a real
# feasibility probe rather than a single noisy step.
MIN_PROBE_STEPS = 5
# Relative tolerance for matching a floating hyperparameter (e.g. LR) between the
# frozen contract and the launch command.
LR_REL_TOL = 1e-3
# Saturation guards on the probe stats (mirror rl_training_health advisory eps).
_ADVANTAGE_SPAN_EPS = 1e-6   # probe advantage max-min at/below this == no signal
_REWARD_CEILING = 0.99       # probe reward mean at/above this == already solved
_WITHIN_GROUP_STD_EPS = 1e-6  # per-group reward std at/below this == no contrast


@dataclass
class ContractIssue:
    """A single provenance/consistency violation. ``code`` is a stable id."""

    code: str
    detail: str


# ---------------------------------------------------------------------------
# RunContract
# ---------------------------------------------------------------------------

# Fields that participate in the contract hash, in canonical order. The hash is
# the manifest's provenance anchor: a run whose manifest cites this hash is
# attesting it used exactly these knobs + this curriculum.
_LOCKED_FIELDS: tuple[str, ...] = (
    "model_id",
    "lr",
    "group_size",
    "total_steps",
    "batch_size",
    "curriculum_slice_id",
    "curriculum_hash",
    "distinct_tasks",
    "seed",
    "scale",
)


@dataclass
class RunContract:
    model_id: str
    lr: float
    group_size: int
    total_steps: int
    batch_size: int
    curriculum_slice_id: str
    curriculum_hash: str
    distinct_tasks: int
    seed: int
    scale: str = "full"
    schema_version: int = CONTRACT_SCHEMA_VERSION
    contract_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def with_hash(self) -> "RunContract":
        self.contract_hash = compute_contract_hash(self.to_dict())
        return self


def _canon_value(key: str, value: object) -> str:
    """Stable, float-robust canonical string for one locked field."""
    if value is None:
        return ""
    if key in ("lr",):
        try:
            return format(float(str(value)), ".6g")
        except (TypeError, ValueError):
            return str(value)
    if key in ("group_size", "total_steps", "batch_size", "distinct_tasks", "seed"):
        try:
            return str(int(float(str(value))))
        except (TypeError, ValueError):
            return str(value)
    return str(value).strip()


def compute_contract_hash(contract: dict) -> str:
    """SHA-256 over the locked fields (excludes ``contract_hash`` itself)."""
    payload = "\n".join(
        f"{k}={_canon_value(k, contract.get(k))}" for k in _LOCKED_FIELDS
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_run_contract(path: Path) -> tuple[RunContract | None, list[ContractIssue]]:
    """Load + structurally validate a RunContract JSON file."""
    issues: list[ContractIssue] = []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [ContractIssue("contract_missing", f"{path} not found")]
    except (OSError, json.JSONDecodeError) as exc:
        return None, [ContractIssue("contract_unreadable", f"{path}: {exc}")]
    if not isinstance(raw, dict):
        return None, [ContractIssue("contract_malformed", f"{path}: not a JSON object")]

    missing = [k for k in _LOCKED_FIELDS if raw.get(k) in (None, "")]
    if missing:
        issues.append(ContractIssue(
            "contract_incomplete",
            f"missing/empty locked fields: {', '.join(missing)}",
        ))
        return None, issues

    try:
        contract = RunContract(
            model_id=str(raw["model_id"]),
            lr=float(raw["lr"]),
            group_size=int(raw["group_size"]),
            total_steps=int(raw["total_steps"]),
            batch_size=int(raw["batch_size"]),
            curriculum_slice_id=str(raw["curriculum_slice_id"]),
            curriculum_hash=str(raw["curriculum_hash"]),
            distinct_tasks=int(raw["distinct_tasks"]),
            seed=int(raw["seed"]),
            scale=str(raw.get("scale", "full")),
            schema_version=int(raw.get("schema_version", CONTRACT_SCHEMA_VERSION)),
            contract_hash=str(raw.get("contract_hash", "")),
        )
    except (TypeError, ValueError) as exc:
        return None, [ContractIssue("contract_malformed", f"{path}: {exc}")]

    recomputed = compute_contract_hash(contract.to_dict())
    if not contract.contract_hash:
        issues.append(ContractIssue(
            "contract_hash_absent",
            "contract_hash is empty — freeze the contract so the run manifest "
            "can cite a provenance anchor",
        ))
    elif contract.contract_hash != recomputed:
        issues.append(ContractIssue(
            "contract_hash_mismatch",
            f"contract_hash={contract.contract_hash[:12]}… does not match the "
            f"locked fields (recomputed {recomputed[:12]}…) — the contract was "
            "edited after freezing; re-freeze it",
        ))
    return contract, issues


# ---------------------------------------------------------------------------
# FeasibilityPacket
# ---------------------------------------------------------------------------


@dataclass
class FeasibilityPacket:
    curriculum_hash: str
    distinct_tasks: int
    total_steps: int
    batch_size: int
    group_size: int
    reward_mean: float
    reward_std: float
    per_group_reward_std_mean: float
    advantage_span_max: float
    frac_reward_zero_std: float
    probe_steps: int
    probe_run_dir: str = ""
    smoke_only: bool = False
    notes: str = ""
    schema_version: int = PACKET_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def prompt_volume(self) -> int:
        return max(0, int(self.total_steps)) * max(0, int(self.batch_size))

    @property
    def max_repetition(self) -> float:
        if self.distinct_tasks <= 0:
            return float("inf")
        return self.prompt_volume / float(self.distinct_tasks)


def load_feasibility_packet(
    path: Path,
) -> tuple[FeasibilityPacket | None, list[ContractIssue]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [ContractIssue("packet_missing", f"{path} not found")]
    except (OSError, json.JSONDecodeError) as exc:
        return None, [ContractIssue("packet_unreadable", f"{path}: {exc}")]
    if not isinstance(raw, dict):
        return None, [ContractIssue("packet_malformed", f"{path}: not a JSON object")]
    required = (
        "curriculum_hash", "distinct_tasks", "total_steps", "batch_size",
        "group_size", "reward_mean", "advantage_span_max",
        "per_group_reward_std_mean", "probe_steps",
    )
    missing = [k for k in required if k not in raw]
    if missing:
        return None, [ContractIssue(
            "packet_incomplete", f"missing fields: {', '.join(missing)}")]
    try:
        packet = FeasibilityPacket(
            curriculum_hash=str(raw["curriculum_hash"]),
            distinct_tasks=int(raw["distinct_tasks"]),
            total_steps=int(raw["total_steps"]),
            batch_size=int(raw["batch_size"]),
            group_size=int(raw["group_size"]),
            reward_mean=float(raw["reward_mean"]),
            reward_std=float(raw.get("reward_std", 0.0)),
            per_group_reward_std_mean=float(raw["per_group_reward_std_mean"]),
            advantage_span_max=float(raw["advantage_span_max"]),
            frac_reward_zero_std=float(raw.get("frac_reward_zero_std", 0.0)),
            probe_steps=int(raw["probe_steps"]),
            probe_run_dir=str(raw.get("probe_run_dir", "")),
            smoke_only=bool(raw.get("smoke_only", False)),
            notes=str(raw.get("notes", "")),
            schema_version=int(raw.get("schema_version", PACKET_SCHEMA_VERSION)),
        )
    except (TypeError, ValueError) as exc:
        return None, [ContractIssue("packet_malformed", f"{path}: {exc}")]
    return packet, []


def validate_feasibility_packet(
    packet: FeasibilityPacket, contract: RunContract
) -> list[ContractIssue]:
    """Provenance + non-degeneracy checks tying a packet to its contract."""
    issues: list[ContractIssue] = []

    # (1) Exact-curriculum provenance: the probe must be on the SAME curriculum
    # the full run will consume. This closes the "readiness on slice A, run on
    # slice B" gap deterministically.
    if packet.curriculum_hash != contract.curriculum_hash:
        issues.append(ContractIssue(
            "packet_curriculum_mismatch",
            f"feasibility probe curriculum_hash={packet.curriculum_hash[:12]}… "
            f"!= contract curriculum_hash={contract.curriculum_hash[:12]}… — the "
            "probe validated a DIFFERENT curriculum than the run will consume; "
            "probe the exact frozen curriculum",
        ))
    if packet.probe_steps < MIN_PROBE_STEPS:
        issues.append(ContractIssue(
            "packet_probe_too_short",
            f"probe_steps={packet.probe_steps} < {MIN_PROBE_STEPS}; run a longer "
            "feasibility probe so the reward/advantage stats are meaningful",
        ))

    # A run the agent explicitly labels smoke/memorisation-only is allowed to
    # skip the diversity + non-saturation bounds — but the reviewer checklist
    # ensures it can NOT then be cited as general-learning evidence.
    if packet.smoke_only:
        return issues

    # (2) Static diversity bound: distinct tasks vs planned rollout volume.
    if packet.max_repetition > MAX_PROMPT_REPETITION:
        issues.append(ContractIssue(
            "curriculum_low_diversity",
            f"each distinct task is seen ~{packet.max_repetition:.1f}x "
            f"(prompt_volume={packet.prompt_volume} / distinct_tasks="
            f"{packet.distinct_tasks}) > {MAX_PROMPT_REPETITION:.0f}x — a "
            "memorisation regime; expand distinct tasks or shorten the run, or "
            "label the run smoke_only",
        ))

    # (3) Probe non-saturation: the curriculum is not already solved / contrast
    # exists at the starting policy.
    if packet.advantage_span_max <= _ADVANTAGE_SPAN_EPS:
        issues.append(ContractIssue(
            "probe_zero_advantage",
            f"probe advantage span max={packet.advantage_span_max:.2e} ~ 0 — no "
            "per-group advantage signal on this curriculum at the start policy; "
            "the run would not learn",
        ))
    if packet.reward_mean >= _REWARD_CEILING:
        issues.append(ContractIssue(
            "probe_reward_ceiling",
            f"probe reward mean={packet.reward_mean:.3f} >= {_REWARD_CEILING} — "
            "the curriculum is already solved (reward-ceiling saturation); pick "
            "harder tasks",
        ))
    if (
        packet.per_group_reward_std_mean <= _WITHIN_GROUP_STD_EPS
        and packet.frac_reward_zero_std >= 1.0
    ):
        issues.append(ContractIssue(
            "probe_no_within_group_contrast",
            "every probed group had zero within-group reward variance — no "
            "GRPO contrast is possible on this curriculum",
        ))
    return issues


# ---------------------------------------------------------------------------
# Launch interlock (called by argus_skill.tools.subagent)
# ---------------------------------------------------------------------------


@dataclass
class LaunchKnobs:
    """The hyperparameters parsed from a launch command, for drift checking."""

    lr: float | None = None
    group_size: int | None = None
    total_steps: int | None = None
    batch_size: int | None = None
    model_id: str | None = None
    curriculum_hash: str | None = None


def _model_ids_match(contract_model: str, launch_model: str) -> bool:
    """Relaxed model match: launch path may be a local snapshot dir while the
    contract names the HF id. Require the contract id's last path segment to
    appear in the launch string (catches instruct-vs-base drift)."""
    cm = contract_model.strip().lower()
    lm = launch_model.strip().lower()
    if not cm or not lm:
        return False
    if cm == lm or cm in lm or lm in cm:
        return True
    tail = cm.rsplit("/", 1)[-1]
    return bool(tail) and tail in lm


def diff_launch_against_contract(
    knobs: LaunchKnobs, contract: RunContract
) -> list[ContractIssue]:
    """Field-by-field drift check between a launch and the frozen contract."""
    issues: list[ContractIssue] = []

    if knobs.curriculum_hash is None:
        issues.append(ContractIssue(
            "launch_no_curriculum_hash",
            "launch did not declare --curriculum-hash; the launcher must compute "
            "the hash of the materialised curriculum and pass it so it can be "
            "matched against the frozen contract",
        ))
    elif knobs.curriculum_hash != contract.curriculum_hash:
        issues.append(ContractIssue(
            "launch_curriculum_drift",
            f"launch curriculum_hash={knobs.curriculum_hash[:12]}… != contract "
            f"curriculum_hash={contract.curriculum_hash[:12]}… — the run would "
            "train on a different curriculum than the frozen plan",
        ))

    if knobs.lr is not None:
        denom = abs(contract.lr) or 1e-12
        if abs(knobs.lr - contract.lr) / denom > LR_REL_TOL:
            issues.append(ContractIssue(
                "launch_lr_drift",
                f"launch lr={knobs.lr:g} != contract lr={contract.lr:g}; reconcile "
                "the plan first or fix the launch",
            ))
    for name, lv, cv in (
        ("group_size", knobs.group_size, contract.group_size),
        ("total_steps", knobs.total_steps, contract.total_steps),
        ("batch_size", knobs.batch_size, contract.batch_size),
    ):
        if lv is not None and int(lv) != int(cv):
            issues.append(ContractIssue(
                f"launch_{name}_drift",
                f"launch {name}={lv} != contract {name}={cv}; reconcile the plan "
                "first or fix the launch",
            ))
    if knobs.model_id is not None and not _model_ids_match(
        contract.model_id, knobs.model_id
    ):
        issues.append(ContractIssue(
            "launch_model_drift",
            f"launch model={knobs.model_id!r} does not match contract "
            f"model={contract.model_id!r} (instruct-vs-base or wrong checkpoint?)",
        ))
    return issues


def check_full_run_launch(
    *,
    contract_path: Path,
    packet_path: Path | None,
    knobs: LaunchKnobs,
) -> tuple[bool, str]:
    """Provenance interlock for a ``scale=full`` RL launch.

    Returns ``(reject, concern)``. ``reject`` is True when the launch is not a
    faithful, feasibility-probed execution of the frozen contract. ``concern`` is
    a single actionable line naming the first violation (so the agent knows
    exactly what to fix). All checks are deterministic provenance/consistency
    facts; scientific adequacy is left to the L2 reviewer.
    """
    contract, c_issues = load_run_contract(contract_path)
    if contract is None:
        detail = c_issues[0].detail if c_issues else ""
        msg = f"freeze {DEFAULT_RUN_CONTRACT_PATH} before any scale=full RL launch"
        return True, f"{msg} ({detail})" if detail else msg
    blocking = [i for i in c_issues if i.code in (
        "contract_hash_absent", "contract_hash_mismatch")]
    if blocking:
        return True, _first_concern(blocking)

    if packet_path is None:
        return True, (
            "scale=full RL launch requires a feasibility packet (--feasibility-"
            "packet) proving the exact frozen curriculum is non-saturating; "
            "build one with `python -m argus_skill.skills.run_contract build-packet`")
    packet, p_issues = load_feasibility_packet(packet_path)
    if packet is None:
        return True, _first_concern(p_issues, fallback="feasibility packet invalid")

    issues = diff_launch_against_contract(knobs, contract)
    issues += validate_feasibility_packet(packet, contract)
    if issues:
        return True, _first_concern(issues)
    return False, ""


def _first_concern(issues: list[ContractIssue], *, fallback: str = "") -> str:
    if not issues:
        return fallback
    head = issues[0]
    return f"[{head.code}] {head.detail}"


# ---------------------------------------------------------------------------
# Curriculum hashing + packet building (agent-facing convenience)
# ---------------------------------------------------------------------------


def compute_curriculum_hash(task_ids: list[str], *, seed: int, repeat_policy: str = "") -> str:
    """Content hash of an admitted curriculum: the SORTED distinct task-id set
    plus the sampling determinants. Order-independent on the id SET but pinned on
    the seed + repeat policy, so two materialisations of "the same slice" hash
    equal while a different admitted set does not."""
    distinct = sorted({str(t) for t in task_ids})
    payload = json.dumps(
        {"task_ids": distinct, "seed": int(seed), "repeat_policy": repeat_policy},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    except OSError:
        return []
    return rows


def build_feasibility_packet_from_run(
    run_dir: Path,
    *,
    curriculum_hash: str,
    total_steps: int,
    batch_size: int,
    group_size: int,
    distinct_tasks: int,
    smoke_only: bool = False,
    notes: str = "",
) -> FeasibilityPacket:
    """Compute packet stats from a short probe run's progress/reward artifacts.

    Reuses the same progress.jsonl schema the RL health analyzer reads. Stats are
    advisory; the *gate* only checks provenance + the non-degeneracy bounds.
    """
    run_dir = Path(run_dir)
    progress = _read_jsonl(run_dir / "progress.jsonl")
    steps = [r for r in progress if r.get("event") == "optimizer_step"]

    reward_means = [float(r["reward_mean"]) for r in steps if _isnum(r.get("reward_mean"))]
    reward_stds = [float(r["reward_std"]) for r in steps if _isnum(r.get("reward_std"))]
    zero_std = [
        float(r["frac_reward_zero_std"]) for r in steps
        if _isnum(r.get("frac_reward_zero_std"))
    ]
    adv_spans: list[float] = []
    for r in steps:
        raw = r.get("raw_verl_metrics") or {}
        amax = raw.get("critic/advantages/max")
        amin = raw.get("critic/advantages/min")
        if _isnum(amax) and _isnum(amin):
            adv_spans.append(float(amax) - float(amin))

    return FeasibilityPacket(
        curriculum_hash=curriculum_hash,
        distinct_tasks=int(distinct_tasks),
        total_steps=int(total_steps),
        batch_size=int(batch_size),
        group_size=int(group_size),
        reward_mean=(sum(reward_means) / len(reward_means)) if reward_means else 0.0,
        reward_std=(reward_stds[-1] if reward_stds else 0.0),
        per_group_reward_std_mean=(sum(reward_stds) / len(reward_stds)) if reward_stds else 0.0,
        advantage_span_max=max(adv_spans) if adv_spans else 0.0,
        frac_reward_zero_std=(zero_std[-1] if zero_std else 0.0),
        probe_steps=len(steps),
        probe_run_dir=str(run_dir),
        smoke_only=smoke_only,
        notes=notes,
    )


def _isnum(v: object) -> TypeGuard[float]:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_task_ids(curriculum_path: Path) -> tuple[list[str], int]:
    """Best-effort extraction of admitted task ids from a curriculum/slice JSON.

    Accepts a list of ids, a list of row dicts (``task_id``/``id``), or a dict
    with a ``task_ids`` / ``tasks`` / ``admitted`` array.
    """
    raw = json.loads(Path(curriculum_path).read_text(encoding="utf-8"))
    rows: list = []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        for key in ("task_ids", "tasks", "admitted", "rows", "items"):
            if isinstance(raw.get(key), list):
                rows = raw[key]
                break
    ids: list[str] = []
    for r in rows:
        if isinstance(r, str):
            ids.append(r)
        elif isinstance(r, dict):
            tid = r.get("task_id") or r.get("id") or (
                (r.get("extra_info") or {}).get("task_id")
                if isinstance(r.get("extra_info"), dict) else None
            )
            if tid is not None:
                ids.append(str(tid))
    return ids, len({*ids})


def _cmd_freeze(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    task_ids, distinct = _load_task_ids(Path(args.curriculum))
    cur_hash = compute_curriculum_hash(
        task_ids, seed=args.seed, repeat_policy=args.repeat_policy)
    contract = RunContract(
        model_id=args.model,
        lr=float(args.lr),
        group_size=int(args.group_size),
        total_steps=int(args.total_steps),
        batch_size=int(args.batch_size),
        curriculum_slice_id=args.curriculum_slice_id or Path(args.curriculum).name,
        curriculum_hash=cur_hash,
        distinct_tasks=distinct,
        seed=int(args.seed),
        scale=args.scale,
    ).with_hash()
    out = root / (args.out or DEFAULT_RUN_CONTRACT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(contract.to_dict(), indent=2), encoding="utf-8")
    print(f"froze {out} contract_hash={contract.contract_hash[:12]}… "
          f"curriculum_hash={cur_hash[:12]}… distinct_tasks={distinct}")
    return 0


def _cmd_build_packet(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    task_ids, distinct = _load_task_ids(Path(args.curriculum))
    cur_hash = compute_curriculum_hash(
        task_ids, seed=args.seed, repeat_policy=args.repeat_policy)
    packet = build_feasibility_packet_from_run(
        root / args.run_dir,
        curriculum_hash=cur_hash,
        total_steps=int(args.total_steps),
        batch_size=int(args.batch_size),
        group_size=int(args.group_size),
        distinct_tasks=distinct,
        smoke_only=bool(args.smoke_only),
        notes=args.notes or "",
    )
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet.to_dict(), indent=2), encoding="utf-8")
    print(f"wrote {out} curriculum_hash={cur_hash[:12]}… "
          f"distinct_tasks={distinct} max_repetition={packet.max_repetition:.2f} "
          f"reward_mean={packet.reward_mean:.3f} "
          f"advantage_span_max={packet.advantage_span_max:.3e}")
    return 0


def _cmd_check_launch(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    knobs = LaunchKnobs(
        lr=float(args.lr) if args.lr is not None else None,
        group_size=int(args.group_size) if args.group_size is not None else None,
        total_steps=int(args.total_steps) if args.total_steps is not None else None,
        batch_size=int(args.batch_size) if args.batch_size is not None else None,
        model_id=args.model,
        curriculum_hash=args.curriculum_hash,
    )
    packet_path = root / args.packet if args.packet else None
    reject, concern = check_full_run_launch(
        contract_path=root / args.contract,
        packet_path=packet_path,
        knobs=knobs,
    )
    if reject:
        print(f"REJECT: {concern}", file=sys.stderr)
        return 1
    print("OK: launch matches the frozen contract and a valid feasibility packet")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("freeze", help="freeze research/RUN_CONTRACT.json")
    f.add_argument("--model", required=True)
    f.add_argument("--lr", required=True)
    f.add_argument("--group-size", required=True)
    f.add_argument("--total-steps", required=True)
    f.add_argument("--batch-size", required=True)
    f.add_argument("--curriculum", required=True, help="admitted slice JSON")
    f.add_argument("--curriculum-slice-id", default="")
    f.add_argument("--seed", default="42")
    f.add_argument("--repeat-policy", default="")
    f.add_argument("--scale", default="full")
    f.add_argument("--out", default="")
    f.set_defaults(func=_cmd_freeze)

    b = sub.add_parser("build-packet", help="build a feasibility packet from a probe run")
    b.add_argument("--run-dir", required=True)
    b.add_argument("--curriculum", required=True)
    b.add_argument("--total-steps", required=True)
    b.add_argument("--batch-size", required=True)
    b.add_argument("--group-size", required=True)
    b.add_argument("--seed", default="42")
    b.add_argument("--repeat-policy", default="")
    b.add_argument("--smoke-only", action="store_true")
    b.add_argument("--notes", default="")
    b.add_argument("--out", required=True)
    b.set_defaults(func=_cmd_build_packet)

    c = sub.add_parser("check-launch", help="provenance interlock for a full-scale RL launch")
    c.add_argument("--contract", default=DEFAULT_RUN_CONTRACT_PATH)
    c.add_argument("--packet", default="")
    c.add_argument("--lr", default=None)
    c.add_argument("--group-size", default=None)
    c.add_argument("--total-steps", default=None)
    c.add_argument("--batch-size", default=None)
    c.add_argument("--model", default=None)
    c.add_argument("--curriculum-hash", default=None)
    c.set_defaults(func=_cmd_check_launch)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
