"""Server-metered Argus trial. Provider credentials never enter client config."""

TOKEN_LIMIT = 1_000_000
WEB_TOKEN_LIMIT = 10_000_000
MAX_CONCURRENCY = 10
GLOBAL_TPM = 10_000_000
TPM_WINDOW_SECONDS = 60
MODEL = "argus-trial"
# The client selector is not evidence of the actual upstream model.
CLIENT_MODEL = MODEL
REASONING_EFFORT = "high"
MAP_REASONING_DEFAULTS = {
    "ARGUS_SKILL_MAP_REASONING_EFFORT": "medium",
    "ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT": "high",
}
MAX_OUTPUT_TOKENS = 16_384
TRIAL_KEY_COUNT = 10
TRIAL_URL = "https://argusbot.cn"
