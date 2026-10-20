"""Server-metered Argus trial. Provider credentials never enter client config.

Layer: delivery
"""

TOKEN_LIMIT = 1_000_000
WEB_TOKEN_LIMIT = 10_000_000
MAX_CONCURRENCY = 10
GLOBAL_TPM = 10_000_000
TPM_WINDOW_SECONDS = 60
MODEL = "argus-trial"
# The client selector is not evidence of the actual upstream model.
CLIENT_MODEL = MODEL
# Server configuration is distinct from the opaque desktop/wire selector.
# Operators may choose another actual upstream model in gateway Settings.
DEFAULT_UPSTREAM_MODEL = "gpt-5.5"
REASONING_EFFORT = "high"
# Map copy is read-time presentation: ``auto`` selects the light map tier.
LIGHT_REASONING_KNOB = "ARGUS_SKILL_LIGHT_REASONING_EFFORT"


def light_reasoning_knobs() -> dict[str, str]:
    """The light-route effort for a trial runtime, keeping an explicit operator value."""
    from ..core.knobs import resolve_knob

    return {LIGHT_REASONING_KNOB: resolve_knob(LIGHT_REASONING_KNOB, "low").value}


MAX_OUTPUT_TOKENS = 16_384
TRIAL_KEY_COUNT = 10
TRIAL_URL = "https://argusbot.cn"
