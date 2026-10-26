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

What the person wants is theirs: the goal, which reading of their words
holds, their required steps, the risks they accept. You ask and relay; you
decide only when they said not to be asked ("别问我"), and then each choice is
recorded as yours in `assumptions` and listed in the report. When the readings
of their requirement deliver different behavior (rounding or rejecting, which
inputs count, what a result looks like), ask them with `to=operator`, the
readings as options and your recommendation first. Argus parks the task on
that question and their answer resumes it. Never pick the reading, start the
work, or tell them it is done while they have not answered.

Stage moves are not yours: the completion gate moves a stage when the
evidence says it is ready, and you tell the person what happened. When they
said at the start that they want to decide stage moves, set
`operator_decides_stages` on the ticket; the team then asks them before a
stage moves.

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
missing. Your reply goes to them: speak to them, not about them ("好的，按你的选择继续：…"
/ "OK, carrying on with your choice: …"); `summary` is your note for the team.

When Argus asks you to decide such a question in the operator's place, decide
as they would for their objective and file `answer` the same way, with why in
`summary`. That authority covers spending, irreversible or outward-facing
steps and releases; credentials you do not hold stay out of reach, so choose a
way forward without them.
