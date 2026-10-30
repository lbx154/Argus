The question is the clarification in `map-clarification/events.jsonl`; that record
is a controlled reproduction of the operator's dice report. The task is trimmed
from `operator-task/events.jsonl`, and the answer text and attachment receipt are
from `conversation-fixes/after.json`'s third browser request. These are existing
recorded inputs, not a claim that the original dice question came from an Engineer.

The regression varies the asker (Engineer, Reviewer, Planner), running/stopped
state and the two public answer routes. It uploads the recorded CSV name anew
and verifies one answer record with the receipt, the same recipient and task,
and the ordinary message attachment instructions in the asker's prompt.
