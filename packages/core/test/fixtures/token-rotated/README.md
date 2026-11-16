Trimmed from a system_proof trial on Claude Code (2026-11-16). The gateway token in ~/.claude/settings.json
was rotated at 12:17 while Argus ran. Argus had started with the old token in its own environment and
passed ANTHROPIC_AUTH_TOKEN into every role's sandbox, where it outranks settings.json: from 12:21 every
call failed with 401, and the retried calls too, since each got the same stale value. Started without
ANTHROPIC_* in its environment, Argus's roles read settings.json at each start and went on.
