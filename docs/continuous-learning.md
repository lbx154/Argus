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

Answer learning aims to add understanding, not to reformat the delivered reply.
A short answer can lead to a deeper note: explain the mechanism, connect related
examples and existing methods, and retain assumptions or counterexamples that
matter in another task. A supported derivation or a useful connection can justify
an update even when the underlying facts are unchanged. Additional length alone
does not. Existing topical pages and methods are improved rather than splitting
each exchange into a separate summary.

Answer and mission notes use the same prose guidance: explain the subject to a
reader, rather than narrating how the agent collected and saved material. Start
from an idea or example before introducing notation. Audit and evidence-assessment
terminology is not the default narrative frame; use such terms only when they
belong to the subject itself. Keep limitations that change the meaning alongside
the relevant claim, expressed concretely, and collect source-access details in one
short source note. Existing Skills are rewritten coherently instead of retaining
fixed English headings around new paragraphs.

Notes use natural prose and subject-specific headings in the operator's language.
There is no fixed question/conclusion/review-date form or mandatory body word
count. References and specific limitations remain visible; reasoning is not
presented as measured evidence. A historical finding does not receive an arbitrary
90-day expiry. The answer-learning prompt has a separate 18,000-character budget,
including up to 6,000 characters of observed work; tool traces omit transport
identifiers and timing fields instead of spending that budget on bookkeeping.
When updating a transcript-shaped note, the pass rebuilds the explanation rather
than preserving its old headings. Comparisons and proposed explanations are not
silently strengthened into causal conclusions. Answer synthesis uses medium
reasoning effort; model selection and the number of learning calls are unchanged.
Both prompt budgets preserve the writing rules, shortening task material when
needed. Making an obscured explanation readable can justify a revision; repeatedly
restyling already clear notes does not count as useful learning.

Answer learning edits a temporary copy of the relevant libraries. The host
validates all changed pages before publishing any of them. A failed provider,
malformed page, attempted deletion or conflicting concurrent edit leaves the
canonical pages intact. Identical content under a new filename is ignored.
Individual replacements are atomic; earlier versions are retained under the
library's `.history` directory, excluded from browsing and recall.

Corrections update the current page's summary, conclusions and index entry.
The current page does not retain an incorrect lead followed by a corrective
appendix. A person or a role that finds a page wrong corrects it the same way:
`argus wiki correct pages/<page>.md --wiki <library> --statement "..." --reason
"..."` (or `--scope vertical --vertical <name>` / `--scope global` for this
home's shared libraries, and `POST /api/wiki/page/correct` from the web
cockpit) replaces the page's opening paragraph with the corrected statement,
replaces the summary when a new one is given, moves what the page said before
into a `## History` section that recall does not search, keeps the earlier
file under the library's `.history` directory, starts that History entry with
`- <date>, corrected by <who>:` so recall shows "corrected <date>" in the page's
line (the front matter keeps its two fields; only the summary line changes),
updates the index entry, and writes a `corrected` record to the knowledge
journal so the feed shows it. A corrected shared page is newer than the
project copy it was promoted from, so the next promotion keeps it. Recall searches topic and procedure content, excluding generic
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
the libraries when the receipt changes. One switch, `ARGUS_SKILL_LEARNING`
(on by default), turns all of it off: reflection, answer learning, Skill
maintenance and promotion, the project wiki, recall from other projects, and
the shared-principles pass, which otherwise runs only when the shared pages
change.

The Engineer writes only inside its workspace, so the procedures it keeps, and
the one the post-mission reflection may add, are the workspace's Agent Skills,
`.agents/skills/<name>/SKILL.md`, with a script it ran beside the `SKILL.md`
when a step is better run than read. A value read from code goes into the Wiki
with the layer it belongs to, the value that takes effect on the usual path,
what decides it and why, and may link a small check in the Wiki's `checks/`
that a later project reruns against another version; a page shared beyond the
project takes the checks it links to along. Nothing imports these files: roles
of the same project discover them, the project's mission view lists them,
recall shows them to later projects of any vertical (as pointers that name the
code), and the post-mission review offers them for the vertical's shared
library, with their code. Engineer procedures an older release kept in the
project state (`skills/engineer`) are still read, never written.

Quality checks cover a changed factual conclusion reaching the next recall,
unrelated English/Chinese questions, repeated findings and methods, partially
written drafts, concurrent human edits, and a worker killed while another
process is waiting to recover the same queue. Real-provider validation uses
isolated state and checks subsequent Manager tool reads of the saved pages.
