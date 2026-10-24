"""The one place Argus core reaches the optional hosted trial.

Core asks ``hosting().<question>`` and never imports the trial package. When
the trial is installed, ``hosting()`` is its ``argus.trial.hosting`` module;
when it is not, ``NO_HOSTING`` answers instead, which is exactly how Argus
behaves today with the trial switched off: nothing is hosted, rewritten,
redacted, captured or restricted.

Every question is listed in ``NO_HOSTING``; ``argus.trial.hosting`` answers
the same names.
"""
from __future__ import annotations

import functools
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace


def _no_setup(url: str, **_kwargs) -> int:
    raise ValueError(f"this Argus has no hosted trial to set up for {url}")


NO_HOSTING = SimpleNamespace(
    # Selection: is the hosted trial on, and what model/effort it fixes.
    enabled=lambda env=None: False,
    client_model="",
    reasoning_effort="",
    model_options=lambda model, effort, *, env=None: (model, effort),
    # Copilot provider: credentials, log redaction and the usage store.
    apply_provider=lambda env: env,
    redactions=lambda: (),
    copilot_session_store=lambda: None,
    setup=_no_setup,
    # Account pause after a failed hosted call.
    attention_reason=lambda: "",
    record_failure=lambda diagnostic: diagnostic,
    # Training capture around agent calls and daemon launches.
    capture_runtime_call=lambda ctx, options: nullcontext(),
    daemon_launch_payload=lambda config: None,
    arm_daemon_launch=lambda payload, config: None,
    prepare_daemon_fork=lambda config: None,
    wait_daemon_claim=lambda handoff: None,
    complete_daemon_fork=lambda handoff, *, forked=True: None,
    # Service-managed plugins.
    plugin_setup_actions=lambda: None,
    plugin_middleware=lambda name: None,
)


@functools.cache
def hosting() -> ModuleType | SimpleNamespace:
    """The installed hosting package, or ``NO_HOSTING`` when there is none."""
    try:
        from ..trial import hosting as installed
    except ModuleNotFoundError as exc:
        if exc.name != "argus.trial":
            raise
        return NO_HOSTING
    return installed
