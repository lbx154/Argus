# Learning during work

A SELF turn keeps what it learned in that same turn, the way the Engineer does.
While `ARGUS_SKILL_LEARNING` is on, the Manager's SELF prompt carries one line:
a reusable procedure goes into the workspace's Agent Skills,
`.agents/skills/<name>/SKILL.md`, and a lasting project fact into the project
Wiki; otherwise nothing is written. No separate learning call follows a reply.
Finished missions keep their reflection pass and report through the project
learning status.

The mission reflection pass independently considers:

- **Knowledge:** useful explanations, findings and sources absent from the saved
  library, including established subject knowledge. Citing a source is not proof
  that it was read. Unverified claims and limitations stay explicit.
- **Skills:** a concrete reusable research, reasoning, implementation or checking
  method, kept in the workspace's Agent Skills with evidence and limits; subject
  facts alone do not create a skill.
- **Preferences:** explicit user preferences and corrections, kept in the
  operator's private memory. One topic request is not a lasting preference.

Related pages are read before editing. Unchanged file content does not count as
an update. A mission can legitimately add nothing; the UI reports this rather
than claiming growth. Existing role recall and Skill discovery make the saved
pages and procedures available to subsequent work.

Mission notes explain the subject to a reader, rather than narrating how the
agent collected and saved material. Start from an idea or example before
introducing notation. Audit and evidence-assessment terminology is not the
default narrative frame; use such terms only when they belong to the subject
itself. Keep limitations that change the meaning alongside the relevant claim,
expressed concretely, and collect source-access details in one short source
note. Notes use natural prose and subject-specific headings in the operator's
language. There is no fixed question/conclusion/review-date form or mandatory
body word count, and a historical finding does not receive an arbitrary 90-day
expiry. The prompt budget preserves the writing rules, shortening task material
when needed.

Corrections update the current page's summary, conclusions and index entry.
The current page does not retain an incorrect lead followed by a corrective
appendix. A person or a role that finds a page wrong corrects it the same way:
`argus wiki correct pages/<page>.md --wiki <library> --statement "..." --reason
"..."` (or `--scope vertical --vertical <name>` / `--scope global` for this
home's shared libraries) replaces the page's opening paragraph with the corrected statement,
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

A page that no longer holds at all — later facts overturned it, or the problem
it describes has been fixed — is retired. Each recalled page shows when it was
written and where it came from, and a page that holds only for one version or
environment names it in its description, so a role can tell what the page was
true for and rerun a check it carries. A role that finds current facts
contradicting a page says so with its path. The mission reflection, which reads
that account, and the shared-principles pass, which sees a vertical's lessons
side by side, name such a page on a `RETIRE: <page> — <why>` line; the judgment
is the model's, and nothing expires by age or usage count. The host retires only
a page of a library recall reads for that project: it moves the whole page into
the library's `.history`, ending with a `- <date>, retired by <who>: <why>`
History entry, drops its INDEX line and writes a `retired` record to the
knowledge journal, so recall, browsing, promotion and the principles stop
reading it and the feed shows who retired it and why. Skills are not retired
this way.

`answer-learning.sqlite3` in the operator home records each mission's
reflection pass. A write lock serializes reflection against shared knowledge and
profiles. Status responses contain only state and saved-entry metadata.

`GET /api/projects/{sid}/learning` returns project-scoped running, complete,
unchanged and failure receipts and requires the usual project authentication.
The chat area and library entries poll it and refresh the libraries when the
receipt changes. One switch, `ARGUS_SKILL_LEARNING` (on by default), turns all
of it off: reflection, the SELF learning line, Skill maintenance and promotion,
the project wiki, recall from other projects, and the shared-principles pass,
which otherwise runs only when the shared pages change.

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
library, with their code. A page or Skill whose front matter names another
vertical this host knows (`vertical: <name>`) is shared into that vertical
instead. Engineer procedures an older release kept in the project state
(`skills/engineer`) are still read, never written.

Quality checks cover a changed factual conclusion reaching the next recall,
unrelated English/Chinese questions, repeated findings and methods, partially
written drafts, concurrent human edits, and a worker killed while another
process is waiting to recover the same queue. Real-provider validation uses
isolated state and checks subsequent Manager tool reads of the saved pages.
