"""Subagent unified system: re-export shim.

This module originally contained all ~2 500 lines of the subagent system.
It has been decomposed into cohesive sub-modules:

    _registry.py       -- registry/persistence, run-dir resolution, usage
    _discussion_log.py -- discussion transcript (jsonl + markdown mirror)
    _reporting.py      -- engineer inbox reporting and LLM-authored summaries
    _direct_run.py     -- direct Popen execution, codex LLM runner, RL detection
    _supervised_run.py -- supervised monitoring loop with health-adaptive backoff
    _discuss_run.py    -- stop-and-discuss parking loop

Every name that was previously importable from this module (and from the
package-level argus_skill.tools.subagent namespace) is still importable
from both locations unchanged.  _cli.py imports "from . import _core" and
"from ._core import ..."; both keep working via this shim.
"""
# ruff: noqa: F401, I001  -- re-export shim; imports are intentional and grouped by responsibility
from __future__ import annotations

import subprocess  # exposed so tests can do _core.subprocess.run / Popen / TimeoutExpired

from ._text import _find_codex  # exposed so tests can do _core._find_codex

# Registry / persistence
from ._registry import (
    DISCUSSION_STALE_AFTER_S,
    EXPERIMENT_HISTORY_REL,
    REGISTRY_DIR,
    SUPERVISOR_INTERVAL_CAP,
    SUPERVISOR_MODEL,
    SUPERVISOR_THREAD_MAX_CHECKS,
    _QUIET_LOGS_ENV,
    _SUPERVISOR_USAGE_BASELINE_FIELD,
    _ZERO_USAGE_TUPLE,
    _add_usage_totals,
    _append_experiment_history,
    _apply_supervisor_usage_fields,
    _child_env,
    _effective_run_dir,
    _exit_status_path,
    _format_metric_line,
    _is_pid_alive,
    _lane_of,
    _launch_durable_command,
    _list_tasks,
    _open_discussion_blockers,
    _parse_codex_jsonl_events,
    _persist_experiment_record,
    _progress_summary,
    _read_exit_code,
    _read_status_json,
    _read_summary_tsv,
    _read_task,
    _registry_path,
    _run_dir_from_command,
    _usage_delta_for_thread,
    _write_task,
    reconcile_terminal_task,
)

# Discussion transcript
from ._discussion_log import (
    _DISCUSSION_MSG_CAP,
    _append_discussion,
    _discussion_path,
    _engineer_turn_count,
    _mirror_discussion_md,
    _read_discussion,
    _render_discussion,
    _reset_discussion,
)

# Reporting
from ._reporting import (
    _alert_engineer,
    _build_report,
    _queue_to_inbox,
    _reply_back_block,
    _supervisor_summarize_report,
)

# Direct execution + codex runner + RL helpers
from ._direct_run import (
    _KNOB_ALIASES,
    _RL_COLLAPSE_GUIDANCE_CACHE,
    _RL_COLLAPSE_SKILL_REL,
    _RL_TRAINING_HINTS,
    _flag,
    _is_full_scale_rl,
    _looks_like_rl_training,
    _parse_launch_flags,
    _rl_collapse_guidance,
    _run_codex,
    _run_codex_with_usage,
    _run_contract_preflight,
    _run_direct,
    _strip_skill_frontmatter,
    _terminate_proc,
)

# Supervised monitoring loop
from ._supervised_run import (
    _run_supervised,
    _supervisor_check,
    _supervisor_check_with_usage,
)

# Pre-launch config preflight + health-adaptive interval backoff
from ._supervised_preflight import (
    _next_monitor_interval,
    _supervisor_preflight,
    _supervisor_preflight_with_usage,
)

# Discussion-mode driver
from ._discuss_run import (
    DISCUSSION_DEADLINE_S,
    DISCUSSION_FIRST_REPLY_TIMEOUT,
    DISCUSSION_POLL_INTERVAL,
    MAX_SUPERVISOR_TURNS,
    _run_discussion,
    _supervisor_discuss,
    _supervisor_discuss_with_usage,
)
