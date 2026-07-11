# argus Ink cockpit

The Ink/React terminal frontend for `argus-skill`. It consumes the same Web API
and shared frontend core as the browser cockpit, including event identity,
project ranking, mission state, cost accounting, and Guardian alerts.

## Run it

```bash
cd frontend/tui
npm install
npm run dev
```

The interactive launcher starts `argus-skill --web` when the local API is not
already available. Use `--host`, `--port`, `--project`, or `--token` to connect
to a different API; `npm run dev -- --help` lists every option.

Open the browser console with:

```bash
argus --web
argus --web --no-open   # SSH/headless: print the URL only
```

A plain interactive launch always creates and enters a fresh idle session. It
does not silently attach to the most recent daemon. Use `--project <id>` for an
explicit launch-time resume, or `/resume` after startup to open the session
picker. The idle session starts its executor lazily when the first real task is
dispatched.

An invalid interactive `--project` ID safely attaches to the best available
project and shows a notice. Headless `--once --project <id>` remains strict and
exits non-zero instead of silently changing its target.

## Everyday controls

- Type natural language and press Enter to talk to the Manager front door.
- When the Manager dispatches real work, the background executor starts
  automatically; there are no manual start/stop campaign commands in the TUI.
- Multiline clipboard paste is supported. The input automatically wraps up to
  four rows around the cursor and shows a character count only when clipped,
  while retaining the complete text sent on Enter.
- One Manager turn runs at a time. Switching daemons cancels the old stream, so
  late phases or replies can never appear in the newly selected project. Press
  Esc or use `/cancel` (`/abort`) to stop waiting without switching; the notice
  makes clear that server-side work may still finish in the project timeline.
- Type `/help` for the full keyboard and command reference.
- The live activity line shows one truthful current action and updates it in
  place instead of appending protocol noise. Press `Ctrl+O` for the recent
  observable-action history (phase, role, model, duration). Raw prompts, token
  deltas, tool output, and chain-of-thought are never shown there.
- Use `/resume` or `/resume list` to open the session picker; `/resume <id>`
  switches directly by ID, prefix, label, or search match.
- Use `/daemons [query]` and the arrow keys to find and switch projects by
  name, session ID, objective, or live/stopped state. Inside the picker, `/`
  starts a new search and `n` opens the daemon creation form.
- Use `/events [all|watch|milestones|messages] [query]` to filter the feed.
- Use `/artifacts`, `/backlog`, and `/item <id>` for result and task details.

## Create a daemon

```text
/new
/new Write the AAAI paper
```

`/new` opens a confirmation form; it never creates immediately. Add an optional
name, use Tab or ↑/↓ to switch fields, then press Enter to confirm. A blank
objective creates an idle daemon workspace. Supplying an objective pre-fills
the form and starts its campaign after confirmation. Esc or Ctrl-C cancels,
and successful creation switches the cockpit to the new daemon automatically.

## Verify

```bash
npm test
npm run build
```
