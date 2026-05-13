# Architecture

> **Status note:** `argus-skill` now exposes a unified REPL, detached
> life worker, and optional Telegram poller. The map below covers the
> live tree; historical upstream paths are called out only for
> provenance.

`argus-skill` is a thin GLUE layer over the current core / scientist /
engineer / life stack. This doc maps the live files to their upstream
origin or notes when a module is new in this repo.

## Live module map

| argus-skill file | Upstream | Notes |
|---|---|---|
| `argus_skill/core/models.py` | ArgusBot/codex_autoloop/models.py | Slimmed: kept `CheckResult` / `ReviewDecision`; added `LoopOutcome`. |
| `argus_skill/core/ports.py` | ArgusBot/codex_autoloop/core/ports.py | Shared protocols, plus `RunnerBackend` and `SkillSource`. |
| `argus_skill/core/project.py` | new | Project fingerprinting from cwd or git remote. |
| `argus_skill/scientist/prompts.py` | skill-agent/skill_agent/prompts.py | Match / distill prompt logic. |
| `argus_skill/scientist/distiller.py` | new | Scientist wrapper around `Prompts.distill(...)`. |
| `argus_skill/skills/layered.py` | new | Layered skill helpers. |
| `argus_skill/skills/lifecycle.py` | new | Reinforce / distill / revise / retire dispatch. |
| `argus_skill/skills/quality.py` | new | Skill quality helpers. |
| `argus_skill/skills/store.py` | skill-agent/skill_agent/skill_store.py | Markdown skill cache + fit-graded matcher. |
| `argus_skill/engineer/reviewer.py` | ArgusBot/codex_autoloop/reviewer.py | Reviewer loop adapted to `RunnerBackend`. |
| `argus_skill/engineer/checks.py` | ArgusBot/codex_autoloop/checks.py | Acceptance-check helpers. |
| `argus_skill/engineer/reviewer_schema.json` | ArgusBot/codex_autoloop/reviewer_schema.json | Reviewer JSON schema. |
| `argus_skill/engineer/runner.py` | new | `SupervisedEngineer` round-loop control flow. |
| `argus_skill/adapters/memory_backend.py` | new | Deterministic backend for tests / smoke runs. |
| `argus_skill/adapters/codex_backend.py` | new | Real CLI backend wrapper. |
| `argus_skill/adapters/stream_progress.py` | new | Live-output plumbing shared by backends. |
| `argus_skill/critic/critic.py` | new | Critic + planner for iteration / continuous mode. |
| `argus_skill/daemon/token_lock.py` | ArgusBot/codex_autoloop/token_lock.py | Process lock helper, vendored verbatim. |
| `argus_skill/daemon/life_worker.py` | new | Detached life worker around `LifeSupervisor`. |
| `argus_skill/apps/_inbox.py` | new | Shared inbox queue / drain / event formatting helpers. |
| `argus_skill/apps/_life_actions.py` | new | Shared non-interactive backlog, config, status, and `/run` helpers. |
| `argus_skill/apps/_target_paths.py` | new | Shared life-dir / project-root resolution helpers. |
| `argus_skill/life/memory.py` | new | Persistent memory primitives. |
| `argus_skill/life/event_log.py` | new | Event JSONL writer / rotator. |
| `argus_skill/life/status.py` | new | Shared backlog-status selectors and continuous-state description helpers. |
| `argus_skill/life/notify.py` | new | Best-effort journal notifications. |
| `argus_skill/life/telegram_bot.py` | new | Optional Telegram inbound command bridge. |
| `argus_skill/life/router.py` | new | Event routing helpers. |
| `argus_skill/life/supervisor.py` | new | Mission scheduler and iteration loop. |
| `argus_skill/apps/cli.py` | new | CLI entry point and one-shot action dispatcher. |
| `argus_skill/apps/_life_repl.py` | new | Unified REPL surface. |
| `argus_skill/apps/_skill_stats.py` | new | Non-interactive skill stats command. |
| `argus_skill/apps/_skill_cleanse.py` | new | Skill migration helper. |
| `argus_skill/apps/_watch.py` | new | Read-only live cockpit. |
| `argus_skill/apps/_init_identity.py` | new | Identity-card wizard. |
| `argus_skill/apps/_input_helpers.py` | new | Shared REPL input helpers. |
| `argus_skill/cli/branding.py` | new | CLI branding helpers. |
| `argus_skill/cli/event_format.py` | new | Event rendering helpers. |
| `argus_skill/cli/render.py` | new | Terminal rendering helpers. |
| `argus_skill/cli/theme.py` | new | Theme helpers. |
| `argus_skill/loop.py` | new | SkillLoop integration glue. |
| `argus_skill/__main__.py` | new | `python -m argus_skill` entry point. |

## Layout Notes

The current tree is intentionally flatter than the historical one. The
daemon, REPL, and Telegram poller all share the same current
`apps/cli.py` entry point, but not the same on-disk root: global
identity/journal/skill state lives at `~/.argus-skill/`, while the
active project card, memory journal, backlog, event log, inbox, and
process state live under `~/.argus-skill/projects/<fingerprint>/`.
Continuous mode is coordinated through each project's `continuous.json`
via `read_continuous_state()` / `write_continuous_config()`, and the
CLI `--status` command reports the current project state alongside the
shared global journal.

The current `apps/cli.py`, `apps/_life_repl.py`, `apps/_watch.py`, and
`life/telegram_bot.py` surfaces now share the helper modules above:
`apps/_inbox.py` handles inbox queueing and draining, `apps/_life_actions.py`
holds the reusable backlog/config/status/run helpers, `apps/_target_paths.py`
keeps life-root resolution consistent, and `life/status.py` centralizes the
running-item and continuous-state selectors.

## How a task flows through the loop

1. **Match.** `SkillStore.find_relevant(task)` ranks skills by
   frontmatter and token overlap.
2. **Distill (if miss).** `Distiller.distill(task)` asks the scientist
   to author a new skill when nothing fits.
3. **Round k.** `SupervisedEngineer.run` builds the engineer prompt,
   runs checks, then asks the reviewer for a verdict.
4. **Classify.** `done + checks pass → status="done"`, `blocked →
   "blocked"`, otherwise keep iterating up to `max_rounds`.
5. **Writeback (on done).** `SkillStore.writeback_from_trajectory`
   updates the skill history so the matcher sees the successful path.

## Tests as living docs

The smoke tests in `tests/test_loop_smoke.py` still document the main
behaviour contract:

* distill on miss, then converge in two rounds.
* blocked short-circuits immediately.
* max-rounds stays bounded.
* `--no-distill-on-miss` falls back to a skill-less run.
