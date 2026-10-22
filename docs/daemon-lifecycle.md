# Unattended daemon operation

The server owns work. A browser, TUI or SSH session is a client of that server;
disconnecting it must not stop an already accepted background task.

```mermaid
flowchart LR
  Client[Web / TUI / SSH client] --> API[Server API]
  API --> Queue[Persistent project backlog]
  Queue --> Daemon[Independent daemon]
  Daemon --> Run[Task execution owner]
  Run --> Guard[Process guard]
  Guard --> Pi[Pi and temporary tool processes]
```

Argus's production Python daemon already implements this separation. On POSIX,
the background launcher double-forks, creates a new session, redirects standard
streams to logs and ignores SIGHUP. The TUI defaults to `--exit-policy detach`.
Closing it does not issue a stop command to the current project daemon.
`stop-api` and `stop-all` are explicit alternate exit policies.

For an existing project, run the commands from its workspace:

```sh
argus --daemon --resume PROJECT_ID
argus --status --resume PROJECT_ID
```

`--daemon` drains the backlog in the background. `--continuous` enables generation
of further work; `--resume-continuous` restores an already armed, persisted
campaign. A background worker does not require a connected human, but still
honors explicit pauses and operator decisions.

A long job runs inside the Engineer's own turn: the Engineer starts it, waits
for it with blocking commands, reads the result, and only then hands off. The
Reviewer judges the finished result, never a launch.

## Linux service supervision

Use a service manager for unattended restart and boot startup. A systemd user
unit for one existing project can look like this; replace the absolute paths and
project ID with the actual installation:

```ini
[Unit]
Description=Argus project worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/srv/workspaces/my-project
ExecStart=/srv/Argus/.venv/bin/argus --daemon-fg --resume PROJECT_ID --life-dir /srv/argus-state --resume-continuous
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=120
StandardInput=null
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

Here `--life-dir` is the **global** Argus state root, not the project's directory.
Keep provider credentials/configuration available to the service account; a user
service does not source an interactive `.bashrc`. An `EnvironmentFile=` can hold
the deployment's provider settings outside the repository.

Install the unit under `~/.config/systemd/user/argus-project.service`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now argus-project.service
journalctl --user -u argus-project.service -f
```

For a user service to survive the account's last logout and start at boot, the
server administrator should enable lingering for that account:

```sh
loginctl enable-linger SERVICE_USER
```

A system service configured with `User=SERVICE_USER` is another deployment option.

### Weekly metrics report

`python -m argus.tools.weekly_report` sums the last full ISO week (UTC) across
every project under the Argus home: tasks started, passed, failed and parked,
success rate, first-round pass rate, reviews per passed task, spend, cost per
passed task, and the change from the week before. Pass project state directories
or another home as arguments, `--week 2026-W42` for another week, and `--json`
for JSON. The daemon does not schedule it (each daemon serves one project); a
timer does. With `--out` it also writes `<week>.md` and `<week>.json`:

```ini
# ~/.config/systemd/user/argus-weekly-report.service
[Unit]
Description=Argus weekly metrics report

[Service]
Type=oneshot
ExecStart=/srv/Argus/.venv/bin/python -m argus.tools.weekly_report /srv/argus-state --out /srv/argus-state/reports/weekly

# ~/.config/systemd/user/argus-weekly-report.timer
[Unit]
Description=Write the Argus weekly metrics report every Monday

[Timer]
OnCalendar=Mon *-*-* 01:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now argus-weekly-report.timer
```

Without systemd, a crontab line does the same:
`0 1 * * 1 /srv/Argus/.venv/bin/python -m argus.tools.weekly_report /srv/argus-state --out /srv/argus-state/reports/weekly`
(cron uses the machine's time zone; the report's weeks are UTC either way).
The web/API server can have its own service. Losing the web process or an SSH
tunnel makes that client connection unavailable; the independent project daemon
continues working. These are deployment examples, not services installed by the
TypeScript migration.

## Execution ownership during TypeScript migration

The production daemon/backlog are still Python. The TypeScript `PiBackend` can
run under an OS process guard. That guard watches a **private pipe from the
execution owner**. It never watches browser connections, the terminal's stdin or
an SSH session. A detached Node owner keeps that pipe open after its launcher
has exited.

A service integrating this primitive must retain the execution iterator as part
of its task state. HTTP handlers attach/detach observers by task ID; they must
not pass a connection-disconnect signal as the task's cancellation signal.
Closing the execution iterator itself means explicit cancellation and still
stops the run. The read API's per-request worker cancellation affects queries,
not daemon work.

If the execution owner dies, the guard reclaims its temporary provider process
tree. On daemon restart, use the persistent backlog. An interrupted provider
call is not resumed from CPU/RAM state or blindly retried.

Jobs a role starts detached (`setsid`, `Start-Process`) leave the provider's
process group on purpose and are the role's to wait for; see
[Windows process ownership](windows-process-ownership.md). POSIX process groups
are not a sandbox against children deliberately creating new sessions. A service
manager's control group also covers simultaneous loss of the execution owner and
its guard.

A durable run has no time limit: it ends when its command does. A direct run
whose process tree has used no CPU and written nothing for 30 minutes is
reported stalled to the Engineer waiting on it, and the Manager is asked once;
nothing stops it unless one of them decides to.
