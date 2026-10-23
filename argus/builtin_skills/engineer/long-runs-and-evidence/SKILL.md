---
name: "Long runs, experiments and evidence"
description: "How the Engineer runs a command longer than two minutes, keeps experiment outputs honest, and backs a performance claim."
---

# Long runs, experiments and evidence

## Commands over two minutes

Start them detached so they outlive your shell, keep the exit status, do the
work that does not need the result, then wait for it once.

- POSIX: `setsid nohup sh -c '<command>; echo $? > <run>/exit_code' > <run>/log 2>&1 < /dev/null &`,
  then wait on `until [ -e <run>/exit_code ]; do sleep 30; done`.
- Native Windows (PowerShell 5.1): `Start-Process -PassThru -WindowStyle Hidden -FilePath <exe> -ArgumentList <args> -RedirectStandardOutput <run>\log.txt -RedirectStandardError <run>\err.txt`,
  keep the process id, then wait on `Wait-Process -Id <pid>`.

Wait in one call: on Codex, one `write_stdin` with empty input and a
`yield_time_ms` longer than the job; on Copilot, the shell tool in `async`
mode, then end your turn, and Copilot resumes you when it exits; elsewhere, the
longest blocking call your shell allows. Never poll in short loops.

## Experiments

- Block dependent runs when a readiness step fails; keep each attempt's outputs
  separate.
- Evidence is the relevant source, inputs and results, not unrelated logs. Keep
  failures; readiness never replaces the independent validation the task needs.

## Performance claims

A root-cause, bottleneck or replacement claim needs hot-path or live-resource
evidence plus timing, profiling or a controlled A/B. Correctness smoke tests
can share hardware, but latency evidence needs an uncontended device or an
explicitly matched concurrent load. Check the actual device and the other
processes on it; free memory or a zero-utilization snapshot does not prove
isolation. Independent repetitions can run one after another on a released
device.
