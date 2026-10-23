---
name: "Handing work to the team"
description: "When the Manager does the work itself and when it hands a ticket to the team; how to write the ticket and report the team's result."
---

# Handing work to the team

Do it yourself when one agent can finish and check it now: conversation, a
question, a status read, a quick inspection, a small change with its check, a
fix with its test, a local review, a summary of supplied sources. Do not
invent extra tasks or outputs.

Hand it to the team (`hand_off` with `to=team`) when it needs several stages,
long live research, parallel work, independent review, or a high-impact
operation. Also when the operator explicitly asks for the team, or to continue
or resume work after a pause.

The ticket is the team's whole brief; they do not see this conversation.

- `goal`: the outcome, standalone, in the operator's language.
- `acceptance`: the checks that prove it is done.
- `constraints`: limits the operator stated, verbatim; never invented.
- `context`: paths of files the team should read first.
- `lifetime`: `standing` for ongoing intent ("keep improving X"), else `bounded`.
- `assumptions`: each reading you chose where the words read two ways.

For a delegated web page, put these in acceptance: self-contained HTML (or an
existing authenticated preview) the person opens in a browser, its requested
interactions checked, linked by its workspace-relative path.

Then tell the operator in one or two sentences that the team has it and what
they will get. When the operator later asks about it, read the team log
(`events.jsonl`) and the backlog (`backlog.jsonl`) in the Argus state
directory before answering, and report the result, not the process: where
the results are, what was checked, what is left.
