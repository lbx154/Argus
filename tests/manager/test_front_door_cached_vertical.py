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
    calls: list[str] = []

    def decide_vertical(body: str, **_kwargs):
        calls.append(body)
        return VerticalDecision(
            choice="existing",
            vertical="math",
            workflow_mode="staged",
            execution_task=body,
            research_target_level="publishable",
        )

    manager = SimpleNamespace(project_root=tmp_path, decide_vertical=decide_vertical)
    state = {
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
    assert calls == ["持续证明一个未解决的 Erdős 问题"]
