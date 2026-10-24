from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.life import runtime_failure_circuit as circuit_module
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.runtime_failure_circuit import (
    CIRCUIT_FILENAME,
    active_runtime_failure_circuit,
    record_runtime_failure_circuit,
)
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


def _checkpoint_error(mission_id: str) -> FileNotFoundError:
    try:
        raise FileNotFoundError(
            2,
            "No such file or directory",
            f"C:/Users/test/.argus-skill/projects/p/handoffs/{mission_id}/CHECKPOINT.md",
        )
    except FileNotFoundError as exc:
        return exc


def _identity(release: str = "release-a", source: str = "source-a") -> dict:
    return {
        "package_version": release,
        "source_revision": source,
        "relevant_source": source,
        "checkpoint_contract_version": 2,
    }


def test_callsite_is_package_relative_for_either_package_spelling() -> None:
    """Frames inside the package (either spelling) become ``argus/<path>:<function>``."""
    own = str(Path(circuit_module.__file__))
    assert circuit_module._package_relative_filename(own) == "argus/life/runtime_failure_circuit.py"
    assert circuit_module._package_relative_filename(
        "/opt/argus/argus_skill/life/supervisor/_helpers.py"
    ) == "argus/life/supervisor/_helpers.py"
    assert circuit_module._package_relative_filename(
        "C:\\Users\\a\\argus_skill\\daemon\\state.py"
    ) == "argus/daemon/state.py"
    # A checkout that merely has ``argus`` in its path is not the package.
    assert circuit_module._package_relative_filename("/home/me/argus/tests/test_x.py") is None
    # A frozen build records build-time paths that do not start with the runtime
    # package root; ``/argus/<subpackage>/`` and ``/argus/<root module>.py`` still count.
    assert circuit_module._package_relative_filename(
        "C:\\actions-runner\\_work\\Argus\\argus\\life\\supervisor\\_helpers.py"
    ) == "argus/life/supervisor/_helpers.py"
    assert circuit_module._package_relative_filename("/build/src/argus/loop.py") == "argus/loop.py"
    assert circuit_module._package_relative_filename("/home/argus/argus/core/paths.py") == "argus/core/paths.py"
    assert circuit_module._package_relative_filename("/build/src/argus/_frontend/web/dist/x.js") is None
    assert circuit_module._package_relative_filename("/build/src/argus/README.md") is None

    namespace: dict = {}
    exec(  # noqa: S102 - a frame whose filename is the pre-rename layout
        compile("def boom():\n    raise ValueError('x')\n", "/opt/argus/argus_skill/life/legacy.py", "exec"),
        namespace,
    )
    try:
        namespace["boom"]()
    except ValueError as exc:
        assert circuit_module._exception_callsite(exc) == "argus/life/legacy.py:boom"

    frozen: dict = {}
    exec(  # noqa: S102 - a frame whose filename is a build-time path of a frozen backend
        compile("def frozen_boom():\n    raise ValueError('x')\n", "C:\\build\\Argus\\argus\\daemon\\state.py", "exec"),
        frozen,
    )
    try:
        frozen["frozen_boom"]()
    except ValueError as exc:
        assert circuit_module._exception_callsite(exc) == "argus/daemon/state.py:frozen_boom"


def test_release_or_relevant_source_change_closes_circuit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity()
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", lambda: identity)
    record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))

    identity = _identity(release="release-b", source="source-b")
    assert active_runtime_failure_circuit(tmp_path) is None
    persisted = json.loads((tmp_path / CIRCUIT_FILENAME).read_text(encoding="utf-8"))
    assert persisted["active"] is False
    assert persisted["cleared_reason"] == "runtime_identity_changed"


def test_supervisor_run_stops_cleanly_instead_of_spinning_on_open_circuit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)
    record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))
    memory = LifeMemory.open(tmp_path)
    memory.init()
    memory.backlog.add(BacklogItem.new(title="held", objective="held"))

    class _Runner:
        def execute(self, **_kwargs):  # pragma: no cover - proves no dispatch
            raise AssertionError("runner must not execute")

    class _Sink:
        def handle_event(self, _event: dict) -> None:
            return None

    summary = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=2),
        ),
    ).run()

    assert summary["stopped_by"] == "runtime_block"
    assert len(summary["results"]) == 1
    assert summary["suggested_sleep"] > 0


def test_reviewed_admitted_canary_closes_open_circuit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(circuit_module, "runtime_failure_identity", _identity)
    state = record_runtime_failure_circuit(tmp_path, _checkpoint_error("aaaaaaaaaaaa"))
    # A half-open window is due: the next dispatched item is its canary. A tag
    # the circuit did not hand out is no longer a pass of its own.
    state["canary_due_at"] = time.time() - 1
    (tmp_path / CIRCUIT_FILENAME).write_text(json.dumps(state), encoding="utf-8")
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = memory.backlog.add(
        BacklogItem.new(
            title="runtime canary",
            objective="prove the repaired settlement path",
        )
    )

    class _CanaryRunner:
        def execute(self, **_kwargs):
            return SimpleNamespace(
                success=True,
                status="done",
                final_message="canary passed",
                stop_reason="",
                rounds=1,
                final_review_status="done",
                final_review_source="reviewer",
                stage_transition={},
            )

    class _Sink:
        def handle_event(self, _event: dict) -> None:
            return None

    supervisor = LifeSupervisor(
        memory=memory,
        runner=_CanaryRunner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=2),
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is True
    row = next(row for row in memory.backlog.all() if row.id == item.id)
    assert row.status == "done"
    assert "runtime_failure_canary" in row.tags
    assert active_runtime_failure_circuit(tmp_path) is None
