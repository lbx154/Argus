"""Heterogeneous resource probes with explicit failure states.

Adapters own every vendor-specific command and field.  The ledger consumes only
the normalized device facts produced here; it never guesses device counts or
turns failed telemetry into capacity.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ...agent_cli._process_control import windows_hidden_subprocess_kwargs

RunCommand = Callable[[Sequence[str]], str]
WhichCommand = Callable[[str], str | None]


def _run(command: Sequence[str]) -> str:
    return subprocess.run(
        list(command),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        **windows_hidden_subprocess_kwargs(),
    ).stdout


def _number(value: object) -> float:
    token = str(value).strip().split()[0].replace("%", "")
    return float(token)


def _device(
    *,
    identity: str,
    index: str,
    name: str,
    total: object,
    used: object,
    utilization: object,
) -> dict[str, Any]:
    return {
        "identity": identity,
        "index": index,
        "name": name,
        "total_memory_mib": int(_number(total)),
        "used_memory_mib": int(_number(used)),
        "utilization_percent": float(_number(utilization)),
        "visibility": index,
    }


def _stable_identity(kind: str, reported: object, index: str, name: str) -> str:
    value = str(reported).strip()
    if value.lower() not in {"", "n/a", "na", "unknown", "[not supported]"}:
        return value
    return f"{kind}:{index}:{name}"


class NvidiaAdapter:
    kind = "cuda"
    visibility_env = "CUDA_VISIBLE_DEVICES"

    def __init__(
        self,
        *,
        run_command: RunCommand = _run,
        which: WhichCommand = shutil.which,
    ) -> None:
        self._run = run_command
        self._which = which

    def probe(self) -> dict[str, Any]:
        executable = self._which("nvidia-smi")
        if not executable:
            return self._result("absent", detail="nvidia-smi is not installed")
        try:
            output = self._run([
                executable,
                "--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ])
        except (OSError, subprocess.SubprocessError) as exc:
            return self._result(
                "inaccessible",
                detail=f"nvidia-smi failed: {type(exc).__name__}: {exc}",
            )
        devices: list[dict[str, Any]] = []
        bad_rows: list[str] = []
        for row in output.splitlines():
            if not row.strip():
                continue
            parts = [part.strip() for part in row.split(",")]
            if len(parts) != 6:
                bad_rows.append(row)
                continue
            try:
                index, uuid, name, total, used, utilization = parts
                devices.append(_device(
                    identity=_stable_identity("cuda", uuid, index, name),
                    index=index,
                    name=name,
                    total=total,
                    used=used,
                    utilization=utilization,
                ))
            except (TypeError, ValueError):
                bad_rows.append(row)
        if bad_rows:
            return self._result(
                "degraded",
                devices=devices,
                detail=f"could not parse {len(bad_rows)} nvidia-smi row(s)",
            )
        if not devices:
            return self._result("absent", detail="nvidia-smi reported no devices")
        return self._result("available", devices=devices)

    def _result(
        self,
        status: str,
        *,
        devices: list[dict[str, Any]] | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status": status,
            "visibility_env": self.visibility_env,
            "devices": devices or [],
            "detail": detail,
        }


class RocmAdapter:
    kind = "rocm"
    visibility_env = "ROCR_VISIBLE_DEVICES"

    def __init__(
        self,
        *,
        run_command: RunCommand = _run,
        which: WhichCommand = shutil.which,
    ) -> None:
        self._run = run_command
        self._which = which

    @staticmethod
    def _field(card: dict[str, Any], *needles: str) -> object:
        for key, value in card.items():
            folded = key.lower().replace("_", " ")
            if all(needle in folded for needle in needles):
                return value
        raise KeyError("/".join(needles))

    def probe(self) -> dict[str, Any]:
        executable = self._which("rocm-smi")
        if not executable:
            return self._result("absent", detail="rocm-smi is not installed")
        try:
            raw = self._run([
                executable,
                "--showuniqueid",
                "--showproductname",
                "--showmeminfo",
                "vram",
                "--showuse",
                "--json",
            ])
            payload = json.loads(raw)
        except (OSError, subprocess.SubprocessError) as exc:
            return self._result(
                "inaccessible",
                detail=f"rocm-smi failed: {type(exc).__name__}: {exc}",
            )
        except json.JSONDecodeError as exc:
            return self._result("degraded", detail=f"invalid rocm-smi JSON: {exc}")
        if not isinstance(payload, dict):
            return self._result("degraded", detail="rocm-smi JSON is not an object")
        devices: list[dict[str, Any]] = []
        bad_cards: list[str] = []
        for label, value in payload.items():
            if not isinstance(value, dict):
                continue
            index = "".join(ch for ch in str(label) if ch.isdigit()) or str(label)
            try:
                unique = str(self._field(value, "unique", "id"))
                name = str(self._field(value, "card", "series"))
                total_bytes = _number(self._field(value, "vram", "total"))
                used_bytes = _number(self._field(value, "vram", "used"))
                utilization = self._field(value, "gpu", "use")
                devices.append(_device(
                    identity=_stable_identity("rocm", unique, index, name),
                    index=index,
                    name=name,
                    total=total_bytes / (1024 * 1024),
                    used=used_bytes / (1024 * 1024),
                    utilization=utilization,
                ))
            except (KeyError, TypeError, ValueError):
                bad_cards.append(str(label))
        if bad_cards:
            return self._result(
                "degraded",
                devices=devices,
                detail=f"incomplete rocm-smi telemetry for {', '.join(bad_cards)}",
            )
        if not devices:
            return self._result("absent", detail="rocm-smi reported no devices")
        return self._result("available", devices=devices)

    def _result(
        self,
        status: str,
        *,
        devices: list[dict[str, Any]] | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status": status,
            "visibility_env": self.visibility_env,
            "devices": devices or [],
            "detail": detail,
        }


def _read_first(paths: Sequence[Path]) -> tuple[str | None, str]:
    for path in paths:
        try:
            return path.read_text(encoding="utf-8").strip(), str(path)
        except OSError:
            continue
    return None, ""


def _parse_cpu_set(value: str) -> set[int]:
    cpu_ids: set[int] = set()
    for part in value.split(","):
        bounds = part.strip().split("-", 1)
        if not bounds[0]:
            continue
        start = int(bounds[0])
        end = int(bounds[-1])
        if end < start:
            raise ValueError(value)
        cpu_ids.update(range(start, end + 1))
    return cpu_ids


def _cpu_memory_snapshot(cgroup_root: Path = Path("/sys/fs/cgroup")) -> dict[str, Any]:
    details: list[str] = []
    try:
        cpu_ids = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpu_ids = list(range(os.cpu_count() or 0))
        details.append("CPU affinity is unavailable")
    cpuset_value, cpuset_path = _read_first([
        cgroup_root / "cpuset.cpus.effective",
        cgroup_root / "cpuset" / "cpuset.cpus",
    ])
    if cpuset_value:
        try:
            cgroup_ids = _parse_cpu_set(cpuset_value)
            cpu_ids = sorted(set(cpu_ids) & cgroup_ids) if cpu_ids else sorted(cgroup_ids)
        except ValueError:
            details.append(f"invalid cgroup CPU set: {cpuset_value!r}")
    memory_current, current_path = _read_first([
        cgroup_root / "memory.current",
        cgroup_root / "memory" / "memory.usage_in_bytes",
    ])
    memory_limit, limit_path = _read_first([
        cgroup_root / "memory.max",
        cgroup_root / "memory" / "memory.limit_in_bytes",
    ])

    def _bytes(value: str | None) -> int | None:
        if value in (None, "", "max"):
            return None
        try:
            return int(value)
        except ValueError:
            details.append(f"invalid cgroup memory value: {value!r}")
            return None

    return {
        "status": "degraded" if details else "available",
        "visible_cpu_ids": cpu_ids,
        "visible_cpu_count": len(cpu_ids),
        "cpu_set_source": cpuset_path,
        "memory_used_bytes": _bytes(memory_current),
        "memory_limit_bytes": _bytes(memory_limit),
        "memory_current_source": current_path,
        "memory_limit_source": limit_path,
        "detail": "; ".join(details),
    }


class CpuNoneAdapter:
    """The always-present no-accelerator path plus visible CPU/memory facts."""

    kind = "none"
    visibility_env = ""

    def __init__(self, *, cgroup_root: Path = Path("/sys/fs/cgroup")) -> None:
        self._cgroup_root = cgroup_root

    def probe(self) -> dict[str, Any]:
        cpu_memory = _cpu_memory_snapshot(self._cgroup_root)
        return {
            "kind": self.kind,
            "status": cpu_memory["status"],
            "visibility_env": "",
            "devices": [],
            "detail": cpu_memory.get("detail", ""),
            "cpu_memory": cpu_memory,
        }


class ResourceProbe:
    """Collect normalized accelerator and cgroup-visible host facts."""

    def __init__(
        self,
        adapters: Sequence[Any] | None = None,
        *,
        clock: Callable[[], float] = time.time,
        cgroup_root: Path = Path("/sys/fs/cgroup"),
    ) -> None:
        self.adapters = tuple(
            adapters or (NvidiaAdapter(), RocmAdapter(), CpuNoneAdapter(cgroup_root=cgroup_root))
        )
        self._clock = clock
        self._cgroup_root = cgroup_root

    def snapshot(self) -> dict[str, Any]:
        accelerators: list[dict[str, Any]] = []
        cpu_memory = _cpu_memory_snapshot(self._cgroup_root)
        for adapter in self.adapters:
            try:
                result = adapter.probe()
            except Exception as exc:  # adapter bugs remain visible, never capacity
                result = {
                    "kind": str(getattr(adapter, "kind", "unknown")),
                    "status": "degraded",
                    "visibility_env": str(getattr(adapter, "visibility_env", "")),
                    "devices": [],
                    "detail": f"adapter failed: {type(exc).__name__}: {exc}",
                }
            if result.get("kind") == "none" and isinstance(result.get("cpu_memory"), dict):
                cpu_memory = result["cpu_memory"]
            else:
                accelerators.append(result)
        unsafe = any(
            item.get("status") in {"inaccessible", "degraded"}
            for item in accelerators
        )
        return {
            "captured_at": self._clock(),
            "enforcement": "advisory" if unsafe else "strict",
            "accelerators": accelerators,
            "cpu_memory": cpu_memory,
        }


CpuAdapter = CpuNoneAdapter

__all__ = [
    "CpuAdapter",
    "CpuNoneAdapter",
    "NvidiaAdapter",
    "ResourceProbe",
    "RocmAdapter",
]
