"""Heterogeneous resource probes with explicit failure states.

Adapters own every vendor-specific command and field; callers see only the
normalized device facts produced here. Device counts are never guessed and
failed telemetry never reads as capacity.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
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


class ResourceProbe:
    """Collect normalized accelerator facts."""

    def __init__(
        self,
        adapters: Sequence[Any] | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.adapters = tuple(adapters or (NvidiaAdapter(), RocmAdapter()))
        self._clock = clock

    def snapshot(self) -> dict[str, Any]:
        accelerators: list[dict[str, Any]] = []
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
            accelerators.append(result)
        unsafe = any(
            item.get("status") in {"inaccessible", "degraded"}
            for item in accelerators
        )
        return {
            "captured_at": self._clock(),
            "enforcement": "advisory" if unsafe else "strict",
            "accelerators": accelerators,
        }


__all__ = [
    "NvidiaAdapter",
    "ResourceProbe",
    "RocmAdapter",
]
