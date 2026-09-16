# Single-agent vertical reuse

Single-agent tasks now use matched vertical methods and Skills. Manager selects
the capability in its existing front-door classification call, independently of
SELF/TEAM routing. Built-in capabilities, project candidates and shared formal
Argus-authored verticals use the existing discovery and materialization paths.

The execution modes previously supplied no Skill paths at all. Ordinary SELF
replies also used a cached Skill scope, and the durable Manager session replaced
per-turn paths with its startup list. The new per-turn context fixes all three:
it exposes global, vertical and project libraries, includes the selected role's
domain requirements, and preserves explicitly supplied paths through the session.
The Manager store reads campaign scope from project state rather than the user's
artifact directory. Existing Skill precedence remains project, vertical, global.

Selecting Skills does not change the campaign's vertical/stage, create a Team, or
claim independent review. An explicit no-match selection clears the previous
vertical for that turn. Invalid selections cannot resolve arbitrary directories;
other projects' unpromoted candidates remain unavailable. Tool-free greetings stay
lean; matched prose tasks get tools so the worker can actually read the Skills.

## Verification

- Ruff and both frontend builds passed; release artifacts match the source.
  Python type comparison: 1,283 existing diagnostics, zero introduced.
- The initial regression selection covered 2,074 tests, with two platform skips.
  Its two architecture failures were corrected by placing capability composition
  in Manager and documenting the required existing Skill-store keyword. The final
  102-test rerun of SELF execution, context and architecture passed.
- Seven SELF modes covered: micro, implement, debug, review, synthesize, inspect,
  reply. Coverage includes shared learned verticals, cold starts, stale Manager
  caches, unmatched/invalid inputs, and preserving active campaign state.
- A Web API integration test uses the real classification, Manager session and
  SELF routing code, and checks the completed solo task in Atlas. No extra model
  classification or team dispatch is introduced.
- Real browser + Pi: software was selected for a one-function repair; the worker
  read its software Skill, changed addition to subtraction, and verified
  `net_total(150, 20) == 130` in about 39 seconds.
- A second browser task selected the previously Argus-authored
  `traditional_chinese_birthdate_reading` in a new project. The worker read the
  existing Skill and rejected lunar day 31 without changing the calendar or
  inventing a date. About 38 seconds; both tasks appeared as completed in Atlas.
- These two real tasks made four model calls in total: one classification and
  one SELF execution each. No Team or background task was created. Browser page
  errors: zero. The date task also attempted two unnecessary runtime-inspection
  commands that failed; its input validation and final answer were still correct.
  This test proves Skill reuse, not flawless tool choice or general domain quality.

Detailed receipts, screenshots and test logs are kept in the private local
verification directory, outside the repository. Production uses the original
public tunnel and retains existing projects and resource limits.
