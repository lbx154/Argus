"""A resumed Planner must still see the facts that change between cycles.

The resume prompt exists so a continuing role session is not charged for the
immutable contract again, and its own header promises that the state below
supersedes stale session facts. It carried the journal, the runtime digest and
the stage — but not the vertical's search altitude, which is the block that
moves most: the promoted floor and frozen count for a metric campaign, the
accepted papers pulled to disk for a paper campaign.

Every long campaign resumes, so in practice that block reached almost no
Planner at all. Observed on four live ICLR campaigns: the altitude rendered
534 chars through the prompt catalog and appeared zero times in the verbatim
prompt log.
"""

from __future__ import annotations

from pathlib import Path

from argus.roles.prompts import planner as planner_prompts


def _resume(tmp_path: Path, altitude: str, monkeypatch) -> str:
    monkeypatch.setattr(
        planner_prompts,
        "continuous_request",
        planner_prompts.continuous_request,
    )
    from argus.roles.prompts import registry

    real = registry.resolve_role_prompt

    def _with_altitude(request):
        context = real(request)
        return context.__class__(
            **{**context.__dict__, "search_altitude": altitude}
        )

    monkeypatch.setattr(registry, "resolve_role_prompt", _with_altitude)
    return planner_prompts.build_continuous_resume_prompt(
        continuous_objective="improve the paper",
        journal_tail="",
        planning_cycle=3,
        project_root=tmp_path,
        state_root=tmp_path,
    )


def test_resume_delta_says_files_opened_earlier_are_still_in_context(
    tmp_path, monkeypatch
) -> None:
    """A resumed Planner must not reopen the playbook it already holds.

    The delta repeats the stage playbook's "Open `…` before acting" line (inline
    when the section is under the elision threshold). In argus-trial
    (2026-10-17 release) all 153 resumed Planner cycles spent their first turn
    reopening that byte-identical playbook, re-sending the whole thread once more.
    """
    rendered = _resume(tmp_path, "", monkeypatch)
    header = rendered.split("\n## ", 1)[0]
    assert "already opened in this session" in header
    assert "open it again only if its text is no longer in your context" in header
