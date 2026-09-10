"""Server-metered Argus trial. Provider credentials never enter client config."""

TOKEN_LIMIT = 1_000_000
MAX_CONCURRENCY = 10
GLOBAL_TPM = 10_000_000
TPM_WINDOW_SECONDS = 60
MODEL = "argus-trial"
CLIENT_MODEL = "gpt-4.1"
MAX_OUTPUT_TOKENS = 16_384
TRIAL_KEY_COUNT = 10
TRIAL_URL = "https://argusbot.cn"
