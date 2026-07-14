# Unified Argus Command Surface

**Date:** 2026-07-14

## Goal

Give the Ink terminal cockpit and React Web cockpit the same deterministic
slash-command surface while preserving Argus's natural-language-first Manager
front door. Restore reliable `argus` startup under
`/home/argustest/argustest2` without removing that directory's isolated state,
Copilot home, or port.

## Current State

The Ink cockpit owns a 34-command registry in
`frontend/tui/src/input/slash.ts`. It uses that registry for completion,
`/help`, parsing, aliases, typo suggestions, and dispatch.

The Web cockpit has the corresponding API methods and several dedicated
panels, but its command palette is assembled independently in `App.tsx`.
The chat composer only intercepts `/rename`; most slash input is sent to the
Manager as ordinary text. The two clients therefore expose different controls
despite using the same WebAPI.

The directory-local shell wrapper for `/home/argustest/argustest2` currently:

- forces `ARGUS_TUI_PORT=8899`;
- isolates `ARGUS_SKILL_HOME` and `COPILOT_HOME`;
- runs a TUI bundle outside the current `argus-skill-latest` release.

Port 8899 is occupied by the wrapper's previous WebAPI release. The current
client correctly rejects it because the client and backend release IDs differ.
This is a release-selection problem, not a working-directory or command lookup
failure.

## User-Facing Command Contract

Natural-language input remains the default. Any non-slash input goes unchanged
to the Manager, which decides whether to answer directly or dispatch work.

Both cockpits expose these canonical slash commands and aliases:

| Group | Commands |
| --- | --- |
| Everyday | `/status`, `/roles`, `/journal [N]`, `/backlog [all]`, `/artifacts`, `/artifact <path>`, `/events [filter] [query]`, `/find <text>`, `/cancel` |
| Task management | `/task <text>` (`/add`), `/plan <objective>`, `/nudge <text>` (`/inject`, `/notify`), `/abort`, `/note <text>`, `/done <id>`, `/skip <id>` (`/rm`), `/stop <id>`, `/item <id>`, `/run` |
| Sessions and diagnostics | `/new [objective]`, `/daemons [query]`, `/resume [list\|<id>]`, `/attach <id\|prefix>`, `/rename <name>`, `/doctor` |
| Configuration | `/backend [codex\|claude\|copilot]`, `/config [key=value ...]`, `/identity [set <text>]`, `/reset`, `/skills [ls\|promote <name>]` |
| Local | `/clear`, `/reconnect`, `/help` (`/?`, `/commands`), `/quit` (`/exit`, `/q`) |

The launcher's flags remain a separate non-interactive surface. `argus
--help` continues to document session selection, host/port, Web launch,
objective, and headless smoke options. Internal `argus-skill` administration
flags are not presented as chat slash commands.

## Architecture

### Shared command registry

Move command metadata into a dependency-free module under `frontend/core`.
Each entry contains:

- stable action ID;
- canonical slash name;
- argument hint;
- description;
- aliases;
- display group;
- behavior kind (`panel`, `action`, or `local`).

The shared module also owns pure parsing, completion, alias canonicalization,
argument extraction, help grouping, and typo suggestion. It must not import
React, Ink, browser APIs, Node APIs, or an API client.

The TUI keeps its existing switch-based dispatch and UI components, but imports
the registry and pure helpers from `frontend/core`. This preserves behavior
while removing the duplicate source of truth.

The Web imports the same registry. A Web-specific dispatcher maps stable action
IDs to existing API calls, overlays, project switching, feed controls, and
session actions. Client-specific behavior remains outside the shared module.

### Web composer and completion

The Web composer parses trimmed input before sending:

1. Non-slash input is sent to the Manager unchanged.
2. A known slash command is dispatched locally and is never sent to the
   Manager.
3. An unknown slash command shows an error and a nearest-command suggestion
   when available.
4. A known command with missing required arguments shows its usage and leaves
   the operator in the composer.

Typing `/` as the first non-whitespace character opens an accessible completion
menu above the composer. It supports keyboard selection, Enter/Tab completion,
Escape dismissal, pointer selection, and a bounded scroll window on mobile.
The menu disappears once the command token has been completed and argument
entry begins.

The Ctrl/Cmd+K palette is generated from the same registry plus project rows.
Commands that are safe without arguments execute immediately. Commands that
need operator input close the palette, focus the composer, and prefill the
canonical command plus a trailing space. Panel commands open their existing Web
view.

`/quit` in a browser does not attempt to close a tab. It leaves the current
project view and displays an explicit browser-safe notice. This is the only
intentional platform-specific semantic difference.

## Directory-Local Startup Recovery

The existing isolation contract is retained:

- launch scope: `/home/argustest/argustest2` and descendants;
- API port: 8899;
- project state: the directory-local `ARGUS_SKILL_HOME`;
- model sessions: the dedicated `COPILOT_HOME`;
- session discovery may additionally include the account-level Argus root.

The shell wrapper must resolve both the `argus` TUI and `argus-skill` WebAPI
from `/home/argustest/argustest2/argus-skill-latest`, so both sides carry the
same release manifest.

On an incompatible local API:

1. Probe `/api/meta` and capture the release identity.
2. Determine ownership using a PID record written by the directory-local
   launcher. Verify the PID is alive, its command is an Argus WebAPI, its port
   is 8899, and its resolved executable belongs to the expected checkout.
3. If every ownership check passes and no mission mutation is in flight,
   request graceful shutdown, wait for the listener to disappear, and start the
   current WebAPI release.
4. If ownership cannot be proven, do not signal the process. Report the PID,
   detected release, expected release, and explicit recovery commands.
5. If graceful shutdown fails, stop and report the blocker. Never escalate to
   a force kill during automatic startup.

The stable public 8799 proxy and its 8798 backend are outside this recovery
path and must not be restarted.

## Error Handling

- API errors remain visible and do not produce success-shaped notices.
- Slash commands with invalid arguments show the canonical usage.
- Mutating commands are disabled while the same mutation is pending.
- A release mismatch includes endpoint, detected release, expected release,
  and whether ownership was proven.
- Automatic recovery is fail-closed whenever process ownership or mission
  safety is ambiguous.
- Normal Manager text is never reclassified by the slash dispatcher.

## Testing

### Shared contract

- Registry has unique canonical names, aliases, and stable action IDs.
- Parsing, completion, aliases, help grouping, and typo suggestions preserve
  current TUI behavior.
- Both clients import the shared registry; no second command list is allowed.

### Web

- Every canonical command reaches its intended dispatcher branch.
- Aliases dispatch to the canonical action.
- Unknown and missing-argument cases remain local and show useful errors.
- Natural-language text still reaches the Manager once.
- Completion supports keyboard, pointer, dismissal, and mobile overflow.
- The command palette contains every command and prefills argument-bearing
  commands correctly.
- Existing panel, API protocol, rename, and Manager streaming tests remain
  green.

### Startup

- Matching 8899 API connects without restart.
- Proven directory-local stale API is gracefully replaced.
- Unknown process on 8899 is never signaled.
- Failed graceful shutdown is surfaced without force killing.
- The restarted API and TUI report the same release ID.
- The wrapper still preserves local session filtering and dedicated state.

## Acceptance Criteria

1. `argus` launched anywhere under `/home/argustest/argustest2` reaches the
   isolated 8899 cockpit with matching client/backend release IDs.
2. Entering `/` in either cockpit exposes the same 34 canonical commands and
   aliases.
3. Every Web slash command either performs the same operation as the TUI or
   documents the single browser-specific `/quit` behavior.
4. Ctrl/Cmd+K and `/help` are generated from the shared registry.
5. Plain language continues to use the Manager front door unchanged.
6. Automatic stale-process recovery cannot terminate an unowned process or
   touch the public 8798/8799 deployment.
