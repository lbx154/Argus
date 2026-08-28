from __future__ import annotations

from argus_skill.daemon._life_worker_boot import LifeWorkerBootMixin


class _FakeWorker(LifeWorkerBootMixin):
    def __init__(self, *, reject: bool) -> None:
        self.config = object()
        self.reject = reject
        self.calls: list[str] = []

    def _rf_bootstrap_environment(self) -> None:
        self.calls.append("bootstrap")

    def _rf_vault_preflight(self, _state):
        self.calls.append("readiness")
        return 2 if self.reject else None

    def _rf_build_memory_runner_sink(self, _state) -> None:
        self.calls.append("build_runner")

    def _rf_resolve_continuous_boot_state(self, _state) -> None:
        self.calls.append("continuous")

    def _rf_manager_divide_on_boot(self, _state) -> None:
        self.calls.append("manager")

    def _rf_build_supervisor(self, _state) -> None:
        self.calls.append("supervisor")

    def _rf_start_services(self, _state) -> None:
        self.calls.append("services")

    def _rf_main_loop(self, _state) -> None:
        self.calls.append("loop")

    def _rf_shutdown(self, _state) -> int:
        self.calls.append("shutdown")
        return 0


def test_failed_readiness_precedes_provider_and_state_construction() -> None:
    worker = _FakeWorker(reject=True)

    assert worker.run_forever() == 2
    assert worker.calls == ["bootstrap", "readiness"]


def test_successful_readiness_precedes_manager_and_continuous_state() -> None:
    worker = _FakeWorker(reject=False)

    assert worker.run_forever() == 0
    assert worker.calls[:5] == [
        "bootstrap",
        "readiness",
        "build_runner",
        "continuous",
        "manager",
    ]
