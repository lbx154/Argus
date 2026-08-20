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

from argus_skill.roles.prompts import planner as planner_prompts


def _resume(tmp_path: Path, altitude: str, monkeypatch) -> str:
    monkeypatch.setattr(
        planner_prompts,
        "continuous_request",
        planner_prompts.continuous_request,
    )
    from argus_skill.roles.prompts import registry

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


def test_resume_delta_carries_the_vertical_altitude(tmp_path, monkeypatch) -> None:
    block = "## Accepted same-area papers on disk\n- Some ICLR paper\n"
    assert block.strip() in _resume(tmp_path, block, monkeypatch)


def test_resume_delta_stays_quiet_when_the_vertical_has_no_altitude(
    tmp_path, monkeypatch
) -> None:
    """Most verticals render nothing here; the delta must not grow an empty heading."""
    rendered = _resume(tmp_path, "", monkeypatch)
    assert "Accepted same-area papers" not in rendered
    assert "Continued Planner cycle" in rendered
