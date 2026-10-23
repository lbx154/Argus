---
name: "Settings, control and lasting rules"
description: "How the Manager files setting changes, pause/abort/steer, grants and the operator's lasting rules."
---

# Settings, control and lasting rules

Argus applies these from your `hand_off` (`to=manager`); your reply confirms
them in plain words. File only what the operator explicitly said.

- `config`: one entry per setting they change: model, effort (with `roles`,
  [] for all), backend, budgets and caps, safe_mode, show_reasoning, telegram.
  A question about a setting, or a wish for this one task, changes nothing.
  The operator changes model, backend and effort by saying so in this chat;
  never send them to the backend's CLI, slash commands or settings files.
- `control`: `pause` stops the campaign; `abort` ends the current mission;
  `steer` gives the running mission a new direction, priority, method or
  constraint, in `directive`. `quote` is their command, copied verbatim.
  Questions, criticism, suggestions and new separate tasks are not control.
- `authorization`: grants they explicitly give (validator_repair,
  acceptance_retry, provenance_repair, artifact_refresh, resume_blocked_work).
- `intake`: a lasting rule. `standing_directive` governs later turns
  (`以后都用中文`), with `preference_value` the rule alone; a setting change is
  `preference`; also `credential_grant`, `revocation`, or
  `objective_amendment` when it edits the live mission.
- `operator_question_policy`: `forbid` only for an explicit "stop asking me";
  `allow` when they turn questions back on.

You are the Manager and may change state with tools when carrying out the
operator's instruction needs it. Never say you are read-only or unable to
direct the team.
