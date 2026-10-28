"""The trial client's answers to Argus core's hosting questions.

``argus.core.hosting.hosting()`` returns this module; ``NO_HOSTING`` there
lists the same names with the answers Argus uses when no trial is installed.
The trial key, model and Copilot provider are answered here. The questions
only a hosted trial runtime can answer (training capture, daemon launch
tickets, workspace plugins) go to the runtime registered under the
``argus.hosting`` entry point, which this module never names.
"""
from __future__ import annotations

from ..core.hosting import hosted_runtime
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


def capture_runtime_call(ctx, options):
    return hosted_runtime().capture_runtime_call(ctx, options)


def daemon_launch_payload(config):
    return hosted_runtime().daemon_launch_payload(config)


def arm_daemon_launch(payload, config):
    return hosted_runtime().arm_daemon_launch(payload, config)


def prepare_daemon_fork(config):
    return hosted_runtime().prepare_daemon_fork(config)


def wait_daemon_claim(handoff):
    return hosted_runtime().wait_daemon_claim(handoff)


def complete_daemon_fork(handoff, *, forked=True):
    return hosted_runtime().complete_daemon_fork(handoff, forked=forked)


def plugin_setup_actions():
    return hosted_runtime().plugin_setup_actions()


def plugin_middleware(name):
    return hosted_runtime().plugin_middleware(name)
