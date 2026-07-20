from __future__ import annotations

from argus_skill.manager._core import Manager
from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.skills.vertical_select import VERTICALS, VERTICAL_PURPOSES
from argus_skill.verticals._base import load_vertical, vertical_role_banner


def test_benchmark_subvertical_is_registered_and_direct() -> None:
    assert "digital_circuit_benchmark" in VERTICALS
    assert "single-stage" in VERTICAL_PURPOSES["digital_circuit_benchmark"]
    mod = load_vertical("digital_circuit_benchmark")
    assert mod.STAGE_ORDER == ("execute",)
    assert mod.CHECKLIST_STAGE_ORDER == ("execute",)
    assert mod.WORKFLOW_MODE == "direct"
    assert tuple(mod.STAGE_CHECKS) == ("execute",)
    assert tuple(mod.REVIEWER_CHECKLISTS) == ("execute",)
    assert Manager._kind_for("digital_circuit_benchmark") == "custom"
    assert mod.__name__ == "argus_skill.verticals.digital_circuit.benchmark.stages"


def test_benchmark_subvertical_inherits_digital_circuit_skills() -> None:
    skills = dict(iter_vertical_skill_texts("digital_circuit_benchmark"))
    assert "engineer/digital-circuit-first-pass-contract-closure.md" in skills
    assert "engineer/digital-circuit-error-guided-repair.md" in skills
    assert "reviewer/digital-circuit-benchmark-review.md" in skills
    assert "reviewer/digital-circuit-guidance-promotion-review.md" in skills


def test_benchmark_role_banner_forbids_staged_overhead() -> None:
    mod = load_vertical("digital_circuit_benchmark")
    for role in ("planner", "engineer", "reviewer"):
        banner = vertical_role_banner(mod, role)
        assert "BENCHMARK SUBVERTICAL" in banner
        assert "ONE bounded execute mission" in banner
        assert "Do not create or wait for separate specification" in banner
