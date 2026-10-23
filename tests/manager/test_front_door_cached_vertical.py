from types import SimpleNamespace

from argus.life import MemoryBundle
from argus.manager.domain_author import VerticalDecision
from argus.manager.front_door import prepare_manager_execution_task


def test_active_bounded_supplement_preserves_persisted_route_contract(
    tmp_path,
) -> None:
    from argus.daemon.state import write_continuous_config

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
