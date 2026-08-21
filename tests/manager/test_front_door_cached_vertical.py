from types import SimpleNamespace

from argus_skill.life import MemoryBundle
from argus_skill.manager.domain_author import VerticalDecision
from argus_skill.manager.front_door import prepare_manager_execution_task


def test_manager_handoff_always_calls_manager_vertical_classifier(tmp_path) -> None:
    memory = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-fast-vertical",
    )
    memory.init()
    calls: list[tuple[str, dict]] = []

    def decide_vertical(body: str, **kwargs):
        calls.append((body, kwargs))
        return VerticalDecision(
            choice="existing",
            vertical="math",
            workflow_mode="staged",
            execution_task=body,
            research_target_level="publishable",
        )

    manager = SimpleNamespace(project_root=tmp_path, decide_vertical=decide_vertical)
    state = {
        "_frontdoor_lifetime": "standing",
        "_frontdoor_vertical": {
            "vertical": "math",
            "target": "publishable",
        }
    }

    prepared = prepare_manager_execution_task(
        memory,
        "持续证明一个未解决的 Erdős 问题",
        state,
        ensure_runner=lambda *_args: SimpleNamespace(manager=manager),
    )

    assert prepared.decision.vertical == "math"
    assert prepared.decision.research_target_level == "publishable"
    assert prepared.execution_task == "持续证明一个未解决的 Erdős 问题"
    assert prepared.lifetime == "standing"
    assert calls == [(
        "持续证明一个未解决的 Erdős 问题",
        {"allow_route_contract_change": True},
    )]


def test_active_bounded_supplement_preserves_persisted_route_contract(
    tmp_path,
) -> None:
    from argus_skill.daemon.state import write_continuous_config

    memory = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-preserve-route",
    )
    memory.init()
    write_continuous_config(
        memory.project.root,
        enabled=True,
        objective="standing research campaign",
    )
    seen: dict[str, object] = {}

    def decide_vertical(body: str, **kwargs):
        seen.update(kwargs)
        return VerticalDecision(
            choice="existing",
            vertical="research",
            workflow_mode="staged",
            execution_task=body,
            research_target_level="publishable",
        )

    manager = SimpleNamespace(project_root=tmp_path, decide_vertical=decide_vertical)
    prepared = prepare_manager_execution_task(
        memory,
        "run one finite supplemental check",
        {"_frontdoor_lifetime": "bounded"},
        ensure_runner=lambda *_args: SimpleNamespace(manager=manager),
    )

    assert prepared.execution_task == "run one finite supplemental check"
    assert seen == {"allow_route_contract_change": False}
