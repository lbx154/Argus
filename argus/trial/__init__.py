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
MAP_REASONING_DEFAULTS = {
    "ARGUS_SKILL_MAP_REASONING_EFFORT": "auto",
    "ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT": "auto",
}
# Values earlier trial runtimes persisted on their own (not operator choices).
# Carrying them forward would keep every map open on the heavy tier.
_LEGACY_MAP_REASONING = {
    "ARGUS_SKILL_MAP_REASONING_EFFORT": "medium",
    "ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT": "high",
}


def map_reasoning_knobs() -> dict[str, str]:
    """Map effort knobs for a trial runtime, keeping explicit operator values."""
    from ..core.knobs import resolve_knob

    knobs = {name: resolve_knob(name, default) for name, default in MAP_REASONING_DEFAULTS.items()}
    # Only the exact pair the runtime wrote together is treated as unchosen.
    legacy = all(knob.source == "persisted" and knob.value == _LEGACY_MAP_REASONING[name]
                 for name, knob in knobs.items())
    return {name: MAP_REASONING_DEFAULTS[name] if legacy else knob.value for name, knob in knobs.items()}
MAX_OUTPUT_TOKENS = 16_384
TRIAL_KEY_COUNT = 10
TRIAL_URL = "https://argusbot.cn"
