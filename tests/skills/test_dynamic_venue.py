"""Dynamic (researched) venue support: a non-built-in target venue gets a
project-local VENUE_PROFILE.json, loaded by resolve_venue_profile and graded via
the stage-checklist venue rendering — without EMNLP/ACL leakage, and without
disturbing the built-in EMNLP/AAAI venues."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.loop import SkillLoop, SkillLoopConfig
from argus_skill.skills.venue_profiles import (
    AAAI_PROFILE,
    EMNLP_PROFILE,
    FRONTIERS_SLEEP_PROFILE,
    VenueProfile,
    is_builtin_venue,
    load_local_venue_profile,
    resolve_venue_profile,
    venue_profile_path,
    write_venue_profile,
)
from argus_skill.skills.venue_research import (
    needs_venue_research,
    research_venue_profile,
)


def _neurips() -> VenueProfile:
    return VenueProfile(
        key="NEURIPS",
        display_name="NeurIPS 2026",
        body_page_limit=9,
        conclusion_underfill_page=8,
        conclusion_max_page=9,
        references_min_page=10,
        style_package="neurips_2026",
        style_files=("neurips_2026.sty",),
        reviewer_persona="NeurIPS",
        figure_style_persona="NeurIPS",
        mandatory_end_sections=(),
    )


def _project(tmp_path: Path, target_venue: str) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research", "target_venue": target_venue})
    )
    return tmp_path


def _research_project_without_target(tmp_path: Path) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "vertical": "research",
            "current_stage": "research",
        }),
        encoding="utf-8",
    )
    return tmp_path


# ---- (de)serialization ----------------------------------------------------

@pytest.mark.parametrize(
    "profile", [EMNLP_PROFILE, AAAI_PROFILE, FRONTIERS_SLEEP_PROFILE]
)
def test_venue_profile_json_round_trip(profile):
    rt = VenueProfile.from_dict(json.loads(json.dumps(profile.to_dict())))
    assert rt == profile


def test_from_dict_requires_key_and_pages():
    with pytest.raises(ValueError):
        VenueProfile.from_dict({"display_name": "X 2026"})  # missing key
    with pytest.raises(ValueError):
        VenueProfile.from_dict("not a dict")


def test_write_and_load_local_profile(tmp_path):
    p = write_venue_profile(tmp_path, _neurips())
    assert p == venue_profile_path(tmp_path) and p.is_file()
    assert load_local_venue_profile(tmp_path) == _neurips()


def test_corrupt_local_profile_is_ignored(tmp_path):
    venue_profile_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    venue_profile_path(tmp_path).write_text("{ not json")
    assert load_local_venue_profile(tmp_path) is None  # fail-soft


# ---- resolution precedence ------------------------------------------------

def test_is_builtin_venue():
    assert is_builtin_venue("EMNLP") and is_builtin_venue("ACL")  # alias
    assert is_builtin_venue("aaai2026")  # variant
    assert not is_builtin_venue("NeurIPS") and not is_builtin_venue("")


def test_local_profile_beats_target_venue(tmp_path):
    root = _project(tmp_path, "NeurIPS")
    write_venue_profile(root, _neurips())
    resolved = resolve_venue_profile(root)
    assert resolved.key == "NEURIPS" and resolved.body_page_limit == 9


def test_unknown_venue_without_profile_fails_closed(tmp_path):
    root = _project(tmp_path, "NeurIPS")  # no VENUE_PROFILE.json
    with pytest.raises(KeyError, match="matched no known profile"):
        resolve_venue_profile(root)


# ---- research hook (mock runner, no network) ------------------------------

def test_needs_venue_research(tmp_path):
    assert needs_venue_research(_project(tmp_path / "a", "NeurIPS")) is True
    assert needs_venue_research(_project(tmp_path / "b", "AAAI")) is False
    missing = tmp_path / "missing"
    (missing / "research").mkdir(parents=True)
    (missing / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research"}), encoding="utf-8"
    )
    assert needs_venue_research(missing) is True
    cached = _project(tmp_path / "c", "NeurIPS")
    write_venue_profile(cached, _neurips())
    assert needs_venue_research(cached) is False


def test_research_venue_profile_writes_and_resolves(tmp_path):
    root = _project(tmp_path, "ICML")

    class MockRunner:
        def run_exec(self, *, prompt, options, run_label):
            assert options.live_search is True and run_label == "venue-research"
            assert options.working_dir == str(root.resolve())
            write_venue_profile(
                root,
                VenueProfile(
                    key="ICML", display_name="ICML 2026", body_page_limit=8,
                    conclusion_underfill_page=7, conclusion_max_page=8,
                    references_min_page=9, reviewer_persona="ICML",
                ),
            )
            return type("R", (), {"exit_code": 0, "agent_messages": ["done"]})()

    assert research_venue_profile(MockRunner(), root) is True
    assert resolve_venue_profile(root).key == "ICML"


def test_research_venue_profile_without_target_requests_open_ccf_a_selection(
    tmp_path,
):
    root = tmp_path
    (root / "research").mkdir(parents=True)
    (root / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research"}), encoding="utf-8"
    )

    class MockRunner:
        def run_exec(self, *, prompt, options, run_label):
            assert "CCF-A" in prompt
            assert "deadline has not passed" in prompt
            assert "VENUE_SELECTION.md" in prompt
            write_venue_profile(root, _neurips())
            return type("R", (), {"exit_code": 0, "agent_messages": ["done"]})()

    assert research_venue_profile(MockRunner(), root) is True


def test_research_venue_profile_fail_open(tmp_path):
    root = _project(tmp_path, "ICML")

    class BadRunner:
        def run_exec(self, **_):
            raise RuntimeError("boom")

    # never raises; no profile produced -> False
    assert research_venue_profile(BadRunner(), root) is False
    assert load_local_venue_profile(root) is None


def test_failed_runner_result_remains_retryable(tmp_path):
    root = _project(tmp_path, "ICML")

    class FailedRunner:
        def run_exec(self, **_):
            return type(
                "R",
                (),
                {
                    "exit_code": 1,
                    "fatal_error": "provider unavailable",
                    "agent_messages": [],
                },
            )()

    assert research_venue_profile(FailedRunner(), root) is False
    assert needs_venue_research(root) is True
    assert not (root / "research" / "VENUE_RESEARCH_ATTEMPT.json").exists()


def test_completed_failed_selection_is_not_retried_every_mission(tmp_path):
    root = tmp_path
    (root / "research").mkdir(parents=True)
    (root / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research"}), encoding="utf-8"
    )

    class NoProfileRunner:
        calls = 0

        def run_exec(self, **_):
            self.calls += 1
            return type("R", (), {"exit_code": 0, "agent_messages": ["no open venue"]})()

    runner = NoProfileRunner()
    assert research_venue_profile(runner, root) is False
    assert runner.calls == 1
    assert needs_venue_research(root) is False
    assert research_venue_profile(runner, root) is False
    assert runner.calls == 1

    attempt = json.loads(
        (root / "research" / "VENUE_RESEARCH_ATTEMPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert attempt["provider_call_completed"] is True
    assert attempt["profile_created"] is False


def test_explicit_builtin_venue_overrides_stale_dynamic_profile(tmp_path):
    root = _project(tmp_path, "AAAI")
    write_venue_profile(root, _neurips())

    assert resolve_venue_profile(root).key == "AAAI"
    assert needs_venue_research(root) is False


def test_changed_dynamic_target_requires_a_matching_profile(tmp_path):
    root = _project(tmp_path, "ICML")
    write_venue_profile(root, _neurips())

    assert needs_venue_research(root) is True
    with pytest.raises(KeyError):
        resolve_venue_profile(root)


def test_skill_loop_researches_venue_before_matcher_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _research_project_without_target(tmp_path / "project")
    monkeypatch.setenv("ARGUS_SKILL_IDEA_SEARCH", "0")
    monkeypatch.delenv("ARGUS_SKILL_VENUE", raising=False)

    class DummyRunner:
        def run_exec(self, **_kwargs):
            return RunnerResult(exit_code=0, agent_messages=["ok"])

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=DummyRunner(),
        config=SkillLoopConfig(
            paper_mission=True,
            skill_adapter_enabled=False,
            wiki_enabled=False,
            auto_compact_enabled=False,
        ),
    )
    observed: dict[str, object] = {}

    def fake_research_venue_profile(_runner, workdir, *, model="gpt-5.5") -> bool:
        observed["research_called"] = True
        assert not venue_profile_path(Path(workdir)).exists()
        write_venue_profile(Path(workdir), AAAI_PROFILE)
        return True

    monkeypatch.setattr(
        "argus_skill.skills.venue_research.research_venue_profile",
        fake_research_venue_profile,
    )

    def fake_select(task, *, extra_exclude, force_empty_match):
        observed["profile_exists_before_match"] = venue_profile_path(root).is_file()
        observed["excluded"] = set(extra_exclude)
        return SimpleNamespace(
            primary=None,
            primary_skills=[],
            reference_skills=[],
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            premium_requests=0.0,
            reasoning_output_tokens=0,
        )

    loop.skill_router.select = fake_select  # type: ignore[method-assign]
    loop.supervised.run = lambda **_kwargs: ("done", [], "ok", "review_done", None)  # type: ignore[method-assign]

    outcome = loop.run("draft the selected venue paper", workdir=root)

    assert outcome.successful
    assert observed["research_called"] is True
    assert observed["profile_exists_before_match"] is True
    excluded = observed["excluded"]
    assert isinstance(excluded, set)
    assert "emnlp-paper-drafting.md" in excluded
    assert "aaai-paper-drafting.md" not in excluded
