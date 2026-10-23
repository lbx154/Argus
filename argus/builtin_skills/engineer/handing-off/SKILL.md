---
name: "Handing off"
description: "What the Engineer's hand_off summary and note carry, where evidence goes, and who decides when no operator is available."
---

# Handing off

- `summary` is the one sentence the operator reads: what was done and found,
  in their language and plain words, without internal roles, fields or status
  words. Never claim progress, completion or a retry that did not happen.
- `note` is for the next holder: the changes with their output paths, the
  decisive check and its result, what is still open, and why it goes to them.
- The next holder sees only the working directory: a log or output they must
  read is a file under `.argus/evidence/` there, never in your CLI's own
  session folders.
- Hand to the reviewer when the work is ready for independent review; do not
  start a review yourself or through a subagent.

## When no operator is available

The Manager decides in the operator's place. Settle what a requirement means
yourself, on the most defensible reading, and write it in `assumptions`. Never
assume facts or results, or fake credentials or services: a missing input, or
an action only the operator could approve (credentials, spending, or an
irreversible or external step), goes `to=manager`.
