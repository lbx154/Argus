# Learning during work

Each delivered web answer is queued for a separate learning pass. The reply
does not wait for it. Finished missions retain their reflection pass and report
through the same project learning status.

The pass independently considers:

- **Knowledge:** useful explanations, findings and sources absent from the saved
  library, including established subject knowledge. Citing a source is not proof
  that it was read. Unverified claims and limitations stay explicit.
- **Skills:** a concrete reusable research, reasoning, implementation or checking
  method. Answer-derived methods stay in the project's `skills/self` directory,
  with evidence and limits; subject facts alone do not create a skill.
- **Preferences:** explicit user preferences and corrections, kept in the
  operator's private memory. One topic request is not a lasting preference.

Related pages are read before editing. Unchanged file content does not count as
an update. An exchange can legitimately add nothing; the UI reports this rather
than claiming growth. Shared Wiki guidance is available even before a project
creates its own Wiki. Existing role recall and Skill discovery make the saved
pages and procedures available to subsequent work.

Answer learning edits a temporary copy of the relevant libraries. The host
validates all changed pages before publishing any of them. A failed provider,
malformed page, attempted deletion or conflicting concurrent edit leaves the
canonical pages intact. Identical content under a new filename is ignored.
Individual replacements are atomic; earlier versions are retained under the
library's `.history` directory, excluded from browsing and recall.

Corrections update the current page's summary, conclusions and index entry.
The current page does not retain an incorrect lead followed by a corrective
appendix. Recall searches topic and procedure content, excluding generic
request words, source metadata and history. Weak positive embedding similarity
alone does not make a page relevant. Existing derived indexes are refreshed
automatically without changing the canonical Markdown.

`answer-learning.sqlite3` in the operator home persists answer jobs. Delivery
IDs make scheduling idempotent. An answer worker drains them in order, and a
process lease recovers interrupted work after restart. A separate write lock
serializes answer and mission reflection against shared knowledge and profiles.
Provider failures stay visible and answer learning can be retried without
repeating the user's task. Queue payloads remain private; status responses
contain only state and saved-entry metadata.

`GET /api/projects/{sid}/learning` returns project-scoped queued/running/complete,
unchanged and failure receipts. `POST /api/projects/{sid}/learning/{job}/retry`
retries a failed answer job. Both require the usual project authentication.
The chat area and library entries poll after foreground work ends and refresh
the libraries when the receipt changes. `ARGUS_SKILL_ANSWER_LEARNING` and
`ARGUS_SKILL_REFLECTION` retain their existing opt-out behavior.

Quality checks cover a changed factual conclusion reaching the next recall,
unrelated English/Chinese questions, repeated findings and methods, partially
written drafts, concurrent human edits, and a worker killed while another
process is waiting to recover the same queue. Real-provider validation uses
isolated state and checks subsequent Manager tool reads of the saved pages.
