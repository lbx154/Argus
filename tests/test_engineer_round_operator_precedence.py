from __future__ import annotations

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend


def test_latest_operator_scope_reaches_next_round_without_stale_guidance(
    tmp_path,
) -> None:
    reviewer_action = "Also implement the beta export before declaring done."
    latest_scope = "Latest operator scope: implement alpha only; do not implement beta."
    guidance = iter([
        ["Initial operator scope: implement alpha and beta."],
        [latest_scope],
    ])
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="implemented alpha"))
    backend.queue(
        "reviewer",
        CannedResponse(
            review_action=('revise_review', {'review': ('beta is absent') + '\n\n' + (reviewer_action)})
        ),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(message="kept alpha only under the updated operator scope"),
    )
    backend.queue(
        "reviewer",
        CannedResponse(
            review_action=('approve_review', {'review': 'latest operator scope is satisfied'})
        ),
    )
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="fixture",
            reviewer_model="fixture",
            workflow_mode="direct",
            active_vertical="software",
            role_session_policy="fresh",
            max_rounds=2,
            require_independent_review=True,
            require_post_task_learning=False,
            wiki_enabled=False,
            auto_init_wiki=False,
        ),
        extra_guidance_provider=lambda: next(guidance),
    )

    outcome = loop.run("Implement alpha and beta.", workdir=tmp_path)

    assert outcome.successful
    second_prompt = next(
        prompt
        for label, prompt, _ in backend.history
        if label == "engineer-r2"
    )
    assert second_prompt.count(reviewer_action) == 1
    assert second_prompt.count(latest_scope) == 1
    assert "Initial operator scope: implement alpha and beta." not in second_prompt
    assert second_prompt.count("## Task authority") == 1
    assert "Follow operator>objective>mission>preregistration" in second_prompt
    assert second_prompt.index(reviewer_action) < second_prompt.index(latest_scope)
