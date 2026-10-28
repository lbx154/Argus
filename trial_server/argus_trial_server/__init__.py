"""Server-metered Argus trial: gateway, egress, compute queue, portal, admin, training capture.

Argus never imports this package. It answers the hosted runtime's questions in
``argus.core.hosting`` through the ``argus.hosting`` entry point (see
``hosting.py``), and shares the trial wire constants with the client.
"""
from argus.trial import CLIENT_MODEL, MAX_OUTPUT_TOKENS, MODEL, REASONING_EFFORT, TRIAL_URL

__all__ = [
    "CLIENT_MODEL", "DEFAULT_UPSTREAM_MODEL", "GLOBAL_TPM", "LIGHT_REASONING_KNOB",
    "MAX_CONCURRENCY", "MAX_OUTPUT_TOKENS", "MODEL", "REASONING_EFFORT", "TOKEN_LIMIT",
    "TPM_WINDOW_SECONDS", "TRIAL_KEY_COUNT", "TRIAL_URL", "WEB_TOKEN_LIMIT", "light_reasoning_knobs",
]

TOKEN_LIMIT = 1_000_000
WEB_TOKEN_LIMIT = 10_000_000
MAX_CONCURRENCY = 10
GLOBAL_TPM = 10_000_000
TPM_WINDOW_SECONDS = 60
# Server configuration is distinct from the opaque desktop/wire selector.
# Operators may choose another actual upstream model in gateway Settings.
DEFAULT_UPSTREAM_MODEL = "gpt-5.5"
# Map copy is read-time presentation: ``auto`` selects the light map tier.
LIGHT_REASONING_KNOB = "ARGUS_SKILL_LIGHT_REASONING_EFFORT"
TRIAL_KEY_COUNT = 10


def light_reasoning_knobs() -> dict[str, str]:
    """The light-route effort for a trial runtime, keeping an explicit operator value."""
    from argus.core.knobs import resolve_knob

    return {LIGHT_REASONING_KNOB: resolve_knob(LIGHT_REASONING_KNOB, "low").value}
