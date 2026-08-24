"""Read-only GPU inventory using nvidia-smi."""

from __future__ import annotations

import csv
import subprocess
from dataclasses import dataclass
from io import StringIO
from typing import Callable, Sequence


@dataclass(frozen=True)
class GpuDevice:
    index: int
    uuid: str
    name: str
    memory_total_mib: int
    memory_used_mib: int
    utilization_percent: int
    temperature_c: int | None


@dataclass(frozen=True)
class GpuProbeResult:
    available: bool
    devices: tuple[GpuDevice, ...]
    error: str | None = None


Runner = Callable[..., subprocess.CompletedProcess[str]]


class NvidiaSmiProbe:
    FIELDS = (
        "index", "uuid", "name", "memory.total", "memory.used",
        "utilization.gpu", "temperature.gpu",
    )

    def __init__(self, runner: Runner = subprocess.run, timeout: float = 5.0) -> None:
        self.runner = runner
        self.timeout = timeout

    def probe(self) -> GpuProbeResult:
        argv: Sequence[str] = (
            "nvidia-smi",
            f"--query-gpu={','.join(self.FIELDS)}",
            "--format=csv,noheader,nounits",
        )
        try:
            completed = self.runner(
                argv, capture_output=True, text=True, check=False,
                timeout=self.timeout, shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return GpuProbeResult(False, (), f"nvidia-smi unavailable: {exc}")
        if completed.returncode != 0:
            return GpuProbeResult(False, (), (completed.stderr or "nvidia-smi failed").strip())
        try:
            rows = list(csv.reader(StringIO(completed.stdout), skipinitialspace=True))
            devices = tuple(self._device(row) for row in rows if row)
        except (ValueError, IndexError) as exc:
            return GpuProbeResult(False, (), f"invalid nvidia-smi output: {exc}")
        return GpuProbeResult(True, devices)

    @staticmethod
    def _device(row: list[str]) -> GpuDevice:
        if len(row) != 7:
            raise ValueError(f"expected 7 columns, got {len(row)}")
        temp = None if row[6].strip().upper() in {"N/A", "[N/A]"} else int(row[6])
        return GpuDevice(
            index=int(row[0]), uuid=row[1].strip(), name=row[2].strip(),
            memory_total_mib=int(row[3]), memory_used_mib=int(row[4]),
            utilization_percent=int(row[5]), temperature_c=temp,
        )
