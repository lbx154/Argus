"""A small shared tool contract injected only when the tool is available."""

_PURPOSE = {
    "manager": "test an interpretation of current team evidence or compare coordination choices",
    "planner": "challenge a proposed plan, dependency, or missing evidence",
    "engineer": "seek a second opinion on an implementation, diagnosis, or experiment",
    "reviewer": "challenge an assessment or identify a missing verification",
}


def advisor_prompt(role: str, *, native: bool, tool_name: str = "consult_advisor") -> str:
    invocation = (
        f"Use {tool_name}(question, evidence_refs)"
        if native else
        "Use python -m argus_skill.tools.advisor consult --question QUESTION --evidence-ref PATH"
    )
    return (
        "\n\nIndependent advisor available: " + invocation + " when useful to " + _PURPOSE[role] + ". "
        "Evidence refs are workspace-relative text files, optionally prefixed workspace: or state:. "
        "The tool uses a separately configured model and returns a consultation_id with source references. "
        "Its answer is advice: retain your role's decision authority and verify consequential claims. "
        "When the advice changes your decision, cite its consultation_id and explain what you accepted or rejected. "
        "Do not wait or loop if the advisor is unavailable, interrupted, or out of its call allowance."
    )
