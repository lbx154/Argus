"""The one place Argus core reaches hosting, which lives in the hosting repository.

Core asks ``hosting().<question>`` and never names the hosting package: an
installed one registers itself under the ``argus.hosting`` entry point. With
none, ``NO_HOSTING`` answers instead: nothing is hosted, rewritten, redacted,
captured or restricted.

Every question is listed in ``NO_HOSTING``; a hosting package answers the same
names.
"""
from __future__ import annotations

import functools
from contextlib import nullcontext
from importlib.metadata import entry_points
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
    """The hosting package registered under ``argus.hosting``, or ``NO_HOSTING``."""
    for entry in entry_points(group="argus.hosting"):
        return entry.load()
    return NO_HOSTING
