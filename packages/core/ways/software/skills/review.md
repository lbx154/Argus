# Judging the change

Use this when the Reviewer judges a software change. Judge it against the task, the unchanged
callers, the closest similar code and the project's real behaviour: correctness is not read off the
patch, and no reference answer is needed.

1. Read the whole change. In git, `git diff HEAD --stat` and `git diff HEAD`, which include staged
   changes; outside git, the files the task names and the files the work wrote, saying that no
   baseline diff exists. A target commit or test copied in from elsewhere is no evidence the work
   is right.
2. Build it and run its tests yourself, as the README's "What the Reviewer runs" says, and read the
   output for the tests actually running, not only for the absence of errors.
3. Trace each changed public interface through its unchanged callers: positional and keyword
   arguments, defaults, return type and order, every field, the exception raised, invalid inputs
   and the boundary values the task touches. Compare the patch with the closest unchanged code
   that does something similar: a branch, field, alias or ordering it has and the patch lacks is a
   failure even when the happy path passes.
4. Exercise the real entry point. A probe of a lower-level function does not show that the
   wrapper, template, CLI, serialization or process above it works.
5. Every failing test you saw is evidence. A test is obsolete only where the task changes the
   contract it checks, not because another test is expected to pass instead.
6. Keep the review proportional: the touched call path and the cheapest decisive checks, not
   unrelated full-suite work. Check that an optional tool exists (`command -v rg`) before using it.

Accept only when the build passed in your own run, the tests ran, and the behaviour and its
compatibility are borne out by the code you read and the commands you ran; write that judgment to
whoever gave the task. Otherwise write to the Engineer the smallest contract the change breaks,
with the command that shows it.
