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

## Identify research subjects before selecting methods

For automatic web routing, the tools-disabled classifier copies a named public
research subject verbatim into `LOOKUP_SUBJECT`. This takes precedence over a
speculative specialist selection or immediate answer. The host searches the
original phrase, shows “正在查证研究对象与来源…”, and passes discovery results to
the executing Manager before any workflow offer. Explicit qualifiers are kept;
the available Skills and the model's prior knowledge cannot redefine the name.
Local work, private subjects, supplied-source-only/no-web requests and routine
conversation do not request this lookup. Explicit Chat still skips classification.

Search uses bounded Bing RSS requests and a six-hour workspace cache. Empty
results, unavailable search and successful discovery remain distinct. Search
snippets are untrusted discovery data: the Manager must inspect primary sources
before drawing conclusions, separate vendor claims from independent evidence,
and check limitations before repeating guarantees. Up to two targeted refinements
are allowed for identification; unresolved ambiguity is stated rather than turned
into a claim that the subject does not exist. Helper commands use the running
version's absolute script paths to avoid accidentally loading a stale installation.

The answer then enters the existing learning queue. Correct identity and qualified
claims improve the input to that pass; a search hit alone is not a knowledge entry
or a new Skill. Completed reads of retained source text carry URL/access receipts
into learning; a receipt proves source access, not independent factual validation.
Regression checks cover contradictory medical routing, preserved
names and qualifiers, source failures, visible phases, cancellation and stale
per-turn state. Real-provider checks include Jev, the explicitly named Japanese
encephalitis virus, an unverified model name, and subsequent knowledge recall.
