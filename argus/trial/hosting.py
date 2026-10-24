"""The hosted trial's answers to Argus core's hosting questions.

``argus.core.hosting.hosting()`` returns this module; ``NO_HOSTING`` there
lists the same names with the answers Argus uses when no trial is installed.
"""
from __future__ import annotations

import os

from . import CLIENT_MODEL as client_model
from . import REASONING_EFFORT as reasoning_effort
from .attention import reason as attention_reason
from .attention import record_failure
from .client import apply_trial_provider as apply_provider
from .client import profile_path
from .client import runtime_redactions as redactions
from .client import setup_trial as setup
from .client import trial_enabled as enabled
from .client import trial_model_options as model_options
from .training_runtime import (
    arm_daemon_launch,
    capture_runtime_call,
    complete_daemon_fork,
    daemon_launch_payload,
    prepare_daemon_fork,
    wait_daemon_claim,
)

__all__ = [
    "apply_provider", "arm_daemon_launch", "attention_reason", "capture_runtime_call",
    "client_model", "complete_daemon_fork", "copilot_session_store", "daemon_launch_payload",
    "enabled", "model_options", "plugin_middleware", "plugin_setup_actions",
    "prepare_daemon_fork", "reasoning_effort", "record_failure", "redactions", "setup",
    "wait_daemon_claim",
]


def copilot_session_store():
    """The trial Copilot home's usage store, or ``None`` outside the trial."""
    return profile_path().parent / "copilot-trial-home" / "session-store.db" if enabled() else None


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
