# Windows provider process ownership

Each CLI provider turn owns a private Windows Job Object. The provider is
created with `CREATE_SUSPENDED`, assigned to the Job, then resumed. Ordinary
children and grandchildren inherit that Job even if their immediate parent
exits first. `CREATE_NEW_PROCESS_GROUP` alone does not detach Windows work.

Argus retains the Job handle and the `Popen` process handle. Stop, watchdog,
natural provider exit and exceptional runner exit terminate remaining Job
members. The Job handle is non-inheritable and uses `KILL_ON_JOB_CLOSE`, so an
abrupt host exit also cleans up an assigned turn. No process-name matching or
unqualified `taskkill /T` is involved; PID reuse cannot retarget cleanup.

CPython closes the primary thread handle during `Popen`. Argus obtains the
still-suspended primary thread through a Toolhelp snapshot, retains a thread
handle, verifies its owning process, then calls `ResumeThread`. Assignment or
resume failures terminate the retained process without allowing provider code
to execute and close all newly acquired handles.

There is a narrow crash window between `CreateProcess` returning and Job
assignment: if the host is forcibly killed in this window, the unassigned
provider can remain suspended. It has not executed or created descendants.
Eliminating that window requires a native launch path that supplies the Job at
process creation; Python's `Popen` does not expose that attribute. This contract
does not claim to be a security sandbox. Deliberately cooperating children can
request breakaway because the Job allows explicit `BREAKAWAY_OK`.

Argus's two durable launch boundaries are the Windows subagent worker launcher
and the durable command launcher. They request `CREATE_BREAKAWAY_FROM_JOB` only
after verifying membership in the inherited Argus turn Job. Their existing
registry, redirected logs and exit-status sidecar provide independent ownership.
Descendants of an already-detached worker do not request another breakaway.
An outer host Job that prohibits breakaway causes a visible launch failure;
Argus does not silently launch work that would be killed with the provider.

Prompt stdin uses a finite temporary file so startup output cannot deadlock a
synchronous prompt write before watchdog monitoring begins. Output reader
threads own and close their read handles at EOF. A detached writer that still
inherits output may finish independently; bounded post-turn readers discard
late output without closing another thread's descriptor under a blocking read.

Native Windows checks live in
`tests/agent_cli/test_windows_turn_ownership.py`. They launch synthetic Python
and PowerShell processes through the actual runner and both durable launchers;
they cover children, grandchildren, parent-first exit, natural completion,
stop, watchdog, host exit, setup failures and unrelated processes. Assertions
and fallback fixture cleanup use handles retained before the tested action.
`test_prompt_stdin_backpressure.py` and `test_output_pipe_lifecycle.py` exercise
real pipe backpressure and independent inherited output. Linux runs skip the
Windows-specific checks and are not evidence of native Windows behavior.
