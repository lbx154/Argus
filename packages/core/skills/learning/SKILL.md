---
name: learning
description: "When a task taught you what a later task would need: keep it as a skill, a wiki page or a tool, and keep what you use in good order"
---

The team's library (skills, wiki, tools) is kept by those who use it. No one tidies it later: what
you find while using it, you are the one who knows, so you are the one who acts. Argus keeps its
history in git, so a change can always be read back or undone.

**While you use it.** A page that was wrong, stale, or hard to find your way in; a skill whose steps
no longer work; a tool that almost did what you needed: put it right now, as part of your task,
in the smallest change that fixes it. A page you had trouble reading is too long or mixes topics for
you: split it the way you would have wanted to find it. What only one project needs moves to that
project's page. What a tool now checks is said in a line that names the tool. In your letter, say
in a line which of them you used and whether they helped, misled or were out of date.

**While you write a script.** When you write a script for a check or a step, ask then, while you
know what it does: would another task, in this project or another, write the same thing? If so,
write it as a tool from the start (its inputs as arguments) instead of as a one-off in your
evidence folder, and use it yourself. A script only one kind of work needs belongs in that skill.

What you keep is read by agents who never saw your task. Write for them: no project ids, task ids or
paths of this machine's workspaces; the case that taught it in a line, enough to recognize the next.

**A skill**, for what an agent can run. A folder in the team's skills folder (Where things are names
it) holding a SKILL.md: front matter with `name`, the folder's name, and `description`, one sentence
on what work it is for; then how to use it, and the script that does the step or catches the failure.

**A tool**, for a command many tasks run as it stands: an executable file in the team's tools
folder, on every role's PATH by its name. On POSIX keep its execute bit and shebang; on Windows
supply a native command (for a script, a `.cmd` companion invoking its interpreter). A shebang alone
is not a Windows command. Its first comment (`#`, `//`, `rem` or `::`) says in one sentence what
it does (Argus lists that); `--help` says how. Check it by name in a role's command environment,
not only by calling its interpreter directly. It changes nothing it was not asked to.

**A wiki page**, for what no script runs: a fact about a tool or library, a pitfall and how it
showed, a route that failed and why, a decision and its reason. One topic a page, in Markdown in the
wiki folder, opening with `# Title`, listed in the wiki's INDEX.md in one line. A fact says what was
seen and how to check it, and dates itself when it may go stale (a tool's version).

Before adding any, read what is there: improve what covers it rather than adding beside it, and
delete what your work showed no longer holds.
