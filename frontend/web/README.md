# argus web cockpit

A React web frontend for the argus-skill autonomous-research daemon — a **client of
the argus-skill webapi** (`argus_skill/webapi/server.py`). It shares the same
durable Mission View and event semantics as the terminal cockpit
(`frontend/tui/`) while using a denser Mission Control layout for DAGs,
metrics, replay, artifacts, and Git changes. State stays drift-free because
event identity, mission projection, cost accounting, Guardian alerts, and
project ranking live in `frontend/core/`.

## What it shows

A compact graphite operations console:

- **Left rail** — plain wordmark + searchable project switcher (name,
  session ID, objective, and live/stopped state), with live daemon dots + uptime.
  Project changes are browser-history entries, so Back/Forward restores the
  previous cockpit; stale `?project=` links recover to a valid project safely.
- **Top bar** — daemon health, backend·model·effort, honest spend gauge, start/stop.
- **Primary pane** — the live event feed (WS stream), with agent-reasoning hidden by default
  (⌘O to toggle), shared watch/milestone/message filters, plain-text search, and
  smart stick-to-live scrolling.
- **Right rail** — a compact four-role ledger, the backlog table
  (full task drill-down plus done/skip/stop via flock-safe daemon paths), and the
  research journal.
- **Composer** — one natural-language Manager front door; Enter sends and the
  Manager decides whether to reply or dispatch work. In-flight Manager streams
  are project-scoped and cancelled when the operator switches daemons. While
  waiting, **Stop waiting**, Esc, or the ⌘K action detaches the reply without
  falsely claiming that already-started server-side work was undone.
- **Deliberate daemon creation** — every `+ New` entry opens a confirmation
  form. A blank objective creates an idle workspace; an objective creates the
  workspace and starts its campaign. The new daemon is selected automatically.
- **Latest result** — reviewer-approved evidence files open in an authenticated
  text/image/PDF preview and can be downloaded without exposing arbitrary files.
- **⌘K command palette** with CLI slash-parity, project ID/objective search,
  keyboard scroll-following, **?** keybinding help, and a
  read-only **kiosk mode** (⌘. / `?kiosk=1`) for an investor/demo wall.

## Honesty notes (by design)

- **Cost uses settled project history** from the canonical event journal; the
  live stream is only a fallback while that server total is unavailable.
- **Config is read-only** — there is no PATCH endpoint (the backend is deliberately
  unchanged); config is shown with "set via env / restart to apply".
- **Artifact access is allowlisted** to the latest reviewer's evidence paths and
  confined to the project workspace. HTML/SVG never execute in the preview.

## Run it

**Production (one port, API + UI):**

```bash
cd frontend/web && npm install && npm run build   # emits dist/
pip install -e '.[web]'                            # from the repo root
argus --web                                        # starts API/UI and opens the browser
```

Use `argus --web --no-open` on SSH/headless hosts to print the forwarding URL.
The lower-level `argus-skill --web` command still serves the built `dist/` at
`/` and the API under `/api` on the same origin.

**Dev (hot reload):** run the API on 8799 and Vite on 5173 (which proxies `/api`):

```bash
argus-skill --web            # terminal 1: the API
cd frontend/web && npm run dev   # terminal 2: http://127.0.0.1:5173
```

## Create a daemon

Use `+ New` in the project rail or **New daemon** in the command palette. The
name is optional. Leave the objective blank when you only want a clean idle
workspace, or enter an objective to arm and start a campaign immediately.
Press Ctrl/⌘+Enter to confirm. Kiosk mode intentionally hides creation controls.

The equivalent Ink cockpit command is `/new [objective]`.

## Verify

```bash
npm run typecheck   # tsc --noEmit
npm run build       # typecheck + production bundle
npm run test        # vitest — cost/event parity with the terminal client
```
