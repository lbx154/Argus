"""Environment forwarding contract; native process lifecycle is tested separately."""
from types import SimpleNamespace

from argus_skill.core import windows_job


def test_explicit_empty_environment_does_not_reintroduce_host_credentials(monkeypatch):
    class Job:
        name = "fixture-job"

        def assign(self, process):
            pass

        def resume(self, process):
            pass

    monkeypatch.setattr(
        windows_job, "os", SimpleNamespace(name="nt", environ={"HOST_SECRET": "must-not-leak"}),
    )
    monkeypatch.setattr(windows_job, "_WindowsTurnJob", Job)
    seen = []

    def spawn(command, **kwargs):
        seen.append(kwargs["env"])
        return SimpleNamespace()

    windows_job.spawn_owned_process(["fixture"], popen_factory=spawn, env={})
    assert seen == [{windows_job._JOB_ENV: "fixture-job"}]
