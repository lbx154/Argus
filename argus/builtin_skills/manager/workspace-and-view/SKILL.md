---
name: "Workspace and the right-side view"
description: "Where project outputs and Argus state live, and how the Manager chooses what the right-side view shows."
---

# Workspace and the right-side view

The workspace is where project outputs live and where every view path is
resolved. The Argus state directory is private session memory and control
state (CHECKPOINT.md, backlog.jsonl, events.jsonl, skills/); never present a
state file as a project output.

You choose what the right-side view shows: inspect current files, pick the
most useful existing file, or author a presentation under
`<workspace>/.argus/live`. Use the view tool your prompt names:

- `<tool> status`
- `<tool> set --title <title> --reason <reason> --path <workspace-relative-path> [--path ...]`
- `<tool> clear`

Path order is presentation order; the first path is shown by default. Never
claim the view changed until the tool returns `ok: true` with `exists: true`.
