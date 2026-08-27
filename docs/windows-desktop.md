# Windows Desktop

Argus includes an Electron host for Windows x64. The desktop application does
not fork the Argus product or maintain a separate Web UI: it starts the same
Python runtime, serves the checked-in Web cockpit on loopback, and displays that
cockpit in a hardened Electron window.

The desktop package version follows the repository version. Prebuilt installers
and unpacked applications are build artifacts and are intentionally not stored
in Git.

## Scope

The Desktop integration adds:

- an Electron main process, preload bridge, launcher, and first-run settings UI;
- a PyInstaller build of the existing `argus_skill` runtime;
- strict ownership checks between Electron and its local Python backend;
- automatic recovery with a bounded restart circuit;
- completion receipts that link chat, the right-side result view, and native
  Windows notifications when the app is backgrounded;
- native Windows caption controls kept outside the cockpit hit area;
- supported Agent CLI discovery and explicit binary selection;
- redacted diagnostic export;
- NSIS installer and portable-package definitions, including the original
  tray/window icon as a packaged runtime resource.

It does **not** replace Manager, Planner, Engineer, Reviewer, WebAPI, Workbench,
or Vertical behavior. Those remain owned by the main Argus runtime. It also does
not expand the Windows portability claims of the underlying runtime; see the
main README for the currently supported Windows surface. The frozen
`resources/argus-backend/_internal` tree is a release payload, not a Git source
checkout. Repairs are built and reviewed in a separate source repository and
delivered through the reviewed deployment boundary as a new Desktop release.

## End-user installation

When a GitHub Release includes `Argus-<version>-setup.exe`, download that asset
and run it. The installer contains the frozen Argus backend; end users do not
install Python, Node.js, or a virtual environment for the Desktop application.
If the Releases page has no matching installer asset, use the Windows pip
instructions in the main README. Do not treat contributor build commands or CI
artifacts as an available end-user release.

The first-run screen selects an installed Agent CLI and starts the bundled
backend. Installation is usable only after the backend reaches the ready screen.
During an upgrade, NSIS terminates old `Argus.exe` and frozen-backend process
trees before its running-app check; it does not rely on WM_CLOSE because normal
close intentionally hides Argus to the tray. A previously launched backend can
be a separate process;
the first launch of the new Desktop release safely replaces a proven prior
backend after installation. If startup fails, use **Export sanitized
diagnostics** from the error screen; the report includes the failed stage and
backend log without credentials.

`Argus-<version>-portable.exe` is the no-install alternative. The source
development instructions later in this document are for contributors building
the package, not for end users.

## Architecture

```text
Windows native non-client frame
  └─ Minimize / Maximize-or-Restore / Close

Argus.exe (Electron)
  ├─ local launcher and settings renderer
  ├─ hardened BrowserWindow / WebContentsView
  └─ supervised resources/argus-backend/argus-backend.exe
       └─ existing Argus WebAPI + checked-in Web cockpit
```

The native caption buttons are deliberately outside renderer coordinates. The
right-side file picker, download, expand, and collapse controls therefore never
share a hit area with Windows window controls.

### Close, hide, and explicit stop

On Windows, clicking the native **Close** button hides the Desktop shell to the
system tray; it does not stop the authenticated backend, project daemon, or an
in-progress Argus mission. Launching Argus again or clicking the tray icon
restores the same window. The File menu distinguishes:

- **关闭窗口并在后台继续** — hide the shell and keep Argus running;
- **退出桌面界面（后台继续）** — exit Electron while leaving the owned backend
  available for a later Desktop instance to authenticate and adopt;
- **停止本地后端并退出** — the explicit destructive shutdown path; it stops only
  the backend proven to be owned by this Desktop instance before exiting.

This is intentionally different from treating a window close as a request to
end 7×24 work.

To verify the behavior, start a mission, click **Close**, then either click the
tray icon or launch Argus again. The same backend and project state should be
reused. Use **停止本地后端并退出** only when you intentionally want to stop
that Desktop-owned local service; do not use Task Manager against an arbitrary
PID as a normal shutdown path.

In a packaged application, `argus-backend.exe` is a one-folder PyInstaller
bundle. Its entry point also implements the small Python invocation subset used
by Argus-owned tools:

- `argus-backend.exe -m argus_skill...`
- `argus-backend.exe -c "..."`
- `argus-backend.exe path/to/script.py ...`

Non-Argus `-m` modules are rejected. The build gate imports every registered
Vertical and Domain so a package cannot look healthy while dynamic providers
are missing.

## Runtime ownership and safety

On first run, the Electron supervisor mints a random Web token and persists it
in per-user Desktop settings. It writes a per-user ownership record for every
backend it launches. During ordinary startup, a backend is accepted only when
an authenticated `/api/meta` response agrees with that record on all relevant
fields:

- PID and process start identity;
- executable path;
- release-manifest source digest;
- loopback host and port;
- SHA-256 of the Web token.

An in-place Desktop update has one narrow compatibility path. When the new
Desktop can authenticate an older listener with that same token and the listener
reports the exact bundled `argus-backend.exe` path, a real PID, start identity,
and prior release digest, it may terminate that listener tree and start the new
bundled backend. This handles a running previous Desktop release without
adopting an arbitrary service. A non-local, unauthenticated, path-mismatched,
or malformed listener remains fail-closed.

The Desktop application never adopts, restarts, or terminates a process whose
ownership or bounded legacy-upgrade identity cannot be proven. It also:

- binds the managed service to `127.0.0.1` by default;
- uses Electron context isolation and renderer sandboxing;
- disables Node integration in renderers;
- denies renderer permission requests;
- sends non-local links to the system browser;
- keeps Minimize, Maximize/Restore, and Close in Windows' native non-client
  frame rather than overlaying right-side file controls;
- redacts tokens, authorization headers, and credential-bearing URLs from logs
  and exported diagnostics;
- bounds automatic restart attempts and surfaces crash-loop failures.

## Completion delivery

A successful mission produces one durable **delivery receipt**. Its stable
`delivery_id` binds the lifecycle completion event, Manager chat message,
transcript replay, Mission View, and the right-side result surface. The receipt
contains the verified summary, review status, and up to six safe
workspace-relative targets.

Targets are selected only from already authoritative sources, in order:

1. Reviewer-named evidence files from the terminal verdict;
2. primary deliverables declared by the active Vertical contract;
3. the current Manager Live View as a presentation fallback.

Argus does not scan a workspace or infer arbitrary files as a deliverable.
Every target is revalidated by the protected artifact API before it can be
opened or downloaded. When a receipt arrives, the cockpit shows an explicit
completion card with **Open result**, expands the right-side panel, and selects
the primary target. Receipt IDs deduplicate WebSocket replay and page reloads.

If the Desktop window is minimized or unfocused, Electron displays a native
Windows notification. Clicking it restores the window and opens the delivery
target when one exists. A completion with no renderable artifact still presents
its verified summary and opens the Mission view rather than inventing a file.

## Requirements

For source development and packaging:

- Windows 10 or 11, x64;
- Python 3.11+;
- Node.js 22.12+;
- PowerShell;
- one supported Agent CLI installed and authenticated if agent work will run.

The first-run UI supports Codex CLI, Claude Code, GitHub Copilot CLI, Pi,
OpenCode, Grok Build, Qoder CLI, and DeepSeek Harness. Auto-detection is only a
convenience; the operator can select the executable explicitly.

### Role-session reuse

Desktop does not need a machine-specific Pi setting to reuse role context. The
shared Argus runtime defaults to `ARGUS_SKILL_ROLE_SESSION_POLICY=auto`: Pi,
Codex, Claude/Qoder, Copilot, OpenCode, and Grok use bounded role-isolated
rolling sessions when the backend supports resume; fresh-only backends such as
DeepSeek Harness remain fresh. Planner, Engineer, and Reviewer each retain a
separate durable capsule and never share a provider thread across roles or
unrelated missions. Rotation occurs at the configured turn/token limit or when
identity-relevant context changes. See
[Role sessions and on-demand Skills](ROLE_SESSIONS_AND_SKILLS.md) for the full
contract and rollback controls.

## Contributor-only development setup

This section builds the frozen backend and installer and therefore uses an
isolated build environment. End users do not run these commands and do not
create a venv.

From the repository root in PowerShell:

```powershell
uv venv --python 3.12 --seed .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e . pytest ruff "pyinstaller>=6.11,<7"

npm --prefix frontend/web ci
npm --prefix desktop ci
```

`py -3.11 -m venv .venv` is also valid when the Python Launcher is installed.
The `uv` form above works on machines (including this tested setup) where
`py.exe` is absent. Always run Argus through this repository's `.venv`; a bare
global Python can appear to start the WebAPI from the source directory and then
fail to import `argus_skill` after an executor changes into the project
workdir.

For direct CLI use in a legacy CP936/GBK PowerShell, the launcher now switches
its streams to UTF-8. For older builds, set these two variables before launch:

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
```

Run the Desktop host against the source runtime:

```powershell
$env:ARGUS_DESKTOP_DEV = "1"
$env:ARGUS_DESKTOP_REPO_ROOT = (Get-Location).Path
$env:ARGUS_SKILL_BIN = "$PWD\.venv\Scripts\python.exe"
npm --prefix desktop run dev
```

The development host reads the repository release manifest and starts
`python -m argus_skill --web` from the selected repository root.

Only one managed Argus API should own a host/port. Stop a manually started
`argus --web` on port 8799 before starting Desktop: Desktop can replace its own
proven prior bundled backend during an update, but correctly refuses a manually
started, non-local, unauthenticated, or path-mismatched listener. Also keep
separate checkouts in separate virtual environments: the public and private
repositories share a default state root and port but can carry different release
identities.

### What exits when the terminal closes

`argus`, the local WebAPI on `127.0.0.1:8799`, and each session's background
executor are separate processes. The default interactive exit policy is
`detach`: Ctrl-D (or Ctrl-C twice in the live view) closes only the terminal UI,
so Web and an in-progress executor remain available. A later plain `argus` in
the same folder now detects that live session and reattaches instead of trying
to create a competing executor for the leased workspace.

Choose cleanup explicitly when that is what you want:

```powershell
argus --exit-policy stop-api  # stop only an API spawned and still owned by this invocation
argus --exit-policy stop-all  # gracefully stop the current executor, then that owned API
$env:ARGUS_TUI_EXIT_POLICY = "stop-all"  # optional persistent shell policy
```

`stop-api` is fail-closed: it checks the loopback endpoint, ownership record,
runtime PID/start identity, and both Windows launcher/listener PIDs before
signalling anything. It never stops a pre-existing, remote, unowned, or
PID-reused service. `stop-all` uses the daemon's graceful stop endpoint; it does
not recommend or issue `Stop-Process` against a raw PID. Workspace lease errors
name the safe session command, for example
`argus --daemon-stop --resume s-12345678`.

## Troubleshooting startup

- **Local backend startup timed out**: inspect the Desktop log and authenticated
  `/api/meta`. Older source builds confused the `.venv` launcher PID with the
  actual Python listener PID on Windows; current ownership records both.
- **`端口上的 Argus 版本为 ...，当前桌面版为 ...` after an installer update**:
  close the old Desktop window, install the new package, then start it once.
  Current builds first use the full prior ownership record (PID, start
  identity, executable, token hash, and prior manifest digest). For older
  Desktop releases that lack a compatible record, they also accept an
  authenticated loopback `/api/meta` response only when it reports the exact
  bundled `argus-backend.exe` path plus a real PID, start identity, and release
  digest. In either case Desktop terminates that proven old listener tree and
  starts the bundled release. A non-local, unauthenticated, path-mismatched, or
  malformed listener remains fail-closed; close that unrelated process normally
  or choose another port instead of killing a raw PID.
- **Connecting to Argus** after the project list appears: the API handshake and
  project index already succeeded; the selected snapshot is still pending.
  Current builds bound that read and no longer let Manager prewarm hold it.
- **`snapshot refresh failed · fetch failed`**: this is a new REST request that
  could not reach or finish against the shared local WebAPI, not evidence that
  CLI and Web maintain separate state. Current TUI output includes the socket
  cause and retries bounded requests.
- **`background executor failed to start (rc=...)`**: inspect the diagnostic
  appended to the UI and the newest `daemons/boot-*.log`. Current builds retain
  helper stderr, validate the workdir/interpreter, and preserve the Windows
  child exit code instead of reducing every failure to a bare integer.
- **Repeated `handoffs/<id>/CHECKPOINT.md` `FileNotFoundError` after a successful
  Agent turn**: the checkpoint is optional role-session metadata, not mission
  authority. Current builds initialize an empty placeholder and independently
  tolerate missing/delete-raced/unreadable metadata; capsule persistence failure
  is a warning and cannot overwrite the provider/reviewer outcome. An uncaught
  runtime exception also opens a durable circuit so rephrased retries do not run
  again under the same release identity.
- **A packaged backend needs a framework update**: `_internal` is a release
  payload, not a source checkout. Install a newly built release rather than
  editing frozen files in place.

## Verification

Run the fast source checks:

```powershell
.\.venv\Scripts\python.exe -m ruff check desktop tests/desktop
.\.venv\Scripts\python.exe -m pytest -q tests/desktop
npm --prefix desktop run typecheck
npm --prefix desktop run test:identity
npm --prefix desktop run build
npm --prefix desktop audit
```

Build and verify the frozen backend:

```powershell
.\desktop\scripts\build-backend.ps1 -SkipInstall
```

The script verifies provider collection and the frozen `-m`, `-c`, and script
entry points before reporting success.

For an unsigned CI-style package-layout check:

```powershell
Set-Location desktop
npx electron-builder --win --dir --publish never -c.win.signExecutable=false
```

This validates the Electron application and bundled backend without signing the
executables while still applying the checked-in Windows icon and version
resources.

## Building distributable packages

Activate the intended Python environment, then run:

```powershell
npm --prefix desktop run dist
```

This command:

1. validates and builds the existing Web cockpit;
2. builds and verifies the frozen Python backend;
3. builds Electron main, preload, and launcher bundles;
4. applies the checked-in Windows icon and version resources;
5. produces NSIS and portable artifacts under `desktop/release/`.

For unsigned local or CI builds, disable only signing with
`--config.win.signExecutable=false`. Do **not** use
`signAndEditExecutable=false`: that also skips the original icon and Windows
version-resource edits.

`desktop/build/`, `desktop/out/`, and `desktop/release/` are reproducible local
outputs and are ignored by Git.

The repository does not contain signing credentials. Release owners should
provide the normal electron-builder signing configuration in the release
environment. On Windows machines where electron-builder cannot unpack its code
signing helper, enable Windows Developer Mode or run the release build in an
environment allowed to create symbolic links.

## Local data and diagnostics

Desktop settings, ownership metadata, and Desktop logs live under Electron's
per-user `userData` directory. The ownership record is
`runtime/backend.json`; it contains only process identity and a token hash,
never the raw Web token. Argus project state continues to use
`ARGUS_SKILL_HOME`, defaulting to the normal `~/.argus-skill` location.

The application menu and settings screen can:

- restart the owned backend;
- open logs and local data;
- select the Agent CLI and executable;
- change the loopback port and appearance;
- export a redacted diagnostic ZIP.

When a verified mission completes, Desktop shows the same durable delivery
receipt in chat and in the right-side result surface. If the window is
minimized or unfocused, it also sends a native Windows notification; clicking
it restores Argus and opens the delivery target when one exists.

Diagnostic export is intended for troubleshooting, but operators should still
review any archive before sharing it.
