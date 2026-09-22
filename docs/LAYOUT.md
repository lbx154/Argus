# Repository layout and declared package layering

This file is the declared package layering of `argus/` and the map of the
repository's top level. It is not advisory: `tests/test_architecture_invariants.py`
section 8 ("Declared layering") reads the layer table below, checks that every
package `__init__.py` names the same layer, and pins today's violations in three
strict-equality allowlists (module-level upward imports; deferred upward imports,
that is function-body or typing-only under `if TYPE_CHECKING:`; cross-package
private imports). The allowlists may only shrink. Fixing an upward edge means
deleting its allowlist line in the same PR; fixing the edge and leaving the line
makes the test fail too. Adding a package means adding it to the `LAYERS` table in
the test, to the table below, and to the Packages section. The test reads only the
Layer and Packages cells of the table; the May-import column is prose, except the
`engineer`/`reviewer` clause, which section 2 of the same file enforces.

Background, measurements and the decision cards are in
`docs/audits/architecture-clarity-2026-09-14.md`. The runtime maintenance map
(`docs/runtime-maintainability.md`, maintained by the runtime session) covers code
entry points, state ownership and recovery boundaries; this file covers package
placement and the dependency direction between packages. Vocabulary is in
`docs/CORE_CONCEPTS.md` (Glossary). Nothing in this file moves code; the Planned
moves section at the end is a plan, not a description of the tree.

## Where to look

- Mission execution: `argus/daemon/life_worker.py` (the detached worker) ->
  `life/supervisor/` (`LifeSupervisor` runs backlog items back-to-back) ->
  `apps/_runtime_execute.py` (builds one `SkillLoop` per backlog item and runs it)
  -> `loop.py` (`SkillLoop`, the round loop) -> `engineer/` + `reviewer/` (the
  Reviewer-gated rounds).
- Role prompts: `roles/prompts/<role>.py`. The `builtin_skills/<role>/argus-<role>-role.md`
  files are seeds written into a fresh Skill library, not the runtime prompts.
- Durable state: `~/.argus-skill/projects/<id>/` (`backlog.jsonl`, `events.jsonl`,
  `handoffs/`, `role-sessions/`, ...); the table is
  [docs/CORE_CONCEPTS.md, "Concept-to-storage mapping"](CORE_CONCEPTS.md#concept-to-storage-mapping).
- Event ledger: `life/event_log.py` is the only appender of `events.jsonl`; every
  other module reads it.
- Operator entry points: `apps/cli/` (the `argus` command line) and
  `webapi/routes/` (what the Web and Ink cockpits call).

## Layers

Layers are ordered low to high. A module's top-level `import` may only point at its
own layer or a lower one; deferred imports that point upward (function-body, or
typing-only under `if TYPE_CHECKING:`) are tolerated only while their
`(file, target package)` pair sits in the allowlist.

| Layer | Packages | May import |
|---|---|---|
| kernel | `core`, `proof_ledger` | stdlib and third-party only |
| providers | `agent_cli`, `provider_integrations`, `adapters`, `advisor` | kernel; inside the layer `adapters` -> `agent_cli`, `adapters` -> `provider_integrations`, `provider_integrations` -> `agent_cli` |
| capabilities | `tools`, `wiki`, `cli`, `skills` | kernel, providers, capabilities |
| domain | `verticals`, `domains`, `builtin_skills` | kernel through domain; a vertical is reached only through the bridge modules, never by name — the same seam the entry-point verticals of `argus-verticals` import |
| roles | `roles`, `planner`, `engineer`, `reviewer` | kernel through roles; `engineer` and `reviewer` never import `verticals` (existing test) |
| runtime | `life`, `manager`, `messaging` | kernel through runtime; the intra-layer cycle `life` <-> `manager` is tolerated (not measured by the tests) |
| process | `daemon`, `team` | kernel through process; the intra-layer cycle `daemon` <-> `team` is tolerated (not measured by the tests) |
| delivery | `apps`, `webapi`, `plugin`, `maintenance`, `trial`, `integrations`, `release_tools` | everything |

Notes on the table:

- The four package-root modules (`argus/__init__.py`, `__main__.py`, `loop.py`,
  `desktop_backend_entry.py`) count as layer `delivery` (source package `<root>`).
- Kernel exception with a fix note: `core/usage.py` imports
  `provider_integrations.copilot_usage` at module level. It is allowlisted and goes
  away when the usage ledger leaves `core` (decision card 12). The other kernel
  upward edges present today (`core/knobs.py` and `core/backend_readiness.py` ->
  `agent_cli`, `core/operator_messages.py` and `core/mission_view/_reduce_mission.py`
  -> `life`, `core/runtime_identity.py` -> package root) are pinned in the same
  allowlist and are removed by phase 1, leaving `core/usage.py` as the single pinned
  outward edge.
- Roughly two thirds of cross-package imports are written inside function bodies,
  and about 387 of those names are monkeypatch targets in tests. Do not hoist them
  to module level as a "fix"; the function-body allowlist exists so they shrink one
  at a time.

## Packages

One line per package, verified against the modules' docstrings on 2026-09-14. Where a
package name no longer describes its contents, the line says so and names the phase
that fixes it.

- `argus/core/` (kernel) - models, ports (`RunnerBackend` protocol), event catalog, contracts (`vertical_contract`, `research_contract`, `project_contract`), `paths`, OS primitives (`file_lock`, `process_stop`, `windows_job`). Today it also holds four tiers marked as extraction candidates (card 12): backend config and readiness (`knobs`, `knob_store`, `config_snapshot`, `backend_readiness`, `role_config`), cost/usage accounting (`usage`, `cost_control`, `cost_events`, `pricing`, `provider_quota`, `token_usage`), operator stores (`operator_context`, `operator_decision`, `operator_messages`, `operator_presence`, `transcript`), and the cockpit read model `mission_view/` plus paper/venue policy (`venue_review`, `manuscript_snapshot`, `manuscript_narrative_runtime`); plus the workbench plugin installer (`plugin_manager`, `plugin_runtime`, `workbench_plugins`).
- `argus/proof_ledger/` (kernel) - append-only ledger of claims, evidence and proof routes for mathematical results; imports nothing from Argus and nothing outside the standard library.
- `argus/agent_cli/` (providers) - low-level driver for the codex/claude/copilot/cursor/opencode/pi/grok/dsh CLIs: argv construction, process control, event parsing, prompt delivery, warm `copilot --acp` client. `runner_backend.py` holds the backend-name `Literal` (becomes `core/backend_names.py` in phase 1).
- `argus/provider_integrations/` (providers) - provider-specific telemetry and policy: Copilot per-call usage and cross-process guard, the single bounded 401 replay.
- `argus/adapters/` (providers) - `AgentCliBackend` (the real `RunnerBackend` over `agent_cli`, split into admission/spawn/finalize phases), the deterministic in-memory backend for tests, and the stream-progress forwarder.
- `argus/advisor/` (providers) - independent, evidence-bound advice requested by a role: an outbound side-channel to an external advisor model (Copilot, MCP, Pi extension) with receipts and evidence; it advises, it never adjudicates. Added upstream on 2026-09-13; its call-time imports of `life` and `trial` are pinned as upward edges.
- `argus/tools/` (capabilities) - operator-approved tools that missions invoke as `python -m argus.tools.<x>`: subagent (submit/supervise background jobs), team CLI, capability vault, resource ledger, image API, PDF chat, Lean check, GPU lease, setup wizard, event-log query. These module paths are cited by prompts and Skills and are frozen.
- `argus/wiki/` (capabilities) - minimal per-project Wiki: semantic pages plus one `INDEX.md`.
- `argus/cli/` (capabilities) - ANSI theme, role colours and event rendering for headless and teammate terminals. It is terminal rendering, not the command-line interface (that is `apps/cli/`); renamed `terminal/` in phase 6.
- `argus/skills/` (capabilities) - the Skill library proper: `store` (two-field frontmatter markdown), `layered` roots, `builtins` seeding, role recall (about 1.7k lines). Today it also holds the pipeline stage machine and vertical selection (`stage_machine`, `vertical_select`, `checklist_store`; moves to `pipeline/` in phase 3), the RL research gates (`run_contract`, `rl_training_health`, `rl_training_plots`, `anti_mediocrity`, `evidence_chain`; move to `verticals/research/` in phase 2) and the `SkillLoop` mixins (`loop_*`; move to `mission_runner/` in phase 5).
- `argus/verticals/` (domain) - 7 built-in verticals, one directory each (`<domain>/stages.py` implements `VerticalContract`): `research`, `software`, `argus_maintenance`, `kernel_engineering`, `math`, `math_synth`, `learning`; plus the framework-owned bridge modules `_base`, `_registry`, `_data_domain`, `store`, `research_bridge`, `metric_evidence`, `optimization_base`, `path_evidence`. Seventeen more verticals (quant, speedrun, kernelbench, nanochat, nanogpt_speedrun, chip_design, digital_circuit, digital_circuit_benchmark, medical, materials, physics, ale_last_exam and the five literary verticals) moved to the community repository `argus-verticals` on 2026-09-14. They reach a running Argus two ways, both resolved by `_registry.py`: the **Vertical Store** (`store.py`, 2026-09-14: one directory per vertical installed from the repository's release zips under `<ARGUS_SKILL_HOME>/verticals/argus_verticals/<name>/`, sha256-verified, no pip, `registry.json` re-read when its mtime changes; surfaces `argus verticals ...`, `/api/verticals`, `release_tools/preinstall_verticals`; see `docs/vertical-store.md`) and the `argus.verticals` entry-point group of a pip-installed `argus-verticals` (memoised per process; the group spelled with the pre-rename package name `argus_skill` is read as well for one release, and a name registered in both is taken from the new group). Precedence for one name: managed workbench plugin, then entry point, then store. Framework modules the community package imports today -- changes to any of them are breaking for `argus-verticals`: the bridge modules `verticals/{_base,_data_domain,metric_evidence,optimization_base,path_evidence,research_bridge}`, `verticals/kernel_engineering/tool_registry`, `skills/stage_machine.ChecklistItem`, `skills/vertical_select.available_vertical_purposes`, `core/{file_digest,models,pipeline_state,repair_freshness}`, `team/result_provenance` and `manager.Manager`. The built-in inventory `VERTICALS` lives in `skills/vertical_select.py` and is reachable here only through `builtin_verticals()`; runtime code uses the merged `available_verticals()`. `DEFAULT_VERTICAL` is defined twice (`verticals/_base.py`, `skills/vertical_select.py`); both become `verticals/inventory.py` in phase 2.
- `argus/domains/` (domain) - domain overlays composed onto a workflow vertical (today: `chemistry`). "Domain" here means overlay; a project-local DATA domain is a different thing and lives in `verticals/_data_domain.py`.
- `argus/builtin_skills/` (domain) - the markdown Skills seeded into a fresh `~/.argus-skill` (per-role folders plus cross-vertical defaults); read by `skills/builtins.py`. The `argus-*-role.md` files are seeds, not the runtime role prompts (those are `roles/prompts/*.py`).
- `argus/roles/` (roles) - the role prompt catalog (`RoleName`, per-role operation sets, one resolver for role/vertical/stage/scope fragments) and the shared task contract.
- `argus/planner/` (roles) - the read-only Planner that inspects project state and delegates concrete work; bounded-DAG pass for Manager-authored tasks.
- `argus/engineer/` (roles) - `SupervisedEngineer`: the Reviewer-gated round loop, split by round phase (prompt, execution, waits, reviewer, self-review, settlement).
- `argus/reviewer/` (roles) - the L2 Reviewer: graded done/continue/blocked verdicts, pure verdict parsers, editable review file.
- `argus/life/` (runtime) - one Project's continuous life: persistent memory (`EventJournal`, `Backlog`, `IdentityCard`, `LifeMemory`), `event_log` (the only appender), `LifeSupervisor` (runs missions back-to-back and drives the continuous planner), operator channels (chat router, Feishu, Telegram, letters), project lifecycle and research plan state.
- `argus/manager/` (runtime) - the Manager control plane: front-door routing shared by the TUI and Web API, vertical decision and domain authoring, stage-transition authority, dispatch, directives, plan mode, Skill tidy.
- `argus/messaging/` (runtime) - advisory messages between projects owned by one Argus user/tenant: store, inbox, transport and handler beside `life` and `manager`. Added upstream on 2026-09-13; `adapters` and `tools` import it at call time (pinned upward edges).
- `argus/daemon/` (process) - the detached 7x24 life worker: boot and run phases, admission caps, blue/green handoff, status sidecar and stop control, health, durable commands, spawn helper.
- `argus/team/` (process) - Agent Teams plumbing: durable task board, roster, pool control file, daemon-resident Curator (owner of teammate process lifetime), leaderboard, teammate entrypoint.
- `argus/apps/` (delivery) - the Python CLI (`apps/cli/`; `python -m argus`, and every admin flag and subcommand of the `argus` command), the `argus` console-script launcher (`tui_launcher`: the Ink cockpit by default, the CLI for admin flags), updaters, `--watch` and `--init-identity`. Today it also holds the mission runtime (`_runtime*.py`, `_self_reply.py`; about 4.6k lines shared by daemon, teammate runner and Manager front door; moves to `mission_runner/` in phase 5) and the operator inbox helpers `_inbox`, `_life_actions` (move to `life/` in phase 4).
- `argus/webapi/` (delivery) - the FastAPI server and `routes/` that the Web and Ink cockpits consume. It also hosts the framework-free `manager_*`, `map_*`, `daemon_*`, `mission_items`, `project_state`, `diagnostics` service modules that `plugin/`, `apps/`, `maintenance/`, `trial/` and `life/chat` import: a service layer that has not been named as one. `fastapi` and `uvicorn` are hard dependencies (`[project.dependencies]`); there is no `[web]` extra (the docstring that claimed one was fixed in phase 0).
- `argus/plugin/` (delivery) - the host-plugin facade and stdio MCP server behind the `argus-plugin-server` script; installed from `plugins/argus/`.
- `argus/maintenance/` (delivery) - the Doctor: read-only findings, closed repair registry, installed-Agent advisor, and the deployment boundary. Not the `argus_maintenance` vertical and not the `~/.argus-skill/maintenance/` decision cards; renamed `doctor/` in phase 6.
- `argus/trial/` (delivery) - the server-metered hosted trial: gateway, egress, compute queue, portal, admin, training capture. 40 files (39 `.py`) ship in the wheel; the product proper uses about four of them. Needs the `[trial]` extra.
- `argus/integrations/` (delivery) - lets the Harbor Framework invoke the complete Argus runtime as an installed agent. Distinct from the top-level `integrations/` directory below.
- `argus/release_tools/` (delivery) - release and CI tooling: plugin wheel build, event fixture and TypeScript type generators, the PR gate, repository parity check.
- Package-root modules (delivery): `argus/__init__.py` (public API, lazy via PEP 562 from phase 0), `__main__.py` (`python -m argus`; re-exports `apps.cli.main` and is the target of the pre-rename `argus-skill` console script, kept one release), `loop.py` (`SkillLoop`; moves to `mission_runner/` in phase 5), `desktop_backend_entry.py` (PyInstaller entry for the frozen desktop backend).

## Repository top level

The tracked top-level directories are listed below. `technical_report/` is
private-mirror only, see `PRIVATE_ONLY_PATTERNS` in
`argus/release_tools/repository_parity.py`. "Not built, not tested, not
shipped" means no CI job, no test, and no wheel content comes from the directory
(decision card 5 leaves them in place for now).

- `.agents/` - agent-host marketplace manifest plus the `minimal-rigorous-work` Skill for agents working on this repository.
- `.claude-plugin/` - Claude Code marketplace manifest pointing at `plugins/argus`.
- `.github/` - CI workflows: `tests` (ruff and full pytest on Linux, TypeScript packages on Linux/macOS/Windows, frontend contract consumers on Linux), `extended` (mypy, additional portability/frontend/desktop checks; on demand), `pr-gate`, `release`, `desktop-cache`, `desktop-trial`; plus Copilot instructions.
- `argus/` - the Python package; everything above. Also carries `plugin_catalog.json`.
- `argus_skill/` - the two-file import alias for the package's pre-rename name (`__init__.py` installs a `sys.meta_path` finder so `argus_skill[.x]` is the same module object as `argus[.x]`; `__main__.py` delegates to `argus.__main__`). Shipped in the wheel for one release after 2026-09-14, then removed. Not a package of its own, not type-checked, no layer.
- `companions/` - `FLYWHEEL`, a standalone research-data-flywheel control plane that talks to Argus only over the versioned WebAPI. Not built, not tested, not shipped.
- `contrib/` - community contributions (`figure-studio` paper-figure pipeline, `pi-research-workflow-skill` for Pi/Hermes). Not built, not tested, not shipped.
- `deploy/` - systemd units and Dockerfiles for the hosted trial (`deploy/trial/`).
- `desktop-tauri/` - the Tauri desktop shell and the PyInstaller spec (`argus_backend.spec`) for the frozen `argus-backend` binary (the spec's `name=`).
- `docs/` - operator and developer documentation; `docs/audits/` holds dated audit reports and their data attachments.
- `frontend/` - `core` (shared TypeScript), `tui` (Ink terminal cockpit), `web` (React web cockpit). `frontend/web/dist` is committed on purpose and force-included into the wheel.
- `integrations/` - the `agent-skills` package for external agent hosts (`SKILL.md` plus per-host adapters). Not the Python package `argus/integrations/`.
- `packages/` - the root npm workspace: `contracts` owns shared event/API definitions and TypeScript generation; `runtime` contains the experimental Pi transport. Built and tested by `npm run check` and CI. Contract schemas ship with the Python wheel and frozen desktop backend; the experimental runtime does not replace the production Python daemon. See [TypeScript migration](typescript-migration.md).
- `plugins/` - the installable `argus` host plugin for Claude Code and Codex: MCP config, bundled Skills, install scripts.
- `research/` - generated architecture-audit output (about 2 MB) and maintenance decisions. Not built, not tested, not shipped.
- `scripts/` - one-off repository scripts (brand asset generation).
- `tools/` - opt-in operator tooling kept outside the package: `rl_experiment_benchmark/` (a local benchmark of whether Argus carries out an RL experiment on its own: `start.py` attaches a declared case to a session, `collect.py` gathers the outcome; see its README).
- `technical_report/` - LaTeX sources and compiled PDF of the technical report. Private-mirror only (`repository_parity.PRIVATE_ONLY_PATTERNS`). Not built, not tested, not shipped.
- `tests/` - the pytest suite. `tests/<pkg>/` mirrors some packages; about 200 files still sit at the root (phase 10 unifies the convention).
- `update/` - two follow-up notes for the PR gate. Not built, not tested, not shipped.

Root files that puzzle newcomers:

- `argus_doctor.py` - stdlib-only bootstrap doctor behind the `argus-doctor` script, force-included in the wheel. Stdlib-only and outside the package on purpose: it must run when the venv or `argus` itself is broken, so it probes them as a subprocess (`<venv>/python -c "import argus; ..."`). Not affected by the layering.
- `ARGUS_IMPRESSIVE_RESULTS.md`, `ARGUS_IMPRESSIVE_RESULTS.zh-CN.md` - a campaign list of candidate results a third party could reproduce; explicitly not a list of achievements. Private-mirror only (`repository_parity.PRIVATE_ONLY_PATTERNS`).
- `PRIVATE_TODO.md`, `PRIVATE_TODO.zh-CN.md` - the allowlisted overlay TODO for the private mirror repository; public `main` is the authority.

## Planned moves (planned, not done)

Each move phase is a zero-content `git mv` commit, a one-line import commit with a
`sys.modules` alias shim, and one allowlist line deleted. Every phase from 1 onward
requires restarting daemons and the processes running from the dev-tree `.venv`.
Details and decision cards: `docs/audits/architecture-clarity-2026-09-14.md`, 4.4 and 5.

1. `core` becomes a module-level leaf (except `core/usage.py`, card 12): `agent_cli/runner_backend.py` -> `core/backend_names.py` (`BackendName`), `agent_cli/_process_control.py` -> `core/process_control.py`, `life/event_log.py` -> `core/event_log.py`, `life/mission_outcome.py` -> `core/mission_outcome.py`, `tools/capability_vault.py` -> `core/capability_vault.py` (forwarder stays), `core/agent_probe.py` -> `adapters/agent_probe.py`, new `core/version.py`.
2. Contract owns its value types: `ChecklistItem` -> `core/vertical_contract.py`; new `verticals/inventory.py` (`VERTICALS`, `DEFAULT_VERTICAL`, aliases); RL gates -> `verticals/research/`, re-exported through `verticals/research_bridge.py`.
3. New `pipeline/`: `stage_machine`, `vertical_select`, `checklist_store` leave `skills/` (card 7).
4. `apps/_inbox.py` -> `life/inbox.py`, `apps/_life_actions.py` -> `life/operator_actions.py`.
5. New `mission_runner/`: `apps/_runtime*.py`, `apps/_self_reply.py`, `loop.py`, `skills/loop_*.py`; public names `SkillLoopRunner`, `build_life_runner`, `run_life_supervisor` (card 7).
6. Cheap renames and one re-runnable sed: `cli/` -> `terminal/`, `maintenance/` -> `doctor/`; shims from phases 1-5 deleted; kill-first restarts are mandatory from here on.
7. Public loader names: `verticals/_base.py`, `_registry.py`, `_data_domain.py` -> `loader.py`, `registry.py`, `data_domain.py`; `core.paths` gains `projects_root` / `project_state_root`; `project_root_or_404` -> `global_root_or_404`.
8. (gated, card 3) `MemoryBundle.root` gets one meaning; project-level files written to the host root move under `projects/<id>/`.
9. (gated, cards 9 and 10) one `core` atomic write replaces about 12 near-copies; single source for the role tuple and the event file name; zero-importer modules deleted.
10. (gated, card 11) tests mirror packages (`tests/verticals/<domain>/`), missing `__init__.py` added, phase 7 shims and path aliases removed.
