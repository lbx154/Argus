"""Software-engineering vertical; Manager independently chooses execution mode.

The delivery checklist is the Reviewer's certification contract. It exists
because a Reviewer that only reads the Engineer's self-report cannot detect the
most common and most expensive software failure: code that does not build.

The Engineer only ever runs the tests it can see. When a benchmark, a CI job, or
a teammate later compiles the change against assertions the Engineer never had,
a name or signature that was never reconciled turns into a hard build error and
every test in the package fails at once. Certifying "it compiles, and the tests
I ran actually ran" is cheap -- seconds -- and catches that class outright, so
it is a protected floor item rather than advisory guidance.
"""

from __future__ import annotations

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ["delivery"]
CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)
# The delivery checklist is mandatory: an empty software contract is what let
# non-compiling work be certified `done`.
CHECKLIST_OPTIONAL_STAGES: tuple[str, ...] = ()
completion_gate = "none"
COMPLETION_CONTRACT_VERSION = 1
# Safe fallback for old callers that have not yet read the Manager-persisted
# workflow_mode. Runtime orchestration uses resolve_workflow_mode(project_root).
WORKFLOW_MODE = "staged"

# The build gate is irreducible. Planner checklist ops may extend this contract
# but may never delete or weaken these ids.
PROTECTED_ITEM_IDS = frozenset({
    "delivery.builds",
    "delivery.tests-executed",
})

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "delivery": (
        ChecklistItem(
            id="delivery.builds",
            statement=(
                "The changed code compiles / type-checks cleanly in the task's own "
                "toolchain, verified in THIS round by an independent build or "
                "type-check the Reviewer ran or saw run to completion. A build "
                "error anywhere the change is reachable -- an undefined symbol, a "
                "wrong argument count, a mismatched type, an unused import -- is a "
                "hard failure regardless of how correct the logic looks."
            ),
            evidence_hint=(
                "the exact build/type-check command and its output "
                "(e.g. `go build ./...`, `go vet ./...`, `tsc --noEmit`, "
                "`python -m compileall`, `cargo check`, `mvn -q compile`)"
            ),
        ),
        ChecklistItem(
            id="delivery.tests-executed",
            statement=(
                "The tests claimed to pass were actually executed this round and "
                "reported passing, not merely asserted. A test target that failed "
                "to build, collected zero tests, or was never run does not count "
                "as a passing test. When official acceptance tests are held back, "
                "passing repository tests is not sufficient: derive a temporary "
                "compile/behaviour probe from every requested public signature and "
                "boundary case in the task specification, run it, then remove only "
                "that temporary probe before handoff."
            ),
            evidence_hint=(
                "the test command and the result line showing tests ran "
                "(counts, PASS/FAIL, or the runner's summary)"
            ),
        ),
        ChecklistItem(
            id="delivery.objective-met",
            statement=(
                "The requested behaviour change is actually implemented in "
                "production code, and unrelated existing work in the checkout is "
                "preserved."
            ),
            evidence_hint="the changed files and the behaviour they now implement",
        ),
    ),
}


def role_banner(role: str) -> str:
    return (
        "SOFTWARE VERTICAL: implement the requested repository change using the "
        "matched skills and project wiki, preserve unrelated work, and verify the "
        "result with task-native tests. A change that does not compile is not a "
        "candidate for `done` no matter how correct it reads: confirm the build "
        "and the test run this round rather than trusting a self-report. "
        "Execution topology is Manager-owned and is not part of this capability "
        "classification."
    )
