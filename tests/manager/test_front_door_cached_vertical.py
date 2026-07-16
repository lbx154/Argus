from types import SimpleNamespace

from argus_skill.life import MemoryBundle
from argus_skill.manager.front_door import prepare_manager_execution_task


def test_manager_handoff_reuses_frontdoor_builtin_vertical(tmp_path) -> None:
    memory = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-fast-vertical",
    )
    memory.init()
    manager = SimpleNamespace(
        project_root=tmp_path,
        decide_vertical=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("obvious built-in vertical must not be classified twice")
        ),
    )
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
    assert "_frontdoor_vertical" not in state
