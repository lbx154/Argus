# Agent-authored session titles

Session names now come from Agent topic summaries. Ordinary messages reuse the
existing Manager classifier's `NAME`; direct task handoffs reuse `SESSION_TITLE`
from the existing vertical decision. No naming-only model call or new agent is
introduced. The old first-line truncation fallback and transient first-turn name
lock are removed.

Automatic names carry persisted ownership (`name_source=agent`). The classifier
sees the current automatic title and retains it for same-topic follow-ups, using
`NAME=NONE` when no change is needed. It can summarize a new topic when the user
changes direction. Missing, numeric-only or placeholder output leaves naming
available for a later turn instead of freezing raw instructions as the title.

Explicit names and manual renames are user-owned. Ownership is checked inside the
session metadata lock, so a manual rename made while the Agent is running wins.
Clearing the manual name permits automatic naming again. Existing nonempty names
without ownership metadata are preserved rather than guessing whether the user
chose them. No bulk rename of historical sessions is performed.

Verification:

- Backend: 2,019 selected regressions, 2,017 passed and two platform skips.
- Frontend: 17 session-name/create/sidebar checks passed.
- Ruff, Web/TUI builds and release artifact validation passed. Python type
  comparison: 1,283 existing diagnostics, zero introduced.
- Real browser with Argus-Pi, using an initially unnamed session:
  - `17` followed by a request to explain binary search → `二分查找原理`.
  - A request for another example → title preserved.
  - A change to Tang poetry rhyming → `唐诗押韵`.
  - After manually naming it `我的学习笔记`, a new hash-table question → manual title preserved.
- Both the header and sidebar showed the persisted titles; no browser page errors.
  Four completed dialogue turns used four ordinary replies. The last test request
  initially hit the isolated 60,000-token cap after classification and was retried
  with the test cap increased to 100,000; its failed receipt is preserved. In total
  there were five classifications, four replies and one existing periodic SELF
  learning review, with no naming-only call. The fixture processes were stopped.

Private screenshots, receipts and logs are retained in the local verification
directory. Existing project content, task routing and production usage limits are
preserved.
