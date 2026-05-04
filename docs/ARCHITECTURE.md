# Architecture

argus-skill is a thin GLUE layer over two existing projects. This doc
maps every file to its upstream origin and notes any non-trivial
adaptation.

## File provenance map

| argus-skill file | Upstream | Adaptation |
|---|---|---|
| `argus_skill/core/models.py` | ArgusBot/codex_autoloop/models.py | Slimmed: kept CheckResult / ReviewDecision; dropped PlanSnapshot / RoundSummary; renamed CodexRunResult → RunnerResult; added LoopOutcome (new). |
| `argus_skill/core/ports.py` | ArgusBot/codex_autoloop/core/ports.py | Kept ControlChannel / NotificationSink / EventSink. Added RunnerBackend (new — replaces ArgusBot's hard-coded CodexRunner) and SkillSource (new — extracted from skill-agent's SkillStore implicit interface). |
| `argus_skill/scientist/prompts.py` | skill-agent/skill_agent/prompts.py | **Verbatim.** Includes Phase A's Coverage check + When NOT to use + fit-graded matcher. |
| `argus_skill/scientist/distiller.py` | new | Wraps `Prompts.distill(...)` + a RunnerBackend call. The split out of `agent.py` was necessary so the loop can call it without dragging in the rest of skill-agent. |
| `argus_skill/skills/store.py` | skill-agent/skill_agent/skill_store.py | Refactored constructor: takes `runner: RunnerBackend` + `matcher_model` instead of importing `codex_exec`. Added `render_skill`, `save_distilled`, `writeback_from_trajectory` to satisfy the SkillSource protocol. |
| `argus_skill/engineer/reviewer.py` | ArgusBot/codex_autoloop/reviewer.py | Refactored to take `RunnerBackend` protocol instead of `CodexRunner`. Parsing helpers (`parse_decision_text`, `_coerce_decision_against_main_summary`, etc.) are byte-for-byte verbatim. |
| `argus_skill/engineer/reviewer_schema.json` | ArgusBot/codex_autoloop/reviewer_schema.json | Verbatim. |
| `argus_skill/engineer/checks.py` | ArgusBot/codex_autoloop/checks.py | Verbatim except for the import path (`models.CheckResult` → `..core.models.CheckResult`). |
| `argus_skill/engineer/runner.py` | new | The `SupervisedEngineer` class — the round-loop control flow. Replaces what `LoopEngine` does in ArgusBot, but for a single-agent (no planner / explore subagent) shape. |
| `argus_skill/adapters/memory_backend.py` | new | Deterministic stub backend for tests + smoke runs. |
| `argus_skill/adapters/codex_backend.py` | new (thin wrapper over `ArgusBot/codex_autoloop/codex_runner.py`) | `CodexRunnerBackend` — the production backend. Translates argus-skill's `RunnerOptions`/`RunnerResult` to/from ArgusBot's, catches subprocess failures, best-effort token accounting from JSON event stream. |
| `argus_skill/adapters/control_channels.py` | shaped after `ArgusBot/codex_autoloop/adapters/control_channels.py` | TelegramControlChannel + LocalBusControlChannel + CompositeControlChannel. FeishuControlChannel intentionally dropped. |
| `argus_skill/adapters/event_sinks.py` | shaped after `ArgusBot/codex_autoloop/adapters/event_sinks.py` | TerminalEventSink + JsonlEventSink + TelegramEventSink + CompositeEventSink. Dashboard / Feishu sinks intentionally dropped. |
| `argus_skill/daemon/__init__.py` | new | package marker |
| `argus_skill/daemon/token_lock.py` | `ArgusBot/codex_autoloop/token_lock.py` | **Verbatim** (single-process exclusion via fcntl/msvcrt + JSON sidecar). |
| `argus_skill/daemon/bus.py` | `ArgusBot/codex_autoloop/daemon_bus.py` | **Verbatim** (`JsonlCommandBus`, `BusCommand`, `read_status` / `write_status` / `inspect_daemon_status`). |
| `argus_skill/daemon/runtime.py` | new (structurally inspired by `ArgusBot/codex_autoloop/apps/daemon_app.py`) | `Daemon` class: queue-based 7×24 wrapper around `SkillLoop`. Honours `/run`, `/inject`, `/skip`, `/stop`, `/status`. Periodic status writer + graceful shutdown. |
| `argus_skill/telegram/__init__.py` | new | package marker |
| `argus_skill/telegram/poller.py` | trimmed from `ArgusBot/codex_autoloop/telegram_control.py` | `TelegramCommandPoller` + slim `parse_command_text`. Kept: `/run`, `/inject`, `/interrupt`, `/skip`, `/stop`, `/status`, `/help`, plain-text-as-inject, full-width slash normalisation. Dropped: voice/Whisper, plan-mode, callback queries, `/btw`, `/criteria`, `/show-*`, `/clock`, `/new`, `/fresh-session`, `/confirm-send`. |
| `argus_skill/telegram/notifier.py` | trimmed from `ArgusBot/codex_autoloop/telegram_notifier.py` | `TelegramNotifier` with `sendMessage` + `sendDocument` + 3900-char chunking + typing pulse. Live-edit / photo-special-case dropped. |
| `argus_skill/apps/daemon_app.py` | new | Wires `argus-skill daemon` + `daemon-status|stop|inject|run` subcommands. |
| `argus_skill/loop.py` | new | The `SkillLoop` — the actual integration: matcher → distill-on-miss → SupervisedEngineer → skill writeback on success. Optional `extra_guidance_provider` hook lets the daemon append `/inject` text into each round's prompt. |
| `argus_skill/apps/cli.py` | new (loose inspiration from skill-agent's `__main__.py`) | Minimal `argus-skill run` CLI; routes `daemon*` subcommands into `daemon_app`. |
| `argus_skill/__main__.py` | new | Entry-point shim. |
| `tests/test_loop_smoke.py` | new | End-to-end test of SkillLoop with the memory backend. |
| `tests/test_reviewer_parse.py` | new | Reviewer JSON parsing tests; structurally similar to ArgusBot's reviewer tests. |
| `tests/test_skill_store.py` | new | Skill store + matcher tests; structurally similar to skill-agent's `test_skill_store_matcher.py`. |
| `tests/test_telegram_parse.py` | new | Slim Telegram command parser unit tests. |
| `tests/test_daemon_bus.py` | new | JSONL bus + status helpers unit tests. |
| `tests/test_daemon_localbus.py` | new | End-to-end integration test: Daemon + LocalBusControlChannel + memory backend. |

## Why this layout

The merge plan (kept in `skill-agent/docs/MERGE_PLAN_ARGUSBOT.md`) lists
8 milestones (M1-M8). argus-skill v0.1 covers M1 + M3 + the M4 skeleton:

* **M1 (vendor reviewer + checks + SupervisedExecutor):** done.
  See `engineer/runner.py` + `engineer/reviewer.py` + `engineer/checks.py`.
* **M3 (skill writeback on done):** v0.1-light. The current writeback
  appends the task to history and bumps `created_at`; it does not yet
  re-call the scientist with the trajectory to edit the playbook
  itself. v0.2 will extend `writeback_from_trajectory` to do that.
* **M4 (three-layer skeleton):** done. `core/` / `scientist/` /
  `engineer/` / `skills/` / `adapters/` / `apps/` all isolated.

Not yet vendored:

* **M2 (harbor benchmark adapter)** — argus-skill's CLI is generic; a
  benchmark driver is left to the upstream skill-agent's
  `benchmarks/harbor_adapter.py`, adapted to call `SkillLoop` instead of
  `codex_exec`.
* **M5 (workflow + capability dual-skill prompts)** — v0.2 work.
* **M6 (daemon + Telegram)** — needs `apps/daemon_app.py` +
  `adapters/control_channels.py`. Leaving those for a follow-up PR
  because they pull in extra deps (Telegram bot lib).
* **M7 (REPL with reviewer)** — replaces `ui.py` from skill-agent.
* **M8 (final report / pptx)** — workflow polish; deferred.

## Backend protocol (RunnerBackend)

The single seam that makes everything testable is:

```python
class RunnerBackend(Protocol):
    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult: ...
```

The `run_label` field is critical: it lets the same backend instance
distinguish matcher / distiller / engineer-r1 / engineer-r2 / reviewer
calls. The `MemoryBackend` keys its canned-response queue on this
label. Real backends typically only use it for logging.

Backends to implement (not in v0.1):

* `CodexBackend` — wraps `subprocess.Popen` of the `codex` CLI. Should
  resolve `RunnerOptions.output_schema_path` for reviewer JSON.
* `ClaudeBackend` — wraps the Anthropic `claude-code` CLI. Same shape.
* `CopilotBackend` — wraps `copilot` CLI.

The skill-agent repo's `skill_agent/backends.py` already has working
codex + claude wrappers; porting them to `RunnerBackend` is a
constructor-signature change away (~50 lines).

## How a task flows through the loop

1. **Match.** `SkillStore.find_relevant(task)` lists skills by
   frontmatter, ranks by token overlap, asks the matcher LLM with
   `Prompts.skill_match(...)`. Only `fit=high` results survive.
2. **Distill (if miss).** `Distiller.distill(task)` calls the scientist
   with `Prompts.distill(...)`. The result is parsed by
   `Prompts.parse_skill_output` and saved with
   `SkillStore.save_distilled`.
3. **Round k.** `SupervisedEngineer.run` builds the engineer prompt
   from `task + skill block + reviewer next_action (if any)`, runs the
   engineer, runs `check_commands`, calls `Reviewer.evaluate(...)`.
4. **Classify.** `done + checks pass → status="done"`. `blocked → "blocked"`.
   `done + checks fail → continue`. Else loop. Hit `max_rounds → "max_rounds"`.
5. **Writeback (on done).** `SkillStore.writeback_from_trajectory`
   updates the skill's `task_history` and `created_at`. The next
   matcher pass will see the longer history and rank this skill
   higher for similar tasks.

## Tests as living docs

Each test in `tests/test_loop_smoke.py` is named after a behaviour
contract:

* `test_skill_loop_distill_then_two_rounds_to_done` — happy path.
* `test_skill_loop_blocked_short_circuits` — `blocked` short-circuits.
* `test_skill_loop_max_rounds_hit` — bounded rounds, no infinite loop.
* `test_skill_loop_no_distill_falls_back_to_no_skill` — opt-out works.

If you change the loop's contract, change one of these (or add a new
one) first.
