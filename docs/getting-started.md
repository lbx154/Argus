# Getting started with Argus

This guide takes you from a machine with Node.js to a first request carried through by
Argus's team, in the web page. For how the core works underneath, see
[the TypeScript core](typescript-core.md).

## What you are installing

Argus is one npm package with no runtime dependencies. The `argus` command starts a web
server on your machine and runs the work of every project inside that same process.
Argus does not call a model API itself: each turn a role takes (Manager, Planner,
Engineer, Reviewer) is a call to a coding-agent CLI you already use, with that CLI's
login. Argus hands the work between the roles, delivers messages, records what happened
and keeps the permission boundary; the backend runs the turn.

## Prerequisites

| Requirement | Why |
|---|---|
| Node.js 22.12+ | the package's `engines` field; Argus is a Node program |
| One backend CLI, installed and signed in | every role's turn is a call to it; there is no Argus account |
| bubblewrap on Linux | each call runs in a sandbox that sees the project directory, system software and the CLI's own login. The host must let unprivileged users create user namespaces: `bwrap --ro-bind / / true` has to succeed |

Without a working `bwrap` (and always on macOS and Windows), Argus uses the CLI's own
sandbox or permission settings where it has them: Codex's `workspace-write` (on Windows
its sandbox that needs no administrator), Claude Code's `acceptEdits`.

| Backend | `--backend` | Install | Sign in |
|---|---|---|---|
| OpenAI Codex CLI | `codex` (default) | `npm install -g @openai/codex@latest` | `codex login` |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | run `claude`, then `/login` |
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` | `copilot login` |
| Pi | `pi` | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` | run `pi`, then `/login` |

Under bubblewrap only the CLI's login file is brought into the sandbox (for Copilot,
`~/.copilot/config.json`), so a login kept only in the system keychain does not reach it.

## Install

The package is not on npm yet. Once it is, this is the whole install (the name becomes
`argus` when that name is available):

```bash
npm i -g @lbx154/argus
```

Until then, build the same package from source and install it:

```bash
git clone https://github.com/microsoft/ArgusAgent.git
cd ArgusAgent
npm ci
npm --prefix frontend/web ci
npm pack -w packages/core          # builds the core and its web page, writes lbx154-argus-<version>.tgz
npm i -g ./lbx154-argus-0.1.8.tgz
argus --help
```

`npm pack` runs the build first: the TypeScript core, bundled into one file, and the web
page from `frontend/web`. For the development preview, clone
`https://github.com/lbx154/Argus.git` instead. A desktop version is on the
[Releases](https://github.com/lbx154/Argus/releases) page.

## Start

```bash
argus
```

It prints `Argus is at http://127.0.0.1:8799/`; open that address. `argus` has no
subcommands, and it does not open a browser by itself.

| Option | |
|---|---|
| `--port N` | another port (default `8799`; `0` picks a free one). A port already in use is an error |
| `--backend codex\|claude\|copilot\|pi` | the backend new projects use (default `codex`) |
| `--model M` | the model new projects use, in the backend's own name for it; without it, the CLI's default |
| `--host H` | serve on another address, for example `0.0.0.0` |
| `--web DIR` | serve a web page built elsewhere instead of the bundled one |
| `ARGUS_WEB_TOKEN` | the access token every project request must carry, kept across restarts; unset, `argus` makes one on its first start and keeps it in the state root (`web-token`). The printed address includes it |
| `ARGUS_HOME` | the state directory (default `~/.argus`) |

One `argus` process owns a state directory. A second one on the same directory stops with
`<directory> is in use by process <pid>`; give it its own `ARGUS_HOME` if you want two.

**On a remote server**, run `argus` there and forward the port from your computer:

```bash
ssh -L 8799:127.0.0.1:8799 user@server
```

then open the address it prints, `http://127.0.0.1:8799/?token=…`. To serve it on the network
instead, with a token that stays the same across restarts:

```bash
export ARGUS_WEB_TOKEN="$(node -e "console.log(require('crypto').randomBytes(24).toString('base64url'))")"
argus --host 0.0.0.0
```

## Your first project

1. **Create a project** in the page. It works in its own folder under
   `~/.argus/workspaces/<id>/` unless you give it an existing directory, which you can
   also do later. Its settings hold its name, backend, model and reasoning effort; a new
   directory or backend is refused while work is under way.
2. **Write what you want** to the Manager, in your own words. Files you attach are
   uploaded into the project directory's `.argus/attachments/` and named in your message,
   so the Manager can read them.
3. **The Manager decides how to carry it.** It answers questions and does small work
   itself. Work one person can carry through goes whole to an Engineer, as a letter that
   is the task's whole brief; when the Engineer is done, the Reviewer opens the result and
   judges it as you would, sending back to the Engineer what you would send back. Work too
   large for one task goes to the Planner, which cuts it into a few large tasks, runs the
   independent ones side by side, an Engineer each, and decides what comes next when they
   have ended. When the work is accepted, the Manager reports the result to you and where
   to open it.
4. **Keep talking.** You can write to the Manager at any time, also while the team works;
   it answers in its next turn and the team carries on. When a role needs something only
   you can decide, it writes to you, and its task waits for your answer while the rest of
   the work goes on. Each open question is a card of its own.

For scale: in the core's first real run, Codex took one sentence to a reviewed delivery in
3 calls, 5.7 minutes and $0.42.

## Following the work

- **The conversation** with the Manager, and every letter the roles wrote, in order.
- **The progress map**: a card per task, its brief the letter that opened it, and a step
  for each letter written about it, by whom to whom. Opening a task lists its calls, each
  with the letter it ended with and the path of its raw output, and the error of its last
  call if it failed.
- **Files**: the project directory, read-only, with what `git` says about it when it is a
  repository.
- **Results**: the files created or changed since the project began. An HTML result is
  previewed as a page; results download one by one.
- **Knowledge & skills**: the bundled skills and those in `~/.argus/skills/`, newest
  changes first, so a skill a role writes shows up while the work goes on.

Stopping a project interrupts every call under way on it, and it stays stopped, whatever
you say to the Manager, until you start it again; each role then goes on in its own session
where it was. The work runs inside the `argus` process, so stopping `argus` stops it too;
when `argus` starts again, the work it cut off goes on by itself, and a project you stopped
stays stopped.

## Skills and plugins

A skill is a folder whose `SKILL.md` says in its front matter (`name`, `description`), as
Codex and Claude Code write a skill, what kind of work it is for.
Argus's own are in the package (`research`, `rl`, `software`, `argus-maintenance`, `learning`)
and are replaced on upgrade; those in `~/.argus/skills/<name>/` are the roles' and yours,
listed beside Argus's, a name shared or not. Every role's prompt lists the skills; the Manager
and the Engineer may write new ones and change the team's, and the Manager writes down there
what you teach it when you correct how the work is done.
Argus does not reflect after a task, score, curate or record which skill a project used:
what to write down is the roles' decision.

A plugin is a skill folder that also holds `plugin.json`:

```json
{ "name": "...", "description": "...", "version": "...", "url": "http://..." }
```

Whoever installs a plugin starts it; the page lists it and opens its `url`.

## The desktop app

The desktop app is a Tauri host that ships Argus (the TypeScript core,
`packages/core`) and runs it with the Node.js already on your machine; it opens
the web cockpit Argus serves. It is a packaged preview channel, separate from
source updates.

- **Download:** the Releases page of
  [lbx154/Argus](https://github.com/lbx154/Argus/releases). Windows ships as
  an NSIS installer named `Argus-<version>-setup.exe`. The release workflow can
  also build macOS (`.dmg`, arm64 and x86_64) and Linux (`.AppImage`, `.deb`)
  packages, but the workflow file notes that v0.1.7 and v0.1.8 were published
  for Windows only, so check the assets of the release you pick.
  `microsoft/ArgusAgent` Releases is a separate channel; do not mix installers
  or updates between the two.
- **What it needs:** Node.js 22 or newer. The agent CLIs Argus drives (Codex,
  Claude Code, Copilot CLI, Pi) are npm packages, so a machine with one of them
  has Node. Without it, or with an older one, the app says so and links to
  <https://nodejs.org/>; it does not install Node itself. No Python is needed.
- **First launch:** a setup wizard asks you to pick an installed, logged-in
  agent CLI (Codex, Claude Code, Copilot CLI or Pi) and confirm it; the local
  backend does not start until you do. The choice is remembered; **File →
  Settings** changes the CLI, executable or port later.
- **After that** the app is the web cockpit: create a project, type the
  objective, watch the feed.

To build it from source instead (Node 22.12+, Rust stable, plus MSVC Build
Tools on Windows or Xcode command-line tools on macOS):

```bash
npm ci
npm --prefix frontend/web ci
npm --prefix desktop-tauri ci
npm --prefix desktop-tauri run build:unsigned
```

The unsigned bundle lands under `desktop-tauri/src-tauri/target/release/bundle/`.
[docs/windows-desktop.md](windows-desktop.md) covers how the app starts Argus,
signing and the update channel.


## Where things live

| Path | Contents |
|---|---|
| `~/.argus/` | the state directory (`ARGUS_HOME` overrides it) |
| `~/.argus/owner.json` | the pid of the `argus` process that owns the directory |
| `~/.argus/projects/<id>/project.json` | a project's current state: directory, request, name, backend and model, tasks, role sessions, the open questions |
| `~/.argus/projects/<id>/events.jsonl` | what happened, in order: messages, letters, calls and the steps each took (what a role said, ran and changed as it worked), task changes |
| `~/.argus/projects/<id>/usage.jsonl` | one record per backend call: tokens and cost |
| `~/.argus/projects/<id>/calls/` | each call's prompt as sent, and its raw output from the backend |
| `~/.argus/skills/` | the roles' and your skills and plugins |
| `~/.argus/workspaces/<id>/` | the folder of a project that was not given a directory |
| `~/.argus/sandbox/` | under bubblewrap, a private home per project directory |
| `~/.argus/trash/` | deleted projects' records, which the page can restore; their directories are left where they were |
| your project directory | the work itself; attachments under `.argus/attachments/` |

## When something goes wrong

- **`<backend> is not installed (not found on PATH)`**: install the CLI with the command
  the message gives, sign in, and start the project again.
- **A call failed**: Argus records the failure as it happened and the work stops; it does
  not retry. The task shows the error of its last call and the path of the raw output
  under `calls/`. Fix the cause (sign-in, quota, network), then start the project again.
- **Spend**: every call's tokens and cost are in the project's `usage.jsonl`. Argus sets no
  spending limit of its own; your provider's limits apply.

## Upgrade and uninstall

Stop `argus` first; work under way is interrupted, and you start it again from the page.

```bash
npm i -g @lbx154/argus@latest      # from source: git pull, then the source steps above again
npm rm -g @lbx154/argus
```

Uninstalling leaves `~/.argus`, which holds your projects' records, the skills, and the
folders of projects that were not given a directory. Delete it only if you want all of
that gone.

## Projects started with the Python runtime

The earlier Python runtime is still in the repository only so that projects started with
it can finish; it will be removed after that and is not for new installations. Its state
is in `~/.argus-skill`, separate from `~/.argus`, and its projects keep running with the
installation that started them. Both provide a command called `argus`: on a machine with
both, check which one runs (`type -a argus`, or `Get-Command argus -All` in PowerShell).
