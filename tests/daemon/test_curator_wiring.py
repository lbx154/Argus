from __future__ import annotations

import threading
import time
from pathlib import Path

from argus_skill.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus_skill.life.memory import LifeMemory


def _cfg(tmp_path: Path, *, workdir: Path | None) -> LifeWorkerConfig:
    return LifeWorkerConfig(life_dir=tmp_path / "life", project_workdir=workdir,
                            backend="memory", poll_interval=0.1)


def test_build_curator_watches_project_workdir(tmp_path: Path) -> None:
    w = LifeWorker(_cfg(tmp_path, workdir=tmp_path / "proj"))
    c = w._build_curator()
    assert c is not None
    # the Curator watches the project workdir, where the lead drops .argus/team markers
    assert c.project_root == (tmp_path / "proj")


def test_build_curator_is_none_without_project_workdir(tmp_path: Path) -> None:
    w = LifeWorker(_cfg(tmp_path, workdir=None))
    assert w._build_curator() is None  # no teams without a project workspace


class _FakeResult:
    last_agent_message = "## Strategy\nkA: deepen the fused kernel"


class _FakeBackend:
    def __init__(self) -> None:
        self.labels: list[str] = []
        self.opts: list = []

    def run_exec(self, *, prompt, options, run_label):
        self.labels.append(run_label)
        self.opts.append(options)
        return _FakeResult()


def test_build_curator_distill_fn_calls_the_backend(tmp_path: Path) -> None:
    backend = _FakeBackend()
    runner = type("R", (), {})()
    runner.curator_backend = backend
    proj = tmp_path / "proj"
    c = LifeWorker(_cfg(tmp_path, workdir=proj))._build_curator(runner)
    assert c is not None and c._distill_fn is not None
    # the daemon wraps the curator backend into the Curator's (prompt)->str distill_fn
    assert c._distill_fn("PROMPT") == "## Strategy\nkA: deepen the fused kernel"
    assert "curator.distill" in backend.labels
    # distill runs CONTAINED in the project dir, so a stray agent file write can't
    # land in the daemon's cwd (a real bug a live run surfaced)
    assert backend.opts and backend.opts[0].working_dir == str(proj)


def test_build_curator_summary_uses_manager_backend_and_conversation_root(tmp_path: Path) -> None:
    backend = _FakeBackend()
    runner = type("R", (), {})()
    runner.manager_backend = backend
    project = tmp_path / "proj"
    config = _cfg(tmp_path, workdir=project)
    worker = LifeWorker(config)

    curator = worker._build_curator(runner)

    assert curator is not None
    assert curator.conversation_root == config.life_dir
    assert curator._completion_fn is not None
    assert curator._completion_fn("TEAM FACTS") == _FakeResult.last_agent_message
    assert "manager.team_summary" in backend.labels
    assert backend.opts[-1].working_dir == str(project)


def test_build_curator_no_distill_fn_without_runner(tmp_path: Path) -> None:
    c = LifeWorker(_cfg(tmp_path, workdir=tmp_path / "proj"))._build_curator()
    assert c is not None and c._distill_fn is None  # deterministic-only Curator


def test_build_curator_reads_env_knobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_TEAM_DEFAULT_WIDTH", "12")
    monkeypatch.setenv("ARGUS_TEAMMATE_TIMEOUT_S", "100")
    monkeypatch.setenv("ARGUS_TEAMMATE_HARD_GRACE_S", "20")
    c = LifeWorker(_cfg(tmp_path, workdir=tmp_path / "proj"))._build_curator()
    assert c.default_width == 12
    assert c.teammate_timeout_s == 100.0 and c.hard_grace_s == 20.0


def test_run_forever_starts_and_stops_the_curator(tmp_path: Path, monkeypatch) -> None:
    LifeMemory.open(tmp_path).init()  # empty backlog → the drain loop idles
    worker = LifeWorker(_cfg(tmp_path, workdir=None))
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    class SpyCurator:
        def __init__(self) -> None:
            self.started = self.stopped = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    spy = SpyCurator()
    monkeypatch.setattr(worker, "_build_curator", lambda *a, **k: spy)

    t = threading.Thread(target=worker.run_forever, daemon=True)
    t.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not spy.started:
        time.sleep(0.02)
    assert spy.started  # run_forever started the resident Curator

    worker._stop.set()
    t.join(timeout=10.0)
    assert not t.is_alive()
    assert spy.stopped  # ...and stopped (reaped) it on clean exit
