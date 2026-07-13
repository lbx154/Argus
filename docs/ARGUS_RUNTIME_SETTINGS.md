# Argus Runtime Settings Snapshot

- Generated: `2026-07-07T08:17:59Z`
- Schema: `1`

## Role Hyperparameters

| Role | Backend | Model | Effort | Sources |
| --- | --- | --- | --- | --- |
| manager | Codex | `gpt-5.5` | `xhigh` | backend: default: codex; model: capability vault / default: gpt-5.5; effort: default: xhigh |
| planner | Codex | `gpt-5.5` | `xhigh` | backend: default: codex; model: capability vault / default: gpt-5.5; effort: default: xhigh |
| engineer | Codex | `gpt-5.5` | `xhigh` | backend: default: codex; model: capability vault / default: gpt-5.5; effort: default: xhigh |
| reviewer | Codex | `gpt-5.5` | `xhigh` | backend: default: codex; model: capability vault / default: gpt-5.5; effort: default: xhigh |

## Operator Knobs

| Group | Name | Value | Source | Default |
| --- | --- | --- | --- | --- |
| backend | `ARGUS_SKILL_LIFE_BACKEND` | `codex` | default | `codex` |
| backend | `ARGUS_SKILL_RUNNER_BIN` | `(agent CLI on PATH)` | default | `(agent CLI on PATH)` |
| backend | `ARGUS_SKILL_ENGINEER_BACKEND` | `(=LIFE_BACKEND)` | default | `(=LIFE_BACKEND)` |
| backend | `ARGUS_SKILL_REVIEWER_BACKEND` | `(=LIFE_BACKEND)` | default | `(=LIFE_BACKEND)` |
| backend | `ARGUS_SKILL_PLANNER_BACKEND` | `(=LIFE_BACKEND)` | default | `(=LIFE_BACKEND)` |
| backend | `ARGUS_SKILL_MANAGER_BACKEND` | `(=LIFE_BACKEND)` | default | `(=LIFE_BACKEND)` |
| backend | `ARGUS_SKILL_ENGINEER_RUNNER_BIN` | `(=RUNNER_BIN)` | default | `(=RUNNER_BIN)` |
| backend | `ARGUS_SKILL_REVIEWER_RUNNER_BIN` | `(=RUNNER_BIN)` | default | `(=RUNNER_BIN)` |
| backend | `ARGUS_SKILL_PLANNER_RUNNER_BIN` | `(=RUNNER_BIN)` | default | `(=RUNNER_BIN)` |
| backend | `ARGUS_SKILL_MANAGER_RUNNER_BIN` | `(=RUNNER_BIN)` | default | `(=RUNNER_BIN)` |
| backend | `ARGUS_SKILL_CURATOR_BACKEND` | `(=LIFE_BACKEND)` | default | `(=LIFE_BACKEND)` |
| backend | `ARGUS_SKILL_CURATOR_RUNNER_BIN` | `(=RUNNER_BIN)` | default | `(=RUNNER_BIN)` |
| models | `ARGUS_SKILL_CURATOR_MODEL` | `gpt-5.5` | default | `gpt-5.5` |
| reasoning | `ARGUS_SKILL_CURATOR_REASONING_EFFORT` | `high` | default | `high` |
| mission | `ARGUS_SKILL_CURATOR_DISTILL_INTERVAL_S` | `1260` | default | `1260` |
| team | `ARGUS_TEAMMATE_FORCE_RESEARCH` | `off` | default | `off` |
| team | `ARGUS_TEAMMATE_RESEARCH_PROMPT` | `(built-in, domain-neutral)` | default | `(built-in, domain-neutral)` |
| team | `ARGUS_TEAMMATE_FORCE_PROFILE` | `off` | default | `off` |
| team | `ARGUS_TEAMMATE_PROFILE_CMD` | `(unset)` | default | `(unset)` |
| team | `ARGUS_TEAMMATE_PROFILE_HEADER` | `(built-in, domain-neutral)` | default | `(built-in, domain-neutral)` |
| team | `ARGUS_TEAMMATE_PROFILE_REQUIRE_SUBSTR` | `(unset → accept any non-empty)` | default | `(unset → accept any non-empty)` |
| team | `ARGUS_TEAMMATE_PAPER_MISSION` | `(inherit lead default)` | default | `(inherit lead default)` |
| team | `ARGUS_TEAMMATE_TIMEOUT_S` | `5400` | default | `5400` |
| team | `ARGUS_TEAMMATE_MAX_ROUNDS` | `200` | default | `200` |
| team | `ARGUS_TEAMMATE_RESULT_FILE` | `(unset)` | default | `(unset)` |
| team | `ARGUS_LEADERBOARD_LOWER_IS_BETTER` | `off (higher-is-better)` | default | `off (higher-is-better)` |
| models | `ARGUS_SKILL_MODEL` | `gpt-5.5` | default | `gpt-5.5` |
| models | `ARGUS_SKILL_ENGINEER_MODEL` | `gpt-5.5` | default | `gpt-5.5` |
| models | `ARGUS_SKILL_REVIEWER_MODEL` | `gpt-5.5` | default | `gpt-5.5` |
| models | `ARGUS_SKILL_PLAN_MODEL` | `gpt-5.5` | default | `gpt-5.5` |
| models | `ARGUS_SKILL_MATCHER_MODEL` | `gpt-5.5` | default | `gpt-5.5` |
| reasoning | `ARGUS_SKILL_MANAGER_REASONING_EFFORT` | `xhigh` | default | `xhigh` |
| reasoning | `ARGUS_SKILL_PLANNER_REASONING_EFFORT` | `xhigh` | default | `xhigh` |
| reasoning | `ARGUS_SKILL_ENGINEER_REASONING_EFFORT` | `xhigh` | default | `xhigh` |
| reasoning | `ARGUS_SKILL_REVIEWER_REASONING_EFFORT` | `xhigh` | default | `xhigh` |
| budget | `ARGUS_SKILL_PER_MISSION_CAP_USD` | `30.0` | default | `30.0` |
| budget | `ARGUS_SKILL_DAILY_CAP_USD` | `180.0` | default | `180.0` |
| mission | `ARGUS_SKILL_VERTICAL` | `(unset → research; see LANES #1)` | default | `(unset → research; see LANES #1)` |
| mission | `ARGUS_SKILL_MAX_ROUNDS` | `500` | default | `500` |
| mission | `ARGUS_SKILL_SHIFT_ROUND_LIMIT` | `3` | default | `3` |
| mission | `ARGUS_SKILL_THREAD_TOKEN_LIMIT` | `1500000` | default | `1500000` |
| mission | `ARGUS_SKILL_MANAGER_LOCK_TIMEOUT_S` | `120` | default | `120` |
| mission | `ARGUS_SKILL_CHECKPOINT_PERSIST` | `true` | default | `true` |
| lifecycle | `ARGUS_SKILL_DAEMON_AUTO_RESTART` | `0` | default | `0` |
| lifecycle | `ARGUS_SKILL_AUTOCOMMIT_SKILLS` | `off` | default | `off` |
| lifecycle | `ARGUS_SKILL_PER_MISSION_DISTILL` | `off` | default | `off` |
| lifecycle | `ARGUS_SKILL_SAFE_MODE` | `off` | default | `off` |
| lifecycle | `ARGUS_SKILL_ENGINEER_SANDBOX` | `off` | default | `off` |
| lifecycle | `ARGUS_SKILL_MEASURED_MODE` | `off` | default | `off` |
| lifecycle | `ARGUS_SKILL_SKIP_VAULT_PREFLIGHT` | `off` | default | `off` |
| meta | `ARGUS_META_JUMP_FROZEN_THRESHOLD` | `12` | default | `12` |
| telemetry | `ARGUS_SKILL_ENABLE_TELEGRAM` | `off` | default | `off` |
| telemetry | `ARGUS_SKILL_TELEGRAM_BOT_TOKEN` | `(unset)` | default | `(unset)` |
| telemetry | `ARGUS_SKILL_TELEGRAM_CHAT_ID` | `(unset)` | default | `(unset)` |
| telemetry | `ARGUS_SKILL_SHOW_REASONING` | `0` | default | `0` |

## Change From Argus

- `switch the model to <name>`
- `把模型换成 <name>`
- `把backend换成 <name>`
- `effort 设为 <low|medium|high|xhigh>`
- `/backend`
- `/config`
