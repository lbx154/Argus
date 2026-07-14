# Unified Argus Command Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Ink TUI and React Web cockpit expose and execute the same 34 canonical slash commands from one shared registry.

**Architecture:** Move command metadata and pure parsing helpers into `frontend/core`, with compatibility re-exports for the TUI. Add a Web-specific handler map and composer completion UI; generate Web help and Ctrl/Cmd+K entries from the shared registry while leaving natural-language messages on the Manager path.

**Tech Stack:** TypeScript 5.6, React 18, Ink 5, Vitest, Node test runner, shared `frontend/core` modules.

## Global Constraints

- Natural-language input remains the default and must reach the Manager unchanged.
- The shared command module must not import React, Ink, browser APIs, Node APIs, or either API client.
- The canonical surface contains exactly 34 commands; aliases canonicalize before dispatch.
- Unknown slash input and missing required arguments remain local and never reach the Manager.
- `/quit` is browser-safe and must not call `window.close()`.
- No new runtime dependency is allowed.
- Existing four-role colors and blue-gold Web branding are unchanged.

---

### Task 1: Shared command registry and TUI compatibility

**Files:**
- Create: `frontend/core/src/commands.ts`
- Modify: `frontend/core/src/index.ts`
- Modify: `frontend/tui/src/input/slash.ts`
- Modify: `frontend/tui/test/input.test.ts`

**Interfaces:**
- Produces: `COMMANDS`, `CommandId`, `SlashCommand`, `ParsedCommand`, `parseCommand()`, `slashCompletions()`, `applyCompletion()`, `didYouMean()`, `parseEventViewArgs()`, `parseResumeTarget()`, and `helpGroups()`.
- Preserves: all imports currently served by `frontend/tui/src/input/slash.ts`.

- [ ] **Step 1: Extend the existing TUI test with shared-contract failures**

Add assertions to `frontend/tui/test/input.test.ts`:

```ts
import {
  COMMANDS,
  commandById,
} from '../../core/src/commands.js';

test('shared slash registry is complete and collision-free', () => {
  assert.equal(COMMANDS.length, 34);
  assert.equal(new Set(COMMANDS.map((row) => row.id)).size, 34);
  const names = COMMANDS.flatMap((row) => [row.name, ...(row.aliases ?? [])]);
  assert.equal(new Set(names.map((name) => name.toLowerCase())).size, names.length);
  assert.equal(commandById('status').name, '/status');
  assert.equal(commandById('quit').name, '/quit');
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
cd frontend/tui
node --import tsx --test test/input.test.ts
```

Expected: FAIL because `frontend/core/src/commands.ts` does not exist.

- [ ] **Step 3: Move the registry and pure helpers into frontend/core**

Create `frontend/core/src/commands.ts` by moving the pure definitions from
`frontend/tui/src/input/slash.ts`. Give every row a stable `id` and explicit
argument requirement:

```ts
export type CommandKind = 'panel' | 'action' | 'local';
export type CommandId =
  | 'status' | 'roles' | 'journal' | 'backlog' | 'artifacts' | 'artifact'
  | 'events' | 'find' | 'cancel' | 'task' | 'plan' | 'nudge' | 'abort'
  | 'note' | 'done' | 'skip' | 'stop' | 'item' | 'run' | 'new' | 'daemons'
  | 'resume' | 'attach' | 'rename' | 'doctor' | 'backend' | 'config'
  | 'identity' | 'reset' | 'skills' | 'clear' | 'reconnect' | 'help' | 'quit';

export interface SlashCommand {
  id: CommandId;
  name: `/${string}`;
  arg?: string;
  argument: 'none' | 'optional' | 'required';
  desc: string;
  aliases?: `/${string}`[];
  group: 'Everyday' | 'Task management' | 'Sessions & diagnostics' | 'Configuration' | 'Other';
  kind: CommandKind;
}
```

Populate `COMMANDS` with the exact 34 rows from the approved design. Export:

```ts
export function commandById(id: CommandId): SlashCommand {
  const command = COMMANDS.find((row) => row.id === id);
  if (!command) throw new Error(`unknown command id: ${id}`);
  return command;
}

export function commandNeedsArgument(command: SlashCommand): boolean {
  return command.argument === 'required';
}
```

Move the existing completion, parsing, event-view, resume-target, edit-distance,
and help-group functions unchanged except for using `COMMANDS`.

Export the module from `frontend/core/src/index.ts`:

```ts
export * from './commands.js';
```

Replace `frontend/tui/src/input/slash.ts` with compatibility re-exports:

```ts
export {
  COMMANDS as SLASH_COMMANDS,
  applyCompletion,
  didYouMean,
  helpGroups,
  isSlash,
  parseCommand,
  parseEventViewArgs,
  parseResumeTarget,
  slashCompletions,
} from '../../../core/src/commands.js';
export type {
  EventViewArgs,
  ParsedCommand,
  ResumeTarget,
  SlashCommand as SlashCmd,
} from '../../../core/src/commands.js';
```

- [ ] **Step 4: Run TUI parsing and type tests**

Run:

```bash
cd frontend/tui
node --import tsx --test test/input.test.ts
npm run typecheck --silent
```

Expected: all input tests PASS and TypeScript exits 0.

- [ ] **Step 5: Commit the shared contract**

```bash
git add frontend/core/src/commands.ts frontend/core/src/index.ts \
  frontend/tui/src/input/slash.ts frontend/tui/test/input.test.ts
git commit -m "refactor(frontend): share slash command registry"
```

### Task 2: Web command planning and dispatch coverage

**Files:**
- Create: `frontend/web/src/lib/webCommands.ts`
- Create: `frontend/web/src/test/webCommands.test.ts`
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/api.ts`
- Modify: `frontend/web/src/lib/projectName.ts`
- Modify: `frontend/web/src/test/sessionRename.test.tsx`

**Interfaces:**
- Consumes: `parseCommand()`, `commandNeedsArgument()`, and `CommandId` from `frontend/core/src/commands.ts`.
- Produces: `WebCommandHandlers`, `WebCommandResult`, and `dispatchWebCommand(line, handlers)`.

- [ ] **Step 1: Write failing pure dispatcher tests**

Create `frontend/web/src/test/webCommands.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest';
import { COMMANDS, type CommandId } from '../../../core/src/commands';
import { dispatchWebCommand, type WebCommandHandlers } from '../lib/webCommands';

function handlers() {
  return Object.fromEntries(
    COMMANDS.map((command) => [command.id, vi.fn(async () => undefined)]),
  ) as WebCommandHandlers;
}

describe('web slash dispatch', () => {
  it('routes every canonical command to its stable handler id', async () => {
    for (const command of COMMANDS) {
      const table = handlers();
      const argument = command.argument === 'required' ? 'value' : '';
      const result = await dispatchWebCommand(
        `${command.name}${argument ? ` ${argument}` : ''}`,
        table,
      );
      expect(result.kind).toBe('handled');
      expect(table[command.id]).toHaveBeenCalledWith(argument);
    }
  });

  it('canonicalizes aliases', async () => {
    const table = handlers();
    await dispatchWebCommand('/rm task-1', table);
    expect(table.skip).toHaveBeenCalledWith('task-1');
  });

  it('keeps unknown and missing-argument commands local', async () => {
    const table = handlers();
    expect(await dispatchWebCommand('/staus', table)).toEqual({
      kind: 'error',
      message: 'Unknown command /staus. Did you mean /status?',
    });
    expect(await dispatchWebCommand('/rename', table)).toEqual({
      kind: 'error',
      message: 'Usage: /rename <name>',
    });
  });

  it('declines plain Manager text', async () => {
    expect(await dispatchWebCommand('continue the research', handlers())).toEqual({
      kind: 'not-command',
    });
  });
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
cd frontend/web
npx vitest run src/test/webCommands.test.ts
```

Expected: FAIL because `webCommands.ts` does not exist.

- [ ] **Step 3: Implement the pure Web dispatcher**

Create `frontend/web/src/lib/webCommands.ts`:

```ts
import {
  commandNeedsArgument,
  didYouMean,
  parseCommand,
  type CommandId,
} from '../../../core/src/commands';

export type WebCommandHandler = (rest: string) => void | Promise<void>;
export type WebCommandHandlers = Record<CommandId, WebCommandHandler>;
export type WebCommandResult =
  | { kind: 'not-command' }
  | { kind: 'handled' }
  | { kind: 'error'; message: string };

export async function dispatchWebCommand(
  line: string,
  handlers: WebCommandHandlers,
): Promise<WebCommandResult> {
  const parsed = parseCommand(line.trim());
  if (!parsed) return { kind: 'not-command' };
  if (!parsed.cmd) {
    const suggestion = didYouMean(parsed.name);
    return {
      kind: 'error',
      message: suggestion
        ? `Unknown command ${parsed.name}. Did you mean ${suggestion}?`
        : `Unknown command ${parsed.name}. Use /help for the full list.`,
    };
  }
  if (commandNeedsArgument(parsed.cmd) && !parsed.rest) {
    return {
      kind: 'error',
      message: `Usage: ${parsed.cmd.name}${parsed.cmd.arg ? ` ${parsed.cmd.arg}` : ''}`,
    };
  }
  await handlers[parsed.cmd.id](parsed.rest);
  return { kind: 'handled' };
}
```

- [ ] **Step 4: Wire all 34 handlers in App**

In `App.tsx`, remove `parseRenameCommand` and create a memoized
`WebCommandHandlers` table. Map IDs as follows:

```text
status -> open status/inspector view
roles, backend, config -> config overlay or api.setConfig
journal -> existing journal-backed inspector
backlog, item -> MissionControl/task detail
artifacts, artifact -> ResearchCanvas/artifact modal
events, find, run, clear -> activity feed state
cancel -> stopWaiting
task -> api.addTask
plan -> api.previewPlan and notice/output
nudge -> api.nudge
abort -> api.abortMission
note -> api.note
done, skip, stop -> existing backlog actions
new -> NewDaemonModal
daemons, resume, attach -> sidebar/project selection
rename -> actions.updateProject
doctor, identity, help -> matching overlays
reset -> api.resetManager
skills -> api.skills
reconnect -> close the current stream connection through a reconnect signal
quit -> show "Background work continues; close this browser tab when ready."
```

Add the missing abort client method to `api.ts`:

```ts
abortMission: (sid: string, reason: string) =>
  postJson<{ requested: boolean; item_id: string | null; message: string }>(
    P(sid, '/abort'),
    { reason },
  ),
```

Where an existing Web panel does not have a distinct overlay (`status`,
`journal`, `backlog`, `events`), select the corresponding existing workspace
view rather than creating a duplicate modal. Add only the minimal state needed
for `events` query/filter and reconnect.

At the beginning of `sendMessage`, call:

```ts
const command = await dispatchWebCommand(text, commandHandlers);
if (command.kind === 'handled') return;
if (command.kind === 'error') {
  notify('error', command.message);
  return;
}
```

Delete `parseRenameCommand()` from `projectName.ts` and its parser-only test;
keep `cacheProjectName()` and its cache test.

- [ ] **Step 5: Run dispatcher, rename, and API protocol tests**

Run:

```bash
cd frontend/web
npx vitest run src/test/webCommands.test.ts src/test/sessionRename.test.tsx \
  src/test/apiProtocol.test.ts
npm run typecheck --silent
```

Expected: all tests PASS and TypeScript exits 0.

- [ ] **Step 6: Commit Web command dispatch**

```bash
git add frontend/web/src/App.tsx frontend/web/src/api.ts \
  frontend/web/src/lib/webCommands.ts \
  frontend/web/src/lib/projectName.ts frontend/web/src/test/webCommands.test.ts \
  frontend/web/src/test/sessionRename.test.tsx
git commit -m "feat(web): dispatch the full slash command surface"
```

### Task 3: Web slash completion

**Files:**
- Create: `frontend/web/src/components/SlashCompletionMenu.tsx`
- Create: `frontend/web/src/test/slashCompletion.test.tsx`
- Modify: `frontend/web/src/components/ChatBox.tsx`
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/index.css`

**Interfaces:**
- Consumes: `slashCompletions()` and `applyCompletion()` from `frontend/core`.
- Produces: controlled composer props `value`, `onChange`, and `onSubmit`, plus accessible slash completion.

- [ ] **Step 1: Write failing completion rendering tests**

Create `frontend/web/src/test/slashCompletion.test.tsx` with static rendering and
pure key-selection assertions:

```ts
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SlashCompletionMenu } from '../components/SlashCompletionMenu';

describe('slash completion menu', () => {
  it('renders shared command names and usage', () => {
    const html = renderToStaticMarkup(
      <SlashCompletionMenu query="/sta" selected={0} onSelect={() => undefined} />,
    );
    expect(html).toContain('/status');
    expect(html).toContain('roles, queued work, journal, and health');
    expect(html).toContain('role="listbox"');
  });

  it('renders no menu after argument entry starts', () => {
    const html = renderToStaticMarkup(
      <SlashCompletionMenu query="/task write" selected={0} onSelect={() => undefined} />,
    );
    expect(html).toBe('');
  });
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
cd frontend/web
npx vitest run src/test/slashCompletion.test.tsx
```

Expected: FAIL because `SlashCompletionMenu` does not exist.

- [ ] **Step 3: Implement the bounded accessible menu**

Implement `SlashCompletionMenu` using `slashCompletions(query)`. Render at most
eight visible rows in a `role="listbox"` container; each button is
`role="option"` and shows canonical name, argument hint, and description.
Return `null` when there are no matches or the query contains whitespace.

- [ ] **Step 4: Make ChatBox controlled and keyboard complete commands**

Move draft state to `App.tsx`:

```ts
const [composerDraft, setComposerDraft] = useState('');
const [slashSelection, setSlashSelection] = useState(0);
```

Change `ChatBox` to accept `value`, `onChange`, and `onSend`. While completion is
open:

- ArrowUp/ArrowDown changes the bounded selection.
- Tab or Enter applies `applyCompletion(selectedCommand)` without submitting.
- Escape closes completion; a second Escape retains current pending-cancel behavior.
- Clicking a row applies the same completion.

After a normal submit, clear the controlled draft only when `onSend` accepts
the input. Change `onSend` to return `boolean | Promise<boolean>` so missing
argument errors can leave the draft in place.

- [ ] **Step 5: Add mobile-safe menu styles**

Add `.slash-completion-menu` styles to `index.css` with:

```css
.slash-completion-menu {
  max-height: min(22rem, 42dvh);
  overflow-y: auto;
  overscroll-behavior: contain;
}
```

Use existing glass, line, blue, gold, and text tokens; add no new brand color.

- [ ] **Step 6: Run completion and ChatBox tests**

Run:

```bash
cd frontend/web
npx vitest run src/test/slashCompletion.test.tsx src/test/webCommands.test.ts
npm run typecheck --silent
```

Expected: all tests PASS and TypeScript exits 0.

- [ ] **Step 7: Commit composer completion**

```bash
git add frontend/web/src/components/SlashCompletionMenu.tsx \
  frontend/web/src/components/ChatBox.tsx frontend/web/src/App.tsx \
  frontend/web/src/index.css frontend/web/src/test/slashCompletion.test.tsx
git commit -m "feat(web): add slash command completion"
```

### Task 4: Shared Web help and command palette

**Files:**
- Modify: `frontend/web/src/components/CommandPalette.tsx`
- Modify: `frontend/web/src/components/KeybindingHelp.tsx`
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/test/core.test.ts`
- Create: `frontend/web/src/test/commandHelp.test.tsx`

**Interfaces:**
- Consumes: `COMMANDS`, `helpGroups()`, `commandNeedsArgument()`, and the App's command handlers.
- Produces: full shared command discoverability in Ctrl/Cmd+K and `/help`.

- [ ] **Step 1: Write failing palette/help tests**

Add to `frontend/web/src/test/core.test.ts`:

```ts
import { COMMANDS } from '../../../core/src/commands';

it('builds one palette row for every shared slash command', () => {
  const rows = commandPaletteRows(COMMANDS, vi.fn(), vi.fn());
  expect(rows).toHaveLength(34);
  expect(rows.map((row) => row.hint)).toContain('/status');
  expect(rows.map((row) => row.hint)).toContain('/quit');
});
```

Create `commandHelp.test.tsx` and assert the rendered help includes `/status`,
`/task <text>`, `/skills [ls|promote <name>]`, and `/quit`.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
cd frontend/web
npx vitest run src/test/core.test.ts src/test/commandHelp.test.tsx
```

Expected: FAIL because shared command palette rows and grouped command help are
not implemented.

- [ ] **Step 3: Generate palette rows from COMMANDS**

Extract and export:

```ts
export function commandPaletteRows(
  commands: readonly SlashCommand[],
  execute: (name: string) => void,
  prefill: (text: string) => void,
): PaletteItem[] {
  return commands.map((command) => ({
    id: `command-${command.id}`,
    label: command.desc,
    hint: `${command.name}${command.arg ? ` ${command.arg}` : ''}`,
    group: command.group,
    keywords: [command.name, ...(command.aliases ?? [])].join(' '),
    run: () => commandNeedsArgument(command)
      ? prefill(`${command.name} `)
      : execute(command.name),
  }));
}
```

Combine these rows with project switching and non-command UI toggles. Remove
the hand-maintained `/doctor`, `/config`, `/identity`, and `/transcript`
duplicates.

- [ ] **Step 4: Render grouped commands in help**

Keep the keyboard bindings at the top of `KeybindingHelp`; below them render
`helpGroups()` using the same labels and descriptions as the TUI. Increase the
modal width to `max-w-2xl` and constrain its body to `max-h-[70dvh]` with
scrolling.

- [ ] **Step 5: Run the focused and full Web suite**

Run:

```bash
cd frontend/web
npx vitest run src/test/core.test.ts src/test/commandHelp.test.tsx
npm test -- --run
npm run typecheck --silent
```

Expected: focused tests and the full Web suite PASS; TypeScript exits 0.

- [ ] **Step 6: Commit discoverability**

```bash
git add frontend/web/src/components/CommandPalette.tsx \
  frontend/web/src/components/KeybindingHelp.tsx frontend/web/src/App.tsx \
  frontend/web/src/test/core.test.ts frontend/web/src/test/commandHelp.test.tsx
git commit -m "feat(web): expose shared commands in palette and help"
```

### Task 5: Release build and cross-client verification

**Files:**
- Modify generated release files produced by `scripts/build_release.py`

**Interfaces:**
- Consumes: completed shared command implementation.
- Produces: matching Web and TUI release artifacts.

- [ ] **Step 1: Run both client suites**

```bash
cd frontend/tui
npm test
npm run typecheck --silent
cd ../web
npm test -- --run
npm run typecheck --silent
```

Expected: all tests PASS.

- [ ] **Step 2: Build the fenced release**

```bash
cd /home/argustest/argustest2/argus-skill-latest
python scripts/build_release.py
```

Expected: `release ready: <release-id>` and exit 0.

- [ ] **Step 3: Verify no duplicate registry remains**

```bash
rg "const SLASH_COMMANDS|const COMMANDS" frontend/tui frontend/web frontend/core
python scripts/check_release_artifacts.py
```

Expected: one command array definition in `frontend/core/src/commands.ts`; the
artifact checker reports matching release IDs.

- [ ] **Step 4: Commit release artifacts**

```bash
git add argus_skill/release_manifest.json argus_skill/_frontend \
  frontend/core/src/release.generated.ts frontend/tui/bundle frontend/web/dist
git commit -m "build: publish unified command release"
```
