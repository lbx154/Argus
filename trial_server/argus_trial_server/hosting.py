"""The hosted trial runtime's answers to Argus core's hosting questions.

Registered under the ``argus.hosting`` entry point (``trial_server/pyproject.toml``);
``argus.trial.hosting`` asks this module the runtime questions listed in
``argus.core.hosting.NO_HOSTING`` and answers the trial-key ones itself.
"""
from __future__ import annotations

import os

from .training_runtime import (
    arm_daemon_launch,
    capture_runtime_call,
    complete_daemon_fork,
    daemon_launch_payload,
    prepare_daemon_fork,
    wait_daemon_claim,
)

__all__ = [
    "arm_daemon_launch", "capture_runtime_call", "complete_daemon_fork", "daemon_launch_payload",
    "plugin_middleware", "plugin_setup_actions", "prepare_daemon_fork", "wait_daemon_claim",
]


def plugin_setup_actions():
    """Plugin setup actions a trial harness still allows, or ``None`` for all."""
    if not os.environ.get("ARGUS_TRIAL_HARNESS"):
        return None
    from .plugins import SETUP_ACTIONS

    return SETUP_ACTIONS


def plugin_middleware(name: str):
    """The trial harness's workspace boundary for CrystalPilot, else ``None``."""
    if not (os.environ.get("ARGUS_TRIAL_HARNESS") and name == "crystalpilot"):
        return None
    from .plugins import workspace_boundary

    return workspace_boundary
