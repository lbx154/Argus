# Research trace retention

Argus preserves Copilot session traces by default. The previous implicit
seven-day deletion policy is disabled on upgrade unless you explicitly set a
positive `ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS`. Already deleted files
cannot be recovered by this change.

## What is retained

| Record | Online storage | Archive behavior |
| --- | --- | --- |
| Argus project `events.jsonl*` | Existing event-history rotation | Every generation remains retained |
| Managed Copilot `session-state/<id>/` | Preserved by default | Opt-in inactive-session reclamation first saves a verified ZIP in `<ARGUS_SKILL_HOME>/copilot-home/session-archives/<id>/` |
| Project `agent_io.jsonl*` | 128 MiB rotation size, two old online generations by default | Reclaimed generations are saved in `trace-archives/agent_io/` beside the log |

ZIP archives are immutable and have **no automatic expiry**. Compression
reduces online storage but does not bound total disk usage. Monitor free space,
copy archives to your research storage, and explicitly remove only archives
you no longer need. A failed archive leaves the original files online.

These controls are host-wide, shared by all projects. Argus never sweeps a
personal, explicitly selected `COPILOT_HOME`, or dedicated Copilot account
directory. It does not reclaim sessions through symlinks or Windows junctions.
Normal workers may still use a linked state root.

## Configure

Settings shows the effective policy and capture mode; its advanced configuration
form and `/config` persist these controls across restarts. `argus --config-help`
lists their defaults and effective values. Environment variables override saved
settings. Existing processes must be restarted to change inherited environment
overrides; saved settings are read on subsequent calls.

| Setting | Default | Meaning |
| --- | --- | --- |
| `ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS` | `0` | Preserve all online sessions. A positive number opts into archival and reclamation after that many inactive days. |
| `ARGUS_SKILL_AGENT_IO_MAX_BYTES` | `134217728` | Rotate raw I/O at this online size; `0` disables rotation. |
| `ARGUS_SKILL_AGENT_IO_KEEP` | `2` | Old generations kept online; even `0` archives reclaimed data. |
| `ARGUS_SKILL_AGENT_IO_MODE` | `full` | Raw prompts and streams; `compact` saves summaries only and cannot supply a complete research trace. |

For example, `/config copilot_session_retention_days=7` enables archival after
seven inactive days. `/config copilot_session_retention_days=0` preserves all
online sessions. For I/O settings use `agent_io_max_bytes`, `agent_io_keep`, and
`agent_io_mode`, or their full environment-variable names.

## Safe reclamation and resume

Session age uses the newest directory or file activity, including nested files.
Ordinary workers and warm ACP processes hold a shared use lock for their whole
process lifetime. Reclamation requires the exclusive lock, so it skips a home
with any live worker, even one producing no output. A session about to resume
is also protected during startup. Sweeps are throttled to once per hour while
idle; the configured window is an eligibility threshold, not a deletion deadline.

Every archive is read back in full, with ZIP CRC, byte-count and SHA-256 checks,
then published before source reclamation. Concurrent I/O writers share one
portable lock through archival, rotation and append. Failed I/O writes try to
save the batch in `trace-archives/pending/` and report an error.

An archived managed Copilot session is automatically restored before ordinary
or ACP resume. An interrupted partial reclamation restores the complete archive
and retains the partial remainder for inspection. Corrupt archives produce an
error instead of silently replacing the requested session with fresh state.

## Inspect or export evidence

Copy the ZIP files together with project `events.jsonl*`, online `agent_io.jsonl*`
and any `trace-archives/pending/*.jsonl`. Each ZIP contains `manifest.json` with
the original source name, creation time, member sizes and SHA-256 digests;
`payload/` contains the original files. Raw records retain their existing
`call_id` and provider-session references for matching to project history.

To verify a ZIP without changing running state:

```python
from pathlib import Path
from argus.core.trace_archive import verify_archive

manifest = verify_archive(Path("/absolute/path/to/archive.zip"))
print(manifest["source_name"], manifest["created_at"])
```

Extract payloads into a separate evidence directory for analysis. Do not unpack
an archive over a running Copilot session. Automatic resume performs the safe
restore; archives remain available afterward.
