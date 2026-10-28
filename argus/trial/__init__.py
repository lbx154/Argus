"""The Argus trial-key client. Provider credentials never enter client config.

``argus --setup --trial`` and the desktop trial route this Argus through a
hosted trial gateway with a private key. The hosted trial itself (gateway,
portal, compute queue, training capture) is the separate ``argus_trial_server``
package under ``trial_server/``; it imports this one, never the reverse.

Layer: delivery
"""

MODEL = "argus-trial"
# The client selector is not evidence of the actual upstream model.
CLIENT_MODEL = MODEL
REASONING_EFFORT = "high"
MAX_OUTPUT_TOKENS = 16_384
TRIAL_URL = "https://argusbot.cn"
