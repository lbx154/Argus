from __future__ import annotations

import subprocess

from argus.tools.resource_ledger.probe import NvidiaAdapter, ResourceProbe


def test_nvidia_command_failure_is_inaccessible() -> None:
    def failed(_command: list[str]) -> str:
        raise subprocess.CalledProcessError(9, "nvidia-smi")

    adapter = NvidiaAdapter(run_command=failed, which=lambda _name: "/fake/nvidia-smi")
    result = ResourceProbe(adapters=[adapter]).snapshot()
    assert result["enforcement"] == "advisory"
    assert result["accelerators"][0]["status"] == "inaccessible"
    assert result["accelerators"][0]["devices"] == []
