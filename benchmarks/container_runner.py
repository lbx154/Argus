"""Compat shim — the implementation moved to argus_skill.runners.container.

This module is preserved so any external code importing
``benchmarks.container_runner`` keeps working. New code should import
directly from :mod:`argus_skill.runners.container`.
"""
from __future__ import annotations

from argus_skill.runners.container import (  # noqa: F401
    ContainerCodexRunner,
    ContainerCodexRunnerConfig,
)

__all__ = ["ContainerCodexRunner", "ContainerCodexRunnerConfig"]
