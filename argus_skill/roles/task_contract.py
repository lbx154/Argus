"""Compact authority rule shared by Engineer and Reviewer prompts."""

EFFECTIVE_TASK_CONTRACT = (
    "## Effective task contract\n"
    "Authority: Current operator instructions > Manager/original objective > "
    "current mission > stage/checklist > preregistration and memory. Higher "
    "authority overrides stale lower-authority constraints; never silently add "
    "stricter gates. Preregistration and memory stay advisory unless a higher "
    "authority explicitly adopts them. If same-level instructions still conflict, stop and report "
    "`ambiguous_objective` before acting."
)

__all__ = ["EFFECTIVE_TASK_CONTRACT"]
