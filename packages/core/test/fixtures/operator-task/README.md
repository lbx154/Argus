# Operator question attribution

`events.jsonl` is the unmodified task.added line 22 from the checked-in real
p-c855623c record in frontend/web/src/test/fixtures/home/projects/. It preserves
the actual Chinese task title and task ID that the question banner joins.
The regression tests append controlled question and answer events; those events
are test cases, not a capture of an operator conversation. No original open
hello.py question record was available in this workspace.
