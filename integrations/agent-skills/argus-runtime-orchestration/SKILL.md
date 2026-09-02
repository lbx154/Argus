---
name: "argus-runtime-orchestration"
description: "Portable outer-operator procedure for durable Argus missions across five hosts."
---

# Argus runtime orchestration

Use when a task benefits from durable state, artifacts, experiments, background execution, independent review, pause/resume, or continuation beyond one model turn. Do not use for a quick answer, simple read-only inspection, or a small operation the current host can safely finish and verify directly.

## Known limitations

- Argus does not provide a universally portable push/callback from **Needs you** into every outer operator. The outer operator must actively inspect compact Argus status/output at meaningful boundaries. Intervention detection may not be real-time when the host cannot maintain durable monitoring, and this skill forbids claiming unattended supervision in that case.
- The Hermes Agent and Claude Code adapters are currently documentation-validated rather than locally execution-tested on this host.
- Some Copilot CLI skill commands in current documentation are absent from the locally tested 1.0.39 build, so feature detection is required.

## Two-party operating model and configuration

There are exactly two operational parties:

1. The **outer operator**: OpenClaw, Hermes Agent, Claude Code, Codex CLI, Copilot CLI, Cursor CLI, or another shell-capable agent following this skill. It stages the mission, launches and observes Argus, handles operator questions within delegated authority, verifies artifacts, and reports compactly.
2. **Argus**: the durable supervised system that plans, executes, reviews, persists state, surfaces questions, and produces evidence and artifacts.

Any model/provider CLI Argus uses internally—such as `codex`, `copilot`, `claude`, `cursor`, `pi`, `opencode`, or `grok`—is Argus configuration and an implementation detail, not a third role, peer actor, or separate party in this procedure. The outer operator may select or verify Argus's configured internal model/provider during preflight when operationally necessary and authorized; otherwise it treats Argus as one durable supervised system. Do not infer Argus configuration from the outer operator's identity, even when names happen to match.

Resolve the executable explicitly; do not assume one universal install path. POSIX shell:

```bash
if [ -n "${ARGUS_BIN:-}" ]; then
  : # operator supplied an executable path
elif command -v argus >/dev/null 2>&1; then
  ARGUS_BIN="$(command -v argus)"
elif [ -n "${ARGUS_INSTALL:-}" ]; then
  ARGUS_BIN="$ARGUS_INSTALL/.venv/bin/argus"
else
  echo "Set ARGUS_BIN or ARGUS_INSTALL" >&2; exit 2
fi
```

Native PowerShell:

```powershell
if ($env:ARGUS_BIN) {
  $ArgusBin = $env:ARGUS_BIN
} elseif (Get-Command argus -ErrorAction SilentlyContinue) {
  $ArgusBin = (Get-Command argus).Source
} elseif ($env:ARGUS_INSTALL) {
  $ArgusBin = Join-Path $env:ARGUS_INSTALL ".venv\Scripts\argus.exe"
} else {
  throw "Set ARGUS_BIN or ARGUS_INSTALL"
}
```

The command examples below use POSIX `"$ARGUS_BIN" ...` syntax. In PowerShell,
invoke the resolved path as `& $ArgusBin ...`.

For a source checkout, `ARGUS_INSTALL="$HOME/src/Argus"` is an example only, not a portable default. Record the resolved absolute executable path. Never download, reinstall, alter Argus's internal model/provider configuration, or widen permissions silently.

## Core operating procedure

1. **Choose one owned workdir and a real security boundary.** Use a dedicated absolute directory per Argus project. Never overlap another active Argus project's directory hierarchy. Do not move, delete, or share-write it while its daemon is active. True parallel work gets separate projects. The workdir is an ownership boundary, not a sandbox. `ARGUS_SKILL_SAFE_MODE=1` adds guardrails but is not itself isolation for every backend. Before staging sensitive inputs, require verified sandboxing for the selected backend or run Argus in an appropriately isolated OS/container boundary. Default unattended mode may grant internal agents access beyond the workdir.
2. **Stage bounded inputs.** Copy only task-relevant, disclosure-safe inputs under `inputs/`. Write `OBJECTIVE.md` with outcome, non-goals, output paths, acceptance checks, authority limits, and review/citation requirements. Before execution, check whether the mission contract could create a non-terminating cycle. If detected, revise the contract while preserving the user's objective and acceptance intent, then recheck it. Continue only when the contract has a stable completion condition. Do not stage ambient conversation history, credentials, or unrelated private data.
3. **Preflight from the exact workdir.** Run `"$ARGUS_BIN" doctor`, then probe `"$ARGUS_BIN" --status`. Use `doctor --deep` only when backend authentication must be verified; do not add that network/auth latency to every routine preflight. Deep Doctor is not universal proof of live authentication: when it reports an unchecked token, offline capability, or unreachable route, use that backend's documented non-mutating live readiness probe before dispatch. Continue only if the required checks pass, the intended project/workdir is identified, no ownership conflict exists, and any operationally required internal model/provider choice is installed and authenticated. The outer operator may select or verify that Argus configuration here when authorized, but must not treat it as another operational party. The current Argus top-level help is intentionally terse and may omit supported automation flags; before using `--notify`, daemon, bounded, or API surfaces against another build, verify them from that build's docs/parser or a safe probe. See `references/argus-runtime-surfaces.md`.
4. **Keep internal execution configuration explicit only when needed.** Prefer Argus's already configured, installed, authenticated internal model/provider. Qualify provider names where Argus requires it. Do not silently change configuration after a failed readiness check; diagnose first and obtain approval when the change exceeds delegated authority.
5. **Dispatch one standalone mission.** From the workdir, give Argus a self-contained objective naming staged inputs and outputs. Request inspect → execute → validate → repair → final-artifact closure. Defer to Argus's native risk/Vertical review policy; explicitly require independent review only when the task contract or material risk requires it. Keep one bounded deliverable per mission. For a one-off bounded mission where reusable learning is not requested and deployment policy permits, set `ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING=0` to avoid the default post-task learning model call; otherwise preserve the configured learning policy. Use the interactive cockpit when available; for detached execution, use Argus's documented daemon/bounded surfaces rather than an improvised shell loop.
6. **Preserve evidence boundaries inside Argus.** Argus may separate planning, engineering, and reviewing internally. Require decision-sized missions and artifact changes with evidence. When Argus's policy requires independent review, it must inspect actual artifacts, limits, citations, and completion. These are internal responsibilities, not additional parties in the outer operating model. Prefer one decisive check per claim; avoid ceremonial duplicate evidence or extra model turns.
7. **Monitor using the strongest available host capability.** Follow the selected adapter in `references/adapters/`. Host background/process APIs improve observation but are not the source of truth: Argus durable project state is. Use Argus's detached daemon only where the active environment preserves host processes. Otherwise save the exact workdir/project identity and relaunch Argus against that state in a later turn. If no detached launch is permitted, keep a foreground cockpit or ask the user to leave it running; never claim unattended monitoring.
8. **Run the pending-question loop at every boundary.** Inspect `"$ARGUS_BIN" --status` at dispatch, each meaningful status check, after blocked/paused/replan results, before leaving a run unattended, after answering, and before closeout. `waiting on you : N unanswered question(s)` is a stop signal. When available, also inspect the cockpit/Web UI **Needs you** prompt. On the validated Argus 0.1.1 build, API-capable operators may inspect `GET /api/projects/{sid}/status` or `GET /api/projects/{sid}/snapshot?compact=true`; both expose `pending_questions`, while full snapshots expose rows in `backlog`. These read routes may remain unauthenticated even when `ARGUS_SKILL_WEB_TOKEN` is configured: keep them loopback-only, or place them behind a trusted VPN/authenticating reverse proxy; never expose them directly to an untrusted network. Feature-detect these routes on other builds. Progress prose alone is insufficient.
9. **Classify each question.** A durable unresolved marker is a backlog row with non-empty `pending_question`; a typed card also has `operator_decision.status == "pending"`, plus `id`, `question`, `reason`, `evidence`, and optional `options`. This is not an informational chat question, objective text, review prose, or ordinary `/ask`. Treat the durable marker as blocking even beside `failed`, `paused_operator`, or success-looking text. Let Argus retain reversible technical routing unless a surfaced question clearly falls within delegated authority. Escalate credentials/secrets, spending, deletion/force-push/publication/external sends, irreversible or outward-facing actions, security/trust-boundary changes, legal/license decisions, material scope/acceptance changes, and ambiguous high-impact choices.
10. **Answer through an authoritative surface.** Prefer the item-specific **Needs you** card in terminal/Web cockpit. On the validated Argus 0.1.1 build, API clients may use `POST /api/projects/{sid}/decisions/{decision_id}/resolve` with `{"option_id":"…","note":"…"}`, or legacy `POST /api/projects/{sid}/backlog/{item_id}/answer` with `{"text":"…"}`. A normal cockpit reply or `POST /api/projects/{sid}/message` is Manager-routed only when exactly one question is pending; when several exist, address the specific card/item. `"$ARGUS_BIN" --notify '<answer>'` queues guidance and can be bound by Manager only when exactly one question is pending; queue acknowledgement is not resolution. Feature-detect non-cockpit routes on other builds. Never guess on the user's behalf.
11. **Verify recording, then resume.** On the validated build, require `resolved: true` where the surface returns it. Confirm the old `pending_question` cleared and a resolution was recorded. When a typed decision card exists, also require that card to become `resolved`; legacy untyped questions may have no card or `resolution_id`. Observe the continuation in its actual pending, running, completed, or stopped state rather than requiring it still be active at inspection time. A `stop` decision instead aborts the item, preserves work, disables continuous mode, and creates no continuation. Re-run status and verify execution resumed, completed, or stopped as intended. If the marker remains, clarify or escalate; do not send duplicate guesses.
12. **Inspect without importing the trajectory.** Use status/cockpit for current mission, role, stage, backlog, outcome, latest verified position, and Needs you. Treat backlog plus latest verified evidence as the frontier. Keep raw transcripts, daemon logs, agent I/O, usage logs, and private ledgers local.
13. **Retrieve and verify artifacts.** Read named output paths. Check existence, requested scope, and decisive tests. Resolve every material citation to the staged source; reject fabricated, stale-current, or claim-mismatched citations. Never infer success from a progress message.
14. **Recover from durable state.** Resume paused write/execution work by asking Argus to resume from durable state with a self-contained resume objective, not an inline chat answer. If an internal execution thread cannot resume, use Argus checkpoints/capsules. Diagnose unresolved questions, ownership, internal model/provider readiness, budget/cooldown, and infrastructure before changing Argus configuration or objective.
15. **Close only on evidence.** Finish when the bounded objective is met, artifacts and decisive checks pass, required Reviewer verdict is `done`, and no pending question/card remains. `continue`, `replan_requested`, `blocked`, research-pause, or unanswered Needs you is not completion. Distinguish mission completion from project/stage completion. Use `references/closeout-checklist.md`.

## Capability fallbacks

- **No native skill discovery:** provide the full bundle or explicitly instruct the host to read this file and the matching adapter; do not claim automatic activation.
- **No shell:** the host cannot directly operate local Argus. Use an approved remote shell/terminal integration or hand off exact operator steps; mark execution blocked rather than simulating it.
- **Shell but no process API:** prefer Argus `--daemon` only when the environment preserves processes; always persist project/workdir identity and re-enter with status/cockpit. Do not background with fragile `&`, `nohup`, or sleep/poll loops unless the host's documented environment makes that the only approved path.
- **Process API but not durable across host sessions:** use it for live logs only; durable Argus project state remains the recovery anchor, and the daemon may need to be relaunched.
- **No HTTP/API client:** use terminal/Web cockpit and `--status`. Never require API access for the question loop.
- **No interactive approval route:** fail closed on approval-gated actions. Split safe preflight from mutating launch, or ask the human to approve through that host's supported UI. Never bypass approval/sandbox flags merely to run unattended.
- **No unattended execution allowed:** keep an interactive session open or stop with a precise resume instruction and unresolved state. Report monitoring as conditional, not active.

## Privacy, authority, and return

Stage minimum data. Keep Web UI loopback-only unless the user approves protected remote access. Current user instructions and fresh evidence override prior plans or runtime suggestions.

Return to the invoking surface only: workdir/project identity, compact outcome/frontier, Reviewer verdict, artifact paths, decisive validation, citation exceptions, and unresolved operator-owned question/blocker. Do not assume that surface is a parent chat.
