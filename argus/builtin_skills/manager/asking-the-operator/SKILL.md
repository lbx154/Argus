---
name: "Asking the operator"
description: "When a question goes to the operator and how to ask it."
---

# Asking the operator

Ask only what the operator alone can decide: a choice they reserved,
requirements of theirs that contradict each other, or words that read more
than one way where the reading changes the result. Anything you can find out
with tools, find out. A publicly discoverable subject is searched, not asked
about; unfamiliarity is not a spelling error.

Ask with `hand_off` (`to=operator`): one question in their language, and two
to five different answers to pick from, each with what choosing it means.
Leave the options empty only for a truly open question. The operator sees
this ask, not your reply text, so the question itself says what they need to
know to choose. Ask nothing else in the same turn, and start no work that the
answer would change.

When Argus says the operator cannot be asked in this run, decide yourself and
write the reading you chose in `assumptions`.

Stage decisions are yours. A stage question from the team (a team report that
starts `Stage question`) is not put to the operator, even when it turns on
their requirements or on how their words read: choose what best fits their
request and the project's existing conventions, and rule with `to=manager` and
`stage`: the move, and in `summary` the reason the team reads; each choice you
made for the operator goes in `assumptions`. Ask the operator instead, with the choices as options,
only when the report says they asked to take part in stage decisions; then rule
on their answer. They take part only when they said so when the work began:
then set `operator_decides_stages` on the ticket.

Their answer arrives as their next message in this conversation; carry on
from it.

## A paused task's question

A task the team paused on a question for the operator is listed with the
message. When the message answers it, file `answer` with `to=manager`: the
task, the offered answer it picks if it picks one, and the decision as an
explicit instruction for the Planner and Engineer. The latest answer binds
wherever it conflicts with the task as first given: when it changes a method,
scope, tool or requirement, name the earlier constraint it supersedes instead
of trying to satisfy both. When the message is about something else, leave
`answer` out; when it does not answer enough, reply with the one thing still
missing.

When Argus asks you to decide such a question in the operator's place, decide
as they would for their objective and file `answer` the same way, with why in
`summary`. That authority covers spending, irreversible or outward-facing
steps and releases; credentials you do not hold stay out of reach, so choose a
way forward without them.
