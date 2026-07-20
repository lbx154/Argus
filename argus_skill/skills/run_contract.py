"""Frozen RUN CONTRACT + curriculum feasibility packet (anti-drift / anti-collapse).

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
* :class:`FeasibilityPacket` — per-full-run RL evidence that the EXACT curriculum
  the run will consume was probed and is non-degenerate.
* :class:`SupervisedFeasibilityPacket` — equivalent evidence for supervised
  training, backed by hashed trainer loss/gradient and parameter-update
  artifacts rather than inapplicable reward/advantage fields.

A ``scale=full`` training launch must cite a matching contract hash + a valid
packet; the :mod:`argus_skill.tools.subagent` pre-launch interlock refuses
applicable launches otherwise (see :func:`check_full_run_launch`).

CLI::

    python -m argus_skill.skills.run_contract freeze --project-root . \\
        --model Qwen/Qwen3-14B-Instruct --lr 5e-6 --group-size 8 \\
        --total-steps 1200 --batch-size 1 --curriculum experiments/<slice>.json \\
        --seed 42 --scale full
    python -m argus_skill.skills.run_contract build-packet --project-root . \\
        --run-dir experiments/runs/<probe> --curriculum experiments/<slice>.json \\
        --total-steps 1200 --batch-size 1 --group-size 8 --out <packet.json>
    python -m argus_skill.skills.run_contract build-supervised-packet \\
        --project-root . --contract research/RUN_CONTRACT.json \\
        --run-dir experiments/runs/<sft-probe> --out <packet.json>
    python -m argus_skill.skills.run_contract check-launch --project-root . \\
        --contract research/RUN_CONTRACT.json --packet paper_or_run/<packet>.json \\
        --lr 5e-6 --group-size 8 --total-steps 1200 --batch-size 1 \\
        --model <id> --curriculum-hash <h>
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypeGuard

CONTRACT_SCHEMA_VERSION = 1
PACKET_SCHEMA_VERSION = 1
SUPERVISED_PACKET_SCHEMA_VERSION = 2

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
_GRAD_NORM_EPS = 1e-12


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


def compute_extension_hash(contract: dict) -> str:
    """SHA-256 over an extended run contract (excludes ``extension_hash``)."""
    payload = dict(contract)
    payload.pop("extension_hash", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


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
    extension_hash = raw.get("extension_hash")
    if extension_hash:
        recomputed_extension = compute_extension_hash(raw)
        if str(extension_hash) != recomputed_extension:
            issues.append(ContractIssue(
                "contract_extension_hash_mismatch",
                f"extension_hash={str(extension_hash)[:12]}… does not match the "
                f"extended contract fields (recomputed {recomputed_extension[:12]}…) "
                "— the contract was edited after freezing; re-freeze it",
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


@dataclass
class SupervisedFeasibilityPacket:
    """Artifact-backed non-degeneracy evidence for supervised training."""

    curriculum_hash: str
    contract_hash: str
    distinct_tasks: int
    unique_example_count: int
    execution_example_count: int
    repeat_policy: str
    maximum_occurrences_per_unique_example: int
    materialized_rows_path: str
    materialized_rows_sha256: str
    total_steps: int
    batch_size: int
    group_size: int
    loss_mean: float
    loss_min: float
    loss_max: float
    grad_norm_mean: float
    grad_norm_max: float
    finite_update: bool
    probe_steps: int
    probe_trace_path: str
    probe_trace_sha256: str
    update_artifact_path: str
    update_artifact_sha256: str
    probe_manifest_path: str
    probe_manifest_sha256: str
    probe_run_dir: str = ""
    smoke_only: bool = False
    notes: str = ""
    probe_type: str = "supervised_sft"
    schema_version: int = SUPERVISED_PACKET_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def prompt_volume(self) -> int:
        return max(0, int(self.total_steps)) * max(0, int(self.batch_size))

    @property
    def max_repetition(self) -> float:
        return float(self.maximum_occurrences_per_unique_example)


FeasibilityPacketType = FeasibilityPacket | SupervisedFeasibilityPacket


def _packet_bool(value: object) -> bool:
    """Strict boolean read for a feasibility packet.

    ``smoke_only`` waives the diversity/non-saturation anti-fraud checks, so it
    fails closed: only a genuine ``true`` (bool, ``1``, or the string
    ``"true"``) waives. ``bool("false")`` would otherwise be truthy and silently
    exempt a full run from the gate.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def load_feasibility_packet(
    path: Path,
) -> tuple[FeasibilityPacketType | None, list[ContractIssue]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [ContractIssue("packet_missing", f"{path} not found")]
    except (OSError, json.JSONDecodeError) as exc:
        return None, [ContractIssue("packet_unreadable", f"{path}: {exc}")]
    if not isinstance(raw, dict):
        return None, [ContractIssue("packet_malformed", f"{path}: not a JSON object")]
    probe_type = str(raw.get("probe_type", "rl_reward"))
    if probe_type == "supervised_sft":
        required = (
            "curriculum_hash", "contract_hash", "distinct_tasks",
            "unique_example_count", "execution_example_count", "repeat_policy",
            "maximum_occurrences_per_unique_example", "materialized_rows_path",
            "materialized_rows_sha256", "total_steps", "batch_size",
            "group_size", "loss_mean", "loss_min", "loss_max", "grad_norm_mean",
            "grad_norm_max", "finite_update", "probe_steps", "probe_trace_path",
            "probe_trace_sha256", "update_artifact_path", "update_artifact_sha256",
            "probe_manifest_path", "probe_manifest_sha256",
        )
    elif probe_type in {"rl_reward", ""}:
        required = (
            "curriculum_hash", "distinct_tasks", "total_steps", "batch_size",
            "group_size", "reward_mean", "advantage_span_max",
            "per_group_reward_std_mean", "probe_steps",
        )
    else:
        return None, [ContractIssue(
            "packet_probe_type_unsupported",
            f"unsupported probe_type={probe_type!r}; expected 'rl_reward' or "
            "'supervised_sft'",
        )]
    missing = [k for k in required if k not in raw]
    if missing:
        return None, [ContractIssue(
            "packet_incomplete", f"missing fields: {', '.join(missing)}")]
    try:
        if probe_type == "supervised_sft":
            packet = SupervisedFeasibilityPacket(
                curriculum_hash=str(raw["curriculum_hash"]),
                contract_hash=str(raw["contract_hash"]),
                distinct_tasks=int(raw["distinct_tasks"]),
                unique_example_count=int(raw["unique_example_count"]),
                execution_example_count=int(raw["execution_example_count"]),
                repeat_policy=str(raw["repeat_policy"]),
                maximum_occurrences_per_unique_example=int(
                    raw["maximum_occurrences_per_unique_example"]
                ),
                materialized_rows_path=str(raw["materialized_rows_path"]),
                materialized_rows_sha256=str(raw["materialized_rows_sha256"]),
                total_steps=int(raw["total_steps"]),
                batch_size=int(raw["batch_size"]),
                group_size=int(raw["group_size"]),
                loss_mean=float(raw["loss_mean"]),
                loss_min=float(raw["loss_min"]),
                loss_max=float(raw["loss_max"]),
                grad_norm_mean=float(raw["grad_norm_mean"]),
                grad_norm_max=float(raw["grad_norm_max"]),
                finite_update=_packet_bool(raw["finite_update"]),
                probe_steps=int(raw["probe_steps"]),
                probe_trace_path=str(raw["probe_trace_path"]),
                probe_trace_sha256=str(raw["probe_trace_sha256"]),
                update_artifact_path=str(raw["update_artifact_path"]),
                update_artifact_sha256=str(raw["update_artifact_sha256"]),
                probe_manifest_path=str(raw["probe_manifest_path"]),
                probe_manifest_sha256=str(raw["probe_manifest_sha256"]),
                probe_run_dir=str(raw.get("probe_run_dir", "")),
                smoke_only=_packet_bool(raw.get("smoke_only", False)),
                notes=str(raw.get("notes", "")),
                schema_version=int(
                    raw.get("schema_version", SUPERVISED_PACKET_SCHEMA_VERSION)
                ),
            )
        else:
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
                smoke_only=_packet_bool(raw.get("smoke_only", False)),
                notes=str(raw.get("notes", "")),
                schema_version=int(raw.get("schema_version", PACKET_SCHEMA_VERSION)),
            )
    except (TypeError, ValueError) as exc:
        return None, [ContractIssue("packet_malformed", f"{path}: {exc}")]
    return packet, []


def validate_feasibility_packet(
    packet: FeasibilityPacketType, contract: RunContract
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
    for name, packet_value, contract_value in (
        ("total_steps", packet.total_steps, contract.total_steps),
        ("batch_size", packet.batch_size, contract.batch_size),
        ("group_size", packet.group_size, contract.group_size),
    ):
        if packet_value != contract_value:
            issues.append(ContractIssue(
                f"packet_{name}_mismatch",
                f"packet {name}={packet_value} != contract {name}={contract_value}",
            ))

    if isinstance(packet, SupervisedFeasibilityPacket):
        issues.extend(_validate_supervised_packet(packet, contract))
        if packet.smoke_only:
            return issues
        if packet.max_repetition > MAX_PROMPT_REPETITION:
            issues.append(_supervised_low_diversity_issue(packet))
        return issues

    # A run the agent explicitly labels smoke/memorisation-only is allowed to
    # skip the diversity + non-saturation bounds — but the reviewer checklist
    # ensures it can NOT then be cited as general-learning evidence.
    if packet.smoke_only:
        return issues

    # (2) Static diversity bound: distinct tasks vs planned rollout volume.
    if packet.max_repetition > MAX_PROMPT_REPETITION:
        issues.append(_low_diversity_issue(packet))

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


def _low_diversity_issue(packet: FeasibilityPacketType) -> ContractIssue:
    return ContractIssue(
        "curriculum_low_diversity",
        f"each distinct task is seen ~{packet.max_repetition:.1f}x "
        f"(prompt_volume={packet.prompt_volume} / distinct_tasks="
        f"{packet.distinct_tasks}) > {MAX_PROMPT_REPETITION:.0f}x — a "
        "memorisation regime; expand distinct tasks or shorten the run, or "
        "label the run smoke_only",
    )


def _supervised_low_diversity_issue(
    packet: SupervisedFeasibilityPacket,
) -> ContractIssue:
    return ContractIssue(
        "curriculum_low_diversity",
        f"the materialized SFT curriculum repeats a unique example up to "
        f"{packet.maximum_occurrences_per_unique_example}x > "
        f"{MAX_PROMPT_REPETITION:.0f}x — expand unique examples or shorten the "
        "run, or label the run smoke_only",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is not a JSON object")
    return raw


def _read_supervised_trace(path: Path) -> tuple[list[tuple[float, float]], int]:
    records: list[tuple[float, float]] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = ast.literal_eval(line)
        except (SyntaxError, ValueError):
            continue
        if not isinstance(row, dict) or "loss" not in row or "grad_norm" not in row:
            continue
        try:
            loss = float(row["loss"])
            grad_norm = float(row["grad_norm"])
        except (TypeError, ValueError):
            malformed += 1
            continue
        if not math.isfinite(loss) or not math.isfinite(grad_norm):
            malformed += 1
            continue
        records.append((loss, grad_norm))
    return records, malformed


def _measure_supervised_curriculum_rows(path: Path) -> dict[str, int]:
    execution_count = 0
    unique_ids: set[str] = set()
    occurrence_counts: dict[str, int] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            unique_id = row.get("unique_example_id")
            if not isinstance(unique_id, str) or not unique_id:
                raise ValueError(
                    f"{path}:{line_number} has no non-empty unique_example_id"
                )
            execution_count += 1
            unique_ids.add(unique_id)
            occurrence_counts[unique_id] = occurrence_counts.get(unique_id, 0) + 1
    if not execution_count:
        raise ValueError(f"{path} has no materialized curriculum rows")
    return {
        "execution_example_count": execution_count,
        "unique_example_count": len(unique_ids),
        "maximum_occurrences_per_unique_example": max(occurrence_counts.values()),
    }


def _validate_supervised_packet(
    packet: SupervisedFeasibilityPacket, contract: RunContract
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    if packet.contract_hash != contract.contract_hash:
        issues.append(ContractIssue(
            "packet_contract_mismatch",
            f"supervised packet contract_hash={packet.contract_hash[:12]}… != "
            f"frozen contract_hash={contract.contract_hash[:12]}…",
        ))
    if packet.distinct_tasks != contract.distinct_tasks:
        issues.append(ContractIssue(
            "packet_distinct_tasks_mismatch",
            f"supervised packet task-family count={packet.distinct_tasks} != "
            f"frozen contract distinct_tasks={contract.distinct_tasks}",
        ))
    if not packet.repeat_policy.strip():
        issues.append(ContractIssue(
            "packet_repeat_policy_missing",
            "supervised packet has no materialized curriculum repetition policy",
        ))
    if packet.prompt_volume != packet.execution_example_count:
        issues.append(ContractIssue(
            "packet_execution_volume_mismatch",
            f"planned prompt volume={packet.prompt_volume} != materialized "
            f"execution examples={packet.execution_example_count}",
        ))
    if (
        packet.unique_example_count <= 0
        or packet.execution_example_count < packet.unique_example_count
        or packet.maximum_occurrences_per_unique_example <= 0
    ):
        issues.append(ContractIssue(
            "packet_curriculum_counts_invalid",
            "supervised packet example counts must be positive and execution "
            "examples must cover every unique example",
        ))

    artifacts = (
        ("probe_trace", packet.probe_trace_path, packet.probe_trace_sha256),
        ("update_artifact", packet.update_artifact_path, packet.update_artifact_sha256),
        ("probe_manifest", packet.probe_manifest_path, packet.probe_manifest_sha256),
        (
            "materialized_rows",
            packet.materialized_rows_path,
            packet.materialized_rows_sha256,
        ),
    )
    for label, path_text, expected_hash in artifacts:
        path = Path(path_text)
        if not path.is_file():
            issues.append(ContractIssue(
                f"{label}_missing", f"supervised packet source {path} not found"))
        elif _sha256_file(path) != expected_hash:
            issues.append(ContractIssue(
                f"{label}_hash_mismatch",
                f"supervised packet source {path} changed after packet creation",
            ))
    if any(issue.code.endswith(("_missing", "_hash_mismatch")) for issue in issues):
        return issues

    trace_path = Path(packet.probe_trace_path)
    records, malformed = _read_supervised_trace(trace_path)
    losses = [loss for loss, _ in records]
    grad_norms = [grad for _, grad in records]
    if malformed:
        issues.append(ContractIssue(
            "probe_non_finite_sft_metrics",
            f"{malformed} supervised trace rows have malformed/non-finite "
            "loss or gradient norm",
        ))
    if len(records) != packet.probe_steps:
        issues.append(ContractIssue(
            "probe_step_count_mismatch",
            f"packet probe_steps={packet.probe_steps} but hashed trace has "
            f"{len(records)} finite loss/gradient steps",
        ))
    if sum(grad > _GRAD_NORM_EPS for grad in grad_norms) < MIN_PROBE_STEPS:
        issues.append(ContractIssue(
            "probe_zero_gradient",
            f"fewer than {MIN_PROBE_STEPS} supervised steps have non-zero "
            "gradient norm",
        ))
    if records:
        summaries = (
            ("loss_mean", packet.loss_mean, sum(losses) / len(losses)),
            ("loss_min", packet.loss_min, min(losses)),
            ("loss_max", packet.loss_max, max(losses)),
            ("grad_norm_mean", packet.grad_norm_mean,
             sum(grad_norms) / len(grad_norms)),
            ("grad_norm_max", packet.grad_norm_max, max(grad_norms)),
        )
        for name, recorded, observed in summaries:
            if not math.isfinite(recorded) or not math.isclose(
                recorded, observed, rel_tol=1e-9, abs_tol=1e-9
            ):
                issues.append(ContractIssue(
                    f"probe_{name}_mismatch",
                    f"packet {name}={recorded!r} != hashed trace value "
                    f"{observed!r}",
                ))

    try:
        update = _read_json_object(Path(packet.update_artifact_path))
        manifest = _read_json_object(Path(packet.probe_manifest_path))
        measured_curriculum = _measure_supervised_curriculum_rows(
            Path(packet.materialized_rows_path)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return issues + [ContractIssue("probe_artifact_unreadable", str(exc))]
    digest_changed = (
        bool(update.get("initial_state_sha256"))
        and bool(update.get("final_state_sha256"))
        and update.get("initial_state_sha256") != update.get("final_state_sha256")
    )
    if (
        not packet.finite_update
        or update.get("finite_update") is not True
        or not digest_changed
    ):
        issues.append(ContractIssue(
            "probe_no_parameter_update",
            "supervised probe lacks a true finite_update backed by distinct "
            "initial/final trainable-state digests",
        ))
    try:
        manifest_steps = int(manifest.get("steps", -1))
        manifest_batch_size = int(manifest.get("effective_batch_size", -1))
        manifest_family_count = int(
            manifest.get("independent_task_family_count", -1)
        )
        manifest_unique_count = int(manifest.get("unique_example_count", -1))
        manifest_execution_count = int(
            manifest.get("execution_example_count", -1)
        )
        manifest_max_occurrences = int(
            manifest.get("maximum_occurrences_per_unique_example", -1)
        )
    except (TypeError, ValueError) as exc:
        return issues + [
            ContractIssue(
                "probe_manifest_malformed",
                f"supervised probe manifest has non-integer execution facts: {exc}",
            )
        ]
    manifest_checks = (
        manifest.get("contract_hash") == contract.contract_hash
        and manifest.get("curriculum_hash") == contract.curriculum_hash
        and manifest_steps == contract.total_steps
        and manifest_batch_size == contract.batch_size
        and manifest.get("terminal_state") == "completed"
    )
    if not manifest_checks:
        issues.append(ContractIssue(
            "probe_manifest_mismatch",
            "hashed supervised probe manifest is not a completed execution of "
            "the frozen contract/curriculum/step/batch facts",
        ))
    manifest_curriculum_checks = (
        manifest_family_count == packet.distinct_tasks
        and manifest_unique_count == packet.unique_example_count
        and manifest_execution_count == packet.execution_example_count
        and str(manifest.get("repeat_policy", "")) == packet.repeat_policy
        and manifest_max_occurrences == packet.maximum_occurrences_per_unique_example
        and str(manifest.get("materialized_rows_sha256", ""))
        == packet.materialized_rows_sha256
    )
    if not manifest_curriculum_checks:
        issues.append(ContractIssue(
            "probe_manifest_curriculum_mismatch",
            "hashed supervised probe manifest does not match the packet's "
            "example-level curriculum identity",
        ))
    measured_curriculum_checks = (
        measured_curriculum["unique_example_count"] == packet.unique_example_count
        and measured_curriculum["execution_example_count"]
        == packet.execution_example_count
        and measured_curriculum["maximum_occurrences_per_unique_example"]
        == packet.maximum_occurrences_per_unique_example
    )
    if not measured_curriculum_checks:
        issues.append(ContractIssue(
            "materialized_curriculum_counts_mismatch",
            "packet/manifest example counts do not match independently measured "
            "materialized curriculum rows",
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
    """Provenance interlock for a ``scale=full`` training launch.

    Returns ``(reject, concern)``. ``reject`` is True when the launch is not a
    faithful, feasibility-probed execution of the frozen contract. ``concern`` is
    a single actionable line naming the first violation (so the agent knows
    exactly what to fix). All checks are deterministic provenance/consistency
    facts; scientific adequacy is left to the L2 reviewer.
    """
    contract, c_issues = load_run_contract(contract_path)
    if contract is None:
        detail = c_issues[0].detail if c_issues else ""
        msg = f"freeze {DEFAULT_RUN_CONTRACT_PATH} before any scale=full training launch"
        return True, f"{msg} ({detail})" if detail else msg
    blocking = [
        issue
        for issue in c_issues
        if issue.code
        in {
            "contract_hash_absent",
            "contract_hash_mismatch",
            "contract_extension_hash_mismatch",
        }
    ]
    if blocking:
        return True, _first_concern(blocking)

    if packet_path is None:
        return True, (
            "scale=full training launch requires a feasibility packet (--feasibility-"
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


def build_supervised_feasibility_packet_from_run(
    run_dir: Path,
    *,
    contract: RunContract,
    project_root: Path | None = None,
    smoke_only: bool = False,
    notes: str = "",
) -> SupervisedFeasibilityPacket:
    """Build a supervised packet from hashed SFT trainer/update artifacts."""
    run_dir = Path(run_dir).resolve()
    trace_path = run_dir / "stdout.log"
    update_path = run_dir / "metrics.json"
    manifest_path = run_dir / "manifest.json"
    for path in (trace_path, update_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(f"supervised probe artifact missing: {path}")
    records, malformed = _read_supervised_trace(trace_path)
    if malformed:
        raise ValueError(
            f"supervised trace has {malformed} malformed/non-finite metric rows")
    if not records:
        raise ValueError("supervised trace has no finite loss/gradient rows")
    losses = [loss for loss, _ in records]
    grad_norms = [grad for _, grad in records]
    update = _read_json_object(update_path)
    manifest = _read_json_object(manifest_path)
    required_curriculum_fields = (
        "independent_task_family_count",
        "unique_example_count",
        "execution_example_count",
        "repeat_policy",
        "maximum_occurrences_per_unique_example",
        "materialized_rows_path",
        "materialized_rows_sha256",
    )
    missing_curriculum_fields = [
        field for field in required_curriculum_fields if manifest.get(field) in (None, "")
    ]
    if missing_curriculum_fields:
        raise ValueError(
            "supervised probe manifest is missing example-level curriculum facts: "
            + ", ".join(missing_curriculum_fields)
        )
    rows_path = Path(str(manifest["materialized_rows_path"]))
    if not rows_path.is_absolute():
        rows_path = (project_root or Path.cwd()) / rows_path
    rows_path = rows_path.resolve()
    if not rows_path.is_file():
        raise FileNotFoundError(
            f"supervised probe materialized curriculum missing: {rows_path}"
        )
    rows_sha256 = _sha256_file(rows_path)
    if rows_sha256 != str(manifest["materialized_rows_sha256"]):
        raise ValueError(
            "supervised probe materialized curriculum hash does not match manifest"
        )
    measured_curriculum = _measure_supervised_curriculum_rows(rows_path)
    declared_curriculum = {
        "unique_example_count": int(manifest["unique_example_count"]),
        "execution_example_count": int(manifest["execution_example_count"]),
        "maximum_occurrences_per_unique_example": int(
            manifest["maximum_occurrences_per_unique_example"]
        ),
    }
    if declared_curriculum != measured_curriculum:
        raise ValueError(
            "supervised probe manifest example counts do not match materialized rows"
        )
    finite_update = (
        update.get("finite_update") is True
        and bool(update.get("initial_state_sha256"))
        and bool(update.get("final_state_sha256"))
        and update["initial_state_sha256"] != update["final_state_sha256"]
    )
    return SupervisedFeasibilityPacket(
        curriculum_hash=contract.curriculum_hash,
        contract_hash=contract.contract_hash,
        distinct_tasks=contract.distinct_tasks,
        unique_example_count=measured_curriculum["unique_example_count"],
        execution_example_count=measured_curriculum["execution_example_count"],
        repeat_policy=str(manifest["repeat_policy"]),
        maximum_occurrences_per_unique_example=measured_curriculum[
            "maximum_occurrences_per_unique_example"
        ],
        materialized_rows_path=str(rows_path),
        materialized_rows_sha256=rows_sha256,
        total_steps=contract.total_steps,
        batch_size=contract.batch_size,
        group_size=contract.group_size,
        loss_mean=sum(losses) / len(losses),
        loss_min=min(losses),
        loss_max=max(losses),
        grad_norm_mean=sum(grad_norms) / len(grad_norms),
        grad_norm_max=max(grad_norms),
        finite_update=finite_update,
        probe_steps=len(records),
        probe_trace_path=str(trace_path),
        probe_trace_sha256=_sha256_file(trace_path),
        update_artifact_path=str(update_path),
        update_artifact_sha256=_sha256_file(update_path),
        probe_manifest_path=str(manifest_path),
        probe_manifest_sha256=_sha256_file(manifest_path),
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
    if getattr(args, "extended", False):
        return _cmd_freeze_extended(args)
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


def _load_project_freezer(module_path: Path):
    spec = importlib.util.spec_from_file_location(
        "_argus_project_freeze_training_launch_slice", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import extended freeze helper from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cmd_freeze_extended(args: argparse.Namespace) -> int:
    """Write a project-owned extended RUN_CONTRACT via the official freezer."""
    root = Path(args.project_root).resolve()
    helper_path = root / "code" / "freeze_training_launch_slice.py"
    module = _load_project_freezer(helper_path)

    curriculum_path = (root / args.curriculum).resolve()
    slice_path = (root / args.launch_slice).resolve()
    argus_out = (root / args.argus_out).resolve()
    out = (root / (args.out or DEFAULT_RUN_CONTRACT_PATH)).resolve()

    scalar_args = argparse.Namespace(
        project_root=root,
        model=args.model,
        lr=args.lr,
        group_size=args.group_size,
        total_steps=args.total_steps,
        batch_size=args.batch_size,
        curriculum=curriculum_path,
        curriculum_slice_id=args.curriculum_slice_id,
        seed=args.seed,
        repeat_policy=args.repeat_policy,
        scale=args.scale,
        out=str(argus_out.relative_to(root)),
    )
    rc = _cmd_freeze(scalar_args)
    if rc != 0:
        return rc

    curriculum = json.loads(curriculum_path.read_text(encoding="utf-8"))
    curriculum_file_sha = module.sha256_file(curriculum_path)
    launch_slice = module.build_slice(curriculum, curriculum_file_sha)
    slice_text = json.dumps(launch_slice, indent=2, sort_keys=True) + "\n"
    slice_path.write_text(slice_text, encoding="utf-8")
    launch_slice_file_sha = module.sha256_bytes(slice_text.encode("utf-8"))
    contract = module.build_contract(
        curriculum,
        curriculum_file_sha,
        launch_slice,
        launch_slice_file_sha,
    )
    recomputed_extension = compute_extension_hash(contract)
    if contract.get("extension_hash") != recomputed_extension:
        raise RuntimeError("extended contract hash mismatch during freeze")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"froze {out} schema_version={contract['schema_version']} "
        f"contract_hash={contract['contract_hash'][:12]}… "
        f"extension_hash={contract['extension_hash'][:12]}… "
        f"launch_slice_content_sha256="
        f"{contract['curriculum']['launch_slice_content_sha256'][:12]}… "
        f"distinct_tasks={contract['distinct_tasks']}"
    )
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


def _cmd_build_supervised_packet(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    contract, issues = load_run_contract(root / args.contract)
    if contract is None or issues:
        print(f"REJECT: {_first_concern(issues)}", file=sys.stderr)
        return 1
    try:
        packet = build_supervised_feasibility_packet_from_run(
            root / args.run_dir,
            contract=contract,
            project_root=root,
            smoke_only=bool(args.smoke_only),
            notes=args.notes or "",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REJECT: {exc}", file=sys.stderr)
        return 1
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet.to_dict(), indent=2), encoding="utf-8")
    validation_issues = validate_feasibility_packet(packet, contract)
    if validation_issues:
        print(f"REJECT: {_first_concern(validation_issues)}", file=sys.stderr)
        return 1
    print(
        f"wrote {out} probe_type=supervised_sft "
        f"probe_steps={packet.probe_steps} loss_mean={packet.loss_mean:.6g} "
        f"grad_norm_max={packet.grad_norm_max:.6g} "
        f"finite_update={packet.finite_update}"
    )
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
    f.add_argument(
        "--extended",
        action="store_true",
        help="also freeze the project-owned extended RUN_CONTRACT schema",
    )
    f.add_argument("--launch-slice", default="research/TRAINING_LAUNCH_SLICE.json")
    f.add_argument("--argus-out", default="research/RUN_CONTRACT_ARGUS_FREEZE.json")
    f.add_argument("--out", default="")
    f.set_defaults(func=_cmd_freeze)

    fe = sub.add_parser(
        "freeze-extended",
        help="freeze a project-owned extended research/RUN_CONTRACT.json",
    )
    fe.add_argument("--model", required=True)
    fe.add_argument("--lr", required=True)
    fe.add_argument("--group-size", required=True)
    fe.add_argument("--total-steps", required=True)
    fe.add_argument("--batch-size", required=True)
    fe.add_argument("--curriculum", required=True, help="admitted slice JSON")
    fe.add_argument("--curriculum-slice-id", required=True)
    fe.add_argument("--seed", default="42")
    fe.add_argument("--repeat-policy", default="")
    fe.add_argument("--scale", default="full")
    fe.add_argument("--launch-slice", default="research/TRAINING_LAUNCH_SLICE.json")
    fe.add_argument("--argus-out", default="research/RUN_CONTRACT_ARGUS_FREEZE.json")
    fe.add_argument("--out", default=DEFAULT_RUN_CONTRACT_PATH)
    fe.set_defaults(func=_cmd_freeze_extended)

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

    s = sub.add_parser(
        "build-supervised-packet",
        help="build an artifact-backed SFT feasibility packet",
    )
    s.add_argument("--contract", default=DEFAULT_RUN_CONTRACT_PATH)
    s.add_argument("--run-dir", required=True)
    s.add_argument("--smoke-only", action="store_true")
    s.add_argument("--notes", default="")
    s.add_argument("--out", required=True)
    s.set_defaults(func=_cmd_build_supervised_packet)

    c = sub.add_parser(
        "check-launch", help="provenance interlock for a full-scale training launch")
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
