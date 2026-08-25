"""codex live web_search (idea-stage) wiring — the ``web_search="live"`` feature.

Idea discovery runs in the research stage; there the engineer enables codex's
native live web_search so literature grounding is real, not cached/recalled.
These tests pin the four links of the chain:
  1. RunnerOptions.live_search -> ``-c web_search="live"`` in the codex command
  2. the core->agent_cli options translation carries the flag
  3. the research-stage gate turns it on for research, off elsewhere
  4. the ACTIVE VERTICAL owns which of its own stages get it; the framework
     default (research) only applies to a vertical that declares nothing
"""
from __future__ import annotations

import inspect
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
from argus_skill.agent_cli.agent_cli_runner import RunnerOptions as AcOpts
from argus_skill.core.models import RunnerOptions as CoreOpts
from argus_skill.core.vertical_contract import VerticalContractError
from argus_skill.engineer.runner import (
    DEFAULT_LIVE_SEARCH_STAGES,
    EngineerConfig,
    _engineer_live_search,
)
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical_contract


def _cmd(live: bool) -> list[str]:
    r = AgentCliRunner(agent_bin="codex")
    return r._build_codex_command(
        resume_thread_id=None,
        options=AcOpts(model="gpt-5.5", live_search=live, full_auto=True),
    )


def test_search_flag_present_only_when_live():
    assert any('web_search="live"' in a for a in _cmd(True))
    assert not any('web_search="live"' in a for a in _cmd(False))


def test_core_and_agentcli_both_have_field():
    assert CoreOpts(model="gpt-5.5", live_search=True).live_search is True
    assert CoreOpts().live_search is False  # default off
    assert "live_search" in AcOpts.__dataclass_fields__


def test_engineer_config_public_live_search_default_is_compatible() -> None:
    config = EngineerConfig(model="test")

    assert config.live_search_stages == frozenset({"research"})
    assert type(config.live_search_stages) is frozenset
    assert (
        EngineerConfig.__dataclass_fields__["live_search_stages"].default
        is DEFAULT_LIVE_SEARCH_STAGES
    )
    assert (
        inspect.signature(EngineerConfig).parameters["live_search_stages"].default
        is DEFAULT_LIVE_SEARCH_STAGES
    )


def test_assigning_framework_default_marks_live_search_as_explicit() -> None:
    config = EngineerConfig(model="test")
    assert config._live_search_stages_explicit is False

    config.live_search_stages = DEFAULT_LIVE_SEARCH_STAGES

    assert config.live_search_stages is DEFAULT_LIVE_SEARCH_STAGES
    assert config._live_search_stages_explicit is True


def test_replace_of_unrelated_field_preserves_live_search_provenance() -> None:
    omitted = EngineerConfig(model="test")
    omitted_update = replace(omitted, reasoning_effort="high")
    assert omitted_update.live_search_stages is DEFAULT_LIVE_SEARCH_STAGES
    assert omitted_update._live_search_stages_explicit is False

    custom_stages = frozenset({"scope"})
    explicit = EngineerConfig(model="test", live_search_stages=custom_stages)
    explicit_update = replace(explicit, reasoning_effort="high")
    assert explicit_update.live_search_stages is custom_stages
    assert explicit_update._live_search_stages_explicit is True


def test_replace_of_live_search_field_marks_it_explicit() -> None:
    config = EngineerConfig(model="test")
    custom_stages = frozenset({"optimize"})

    updated = config.replace(live_search_stages=custom_stages)

    assert updated.live_search_stages is custom_stages
    assert updated._live_search_stages_explicit is True


def test_replace_of_live_search_field_with_default_marks_it_explicit() -> None:
    config = EngineerConfig(model="test")

    updated = config.replace(live_search_stages=DEFAULT_LIVE_SEARCH_STAGES)

    assert updated.live_search_stages is DEFAULT_LIVE_SEARCH_STAGES
    assert updated._live_search_stages_explicit is True


def test_stage_gate_research_on_others_off():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "research"), exist_ok=True)
    stages = frozenset({"research"})

    def _set(stage: str) -> None:
        with open(os.path.join(d, "research", "PIPELINE_STATE.json"), "w") as fh:
            json.dump({"current_stage": stage}, fh)

    _set("research")
    assert _engineer_live_search(d, stages) is True
    _set("plan")
    assert _engineer_live_search(d, stages) is False
    _set("run")
    assert _engineer_live_search(d, stages) is False


def test_stage_gate_empty_stages_never_on():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "research"), exist_ok=True)
    with open(os.path.join(d, "research", "PIPELINE_STATE.json"), "w") as fh:
        json.dump({"current_stage": "research"}, fh)
    assert _engineer_live_search(d, frozenset()) is False


# --- the active vertical owns its own live-search stages -------------------


def _resolved_stages(vertical: str, project_root: object = None) -> frozenset[str]:
    """Resolve exactly what SkillLoop puts into EngineerConfig for a vertical."""
    contract = load_vertical_contract(vertical, project_root=project_root)
    return contract.live_search_stages(DEFAULT_LIVE_SEARCH_STAGES)


def _math_project(tmp_path: Path, stage: str) -> Path:
    persist_vertical(tmp_path, "math")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["current_stage"] = stage
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def _kernel_project(tmp_path: Path) -> Path:
    persist_vertical(tmp_path, "kernel_engineering")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["current_stage"] = "optimize"
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_every_math_stage_gets_live_search(tmp_path: Path) -> None:
    """Current literature, official implementations and provider behaviour can
    change while solving and while reviewing, not only while scoping."""
    contract = load_vertical_contract("math")
    assert contract.engineer_live_search_stages is None
    stages = _resolved_stages("math")
    assert stages == frozenset(contract.stage_order)
    for index, stage in enumerate(contract.stage_order):
        assert _engineer_live_search(
            _math_project(tmp_path / str(index), stage), stages
        ) is True


def test_every_research_stage_gets_live_search(tmp_path: Path) -> None:
    contract = load_vertical_contract("research")
    assert contract.engineer_live_search_stages is None
    stages = _resolved_stages("research")
    assert stages == frozenset(contract.stage_order)

    persist_vertical(tmp_path, "research")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    for stage in contract.stage_order:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        payload["current_stage"] = stage
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        assert _engineer_live_search(tmp_path, stages) is True


def test_vertical_without_declaration_takes_the_default_path(tmp_path: Path) -> None:
    """Every other in-tree vertical keeps the framework default untouched."""
    for vertical in ("software", "physics", "speedrun", "argus_maintenance"):
        contract = load_vertical_contract(vertical)
        assert contract.engineer_live_search_stages is None, vertical
        assert contract.live_search_stages(DEFAULT_LIVE_SEARCH_STAGES) == (
            frozenset(contract.stage_order)
        ), vertical

    # A software project searches in delivery too: current dependencies,
    # provider behaviour and official docs do not stop changing after scope.
    persist_vertical(tmp_path, "software")
    assert _engineer_live_search(tmp_path, _resolved_stages("software")) is True


def _done_review() -> CannedResponse:
    return CannedResponse(message=json.dumps({
        "status": "done",
        "reason": "Verified.",
        "next_action": "None.",
        "round_summary_markdown": "# Review\n\n- verified\n",
        "completion_summary_markdown": "Verified.",
    }))


def _build_loop(
    tmp_path: Path,
    vertical: str,
    *,
    live_search_stages: frozenset[str] | None = None,
    vertical_state_root: Path | None = None,
) -> tuple[SkillLoop, MemoryBackend]:
    """Build a real SkillLoop, optionally with a caller-configured baseline."""
    skills = tmp_path / "skills"
    skills.mkdir()
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Worked and verified."))
    backend.queue("reviewer", _done_review())
    loop = SkillLoop(
        skills_dir=skills,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=1,
            workflow_mode="direct",
            active_vertical=vertical,
            vertical_state_root=vertical_state_root,
        ),
    )
    if live_search_stages is not None:
        # Exactly what a caller does through the public EngineerConfig knob.
        loop.supervised.engineer_config.live_search_stages = live_search_stages
    return loop, backend


def _engineer_options(backend: MemoryBackend) -> CoreOpts:
    return next(
        options for label, _prompt, options in backend.history
        if label == "engineer-r1"
    )


def _run_one_round(
    tmp_path: Path,
    vertical: str,
    *,
    live_search_stages: frozenset[str] | None = None,
    vertical_state_root: Path | None = None,
    work_kind: str = "",
    task: str = "Do the work.",
) -> CoreOpts:
    """Run one real SkillLoop round and return the engineer's RunnerOptions."""
    loop, backend = _build_loop(
        tmp_path,
        vertical,
        live_search_stages=live_search_stages,
        vertical_state_root=vertical_state_root,
    )
    loop.run(
        task,
        workdir=tmp_path,
        scope="bounded",
        work_kind=work_kind,
    )
    return _engineer_options(backend)


_KERNEL_KEYWORD_NOISE = (
    "Algorithm discovery and optimization are mentioned identically; use live search "
    "only when the persisted mission structure permits it."
)


@pytest.mark.parametrize(
    ("work_kind", "expected"),
    [
        ("algorithm_discovery", True),
        ("engineering_optimization", True),
        ("", True),
    ],
)
def test_kernel_skill_loop_keeps_search_for_every_work_kind(
    tmp_path: Path,
    work_kind: str,
    expected: bool,
) -> None:
    """Same optimize stage and prose; only mission.work_kind may change routing."""
    _kernel_project(tmp_path)

    options = _run_one_round(
        tmp_path,
        "kernel_engineering",
        work_kind=work_kind,
        task=_KERNEL_KEYWORD_NOISE,
    )

    assert options.live_search is expected


def test_separated_state_root_enables_kernel_algorithm_discovery(
    tmp_path: Path,
) -> None:
    """The contract and stage gate must read the same production state root."""
    state_root = _kernel_project(tmp_path / "state")
    execution_root = tmp_path / "execution"
    persist_vertical(execution_root, "research")

    options = _run_one_round(
        execution_root,
        "kernel_engineering",
        vertical_state_root=state_root,
        work_kind="algorithm_discovery",
    )

    # State is optimize, while the execution tree is research. Reading the
    # execution tree would incorrectly disable the optimize-only declaration.
    assert options.live_search is True


@pytest.mark.parametrize("work_kind", ["engineering_optimization", ""])
def test_separated_state_root_keeps_other_kernel_work_kinds_searchable(
    tmp_path: Path,
    work_kind: str,
) -> None:
    state_root = _kernel_project(tmp_path / "state")
    execution_root = tmp_path / "execution"
    persist_vertical(execution_root, "research")

    options = _run_one_round(
        execution_root,
        "kernel_engineering",
        vertical_state_root=state_root,
        work_kind=work_kind,
    )

    # The state root still decides the stage, but search is available throughout
    # the vertical regardless of which kind this particular mission carries.
    assert options.live_search is True


@pytest.mark.parametrize(
    ("work_kind", "custom", "expected"),
    [
        ("engineering_optimization", frozenset({"optimize"}), True),
        ("algorithm_discovery", frozenset(), False),
    ],
)
def test_kernel_work_kind_default_does_not_replace_caller_live_search_config(
    tmp_path: Path,
    work_kind: str,
    custom: frozenset[str],
    expected: bool,
) -> None:
    _kernel_project(tmp_path)

    options = _run_one_round(
        tmp_path,
        "kernel_engineering",
        live_search_stages=custom,
        work_kind=work_kind,
        task=_KERNEL_KEYWORD_NOISE,
    )

    assert options.live_search is expected


def test_explicit_framework_default_keeps_caller_provenance(tmp_path: Path) -> None:
    """An explicit value remains explicit even when equal to the default."""
    _kernel_project(tmp_path)

    options = _run_one_round(
        tmp_path,
        "kernel_engineering",
        live_search_stages=frozenset({"research"}),
        work_kind="algorithm_discovery",
    )

    assert options.live_search is False


def test_standard_replace_custom_live_search_overrides_kernel_work_kind_default(
    tmp_path: Path,
) -> None:
    _kernel_project(tmp_path)
    loop, backend = _build_loop(tmp_path, "kernel_engineering")
    loop.supervised.engineer_config = replace(
        loop.supervised.engineer_config,
        live_search_stages=frozenset({"optimize"}),
    )

    loop.run(
        "Do the work.",
        workdir=tmp_path,
        scope="bounded",
        work_kind="engineering_optimization",
    )

    assert loop.supervised.engineer_config._live_search_stages_explicit is True
    assert _engineer_options(backend).live_search is True


def test_replaced_live_search_config_overrides_kernel_work_kind_default(
    tmp_path: Path,
) -> None:
    _kernel_project(tmp_path)
    loop, backend = _build_loop(tmp_path, "kernel_engineering")
    loop.supervised.engineer_config = loop.supervised.engineer_config.replace(
        live_search_stages=frozenset(),
    )

    loop.run(
        "Do the work.",
        workdir=tmp_path,
        scope="bounded",
        work_kind="algorithm_discovery",
    )

    assert _engineer_options(backend).live_search is False


def test_replaced_default_live_search_config_overrides_kernel_work_kind_default(
    tmp_path: Path,
) -> None:
    _kernel_project(tmp_path)
    loop, backend = _build_loop(tmp_path, "kernel_engineering")
    loop.supervised.engineer_config = loop.supervised.engineer_config.replace(
        live_search_stages=DEFAULT_LIVE_SEARCH_STAGES,
    )

    loop.run(
        "Do the work.",
        workdir=tmp_path,
        scope="bounded",
        work_kind="algorithm_discovery",
    )

    assert _engineer_options(backend).live_search is False


def test_skill_loop_hands_math_engineer_live_search_in_solve(tmp_path: Path) -> None:
    """End-to-end: the declaration actually reaches the codex invocation."""
    _math_project(tmp_path, "solve")

    assert _run_one_round(tmp_path, "math").live_search is True


def test_skill_loop_keeps_research_engineer_behaviour(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["current_stage"] = "research"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    assert _run_one_round(tmp_path, "research").live_search is True


def test_skill_loop_gives_undeclared_vertical_search_in_its_stage(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "software")

    assert _run_one_round(tmp_path, "software").live_search is True


def test_undeclared_vertical_preserves_a_caller_configured_live_search_set(
    tmp_path: Path,
) -> None:
    """A vertical that declares nothing must not lose the CALLER's setting.

    The baseline for an undeclared vertical is whatever is already configured on
    ``EngineerConfig`` — not the framework constant. Resolving against the
    constant would silently downgrade an explicit caller choice to
    ``{"research"}``, which for a ``delivery``-stage project means turning live
    search off entirely.
    """
    persist_vertical(tmp_path, "software")
    custom = frozenset({"delivery"})

    options = _run_one_round(tmp_path, "software", live_search_stages=custom)

    # The custom set survives and actually reaches the codex invocation;
    # collapsing to the framework default would make this False.
    assert options.live_search is True


def test_mission_never_mutates_the_shared_supervised_engineer(
    tmp_path: Path,
) -> None:
    """Per-mission live-search resolution must not write back onto the loop.

    ``SupervisedEngineer`` is shared across missions and documents itself as
    stateless across calls; a mission-scoped value written onto it outlives the
    mission (and any exception during the mission leaves it there for good).
    """
    _math_project(tmp_path, "solve")
    custom = frozenset({"scope"})
    loop, backend = _build_loop(tmp_path, "math", live_search_stages=custom)
    shared_before = loop.supervised
    config_before = loop.supervised.engineer_config

    loop.run("Do the work.", workdir=tmp_path, scope="bounded")

    # An explicit caller policy is an opt-out from the vertical default, and
    # solve is outside this caller's scope-only set.
    assert _engineer_options(backend).live_search is False
    # ...yet nothing was written back onto the shared object.
    assert loop.supervised is shared_before
    assert loop.supervised.engineer_config is config_before
    assert loop.supervised.engineer_config.live_search_stages == custom


def test_unknown_vertical_fails_loudly_instead_of_defaulting(tmp_path: Path) -> None:
    """A vertical that cannot be loaded is an inconsistency, not a missing knob.

    ``resolve_role_prompt`` loads this exact contract for the same mission
    moments earlier, so a failure here means the two disagree. Falling back
    would run the Engineer with silently wrong permissions.
    """
    loop, _backend = _build_loop(tmp_path, "software")

    with pytest.raises(LookupError, match="unknown vertical"):
        loop._resolve_live_search_stages(
            "definitely_not_a_vertical_xyz",
            tmp_path,
            DEFAULT_LIVE_SEARCH_STAGES,
        )


def test_contract_validation_error_is_not_swallowed(tmp_path: Path) -> None:
    """The fail-loud property added to ``vertical_contract`` must survive here.

    A vertical whose declaration does not typecheck already raises at load
    time; this pins that the loop does not catch it and degrade to a default.
    The contract is patched directly because ``resolve_role_prompt`` would
    otherwise raise on the very same bad provider before the loop reaches this
    call — which is exactly the inconsistency the propagation protects.
    """
    from argus_skill.verticals import _base as verticals_base

    loop, _backend = _build_loop(tmp_path, "software")

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise VerticalContractError("vertical 'x' declares a blank live search stage")

    original = verticals_base.load_vertical_contract
    verticals_base.load_vertical_contract = _explode
    try:
        with pytest.raises(VerticalContractError, match="blank live search stage"):
            loop._resolve_live_search_stages(
                "software",
                tmp_path,
                DEFAULT_LIVE_SEARCH_STAGES,
            )
    finally:
        verticals_base.load_vertical_contract = original
