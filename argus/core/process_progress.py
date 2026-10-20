"""Whether a process tree is doing work: a live process alone is not proof of it.

A command that only computes (a build, a training step) prints nothing until
it ends, but its CPU time grows; a hung network read or a deadlock does not.
"""
from __future__ import annotations

# The process tree is sampled this often, and this much CPU time between two
# samples counts as progress.
CPU_SAMPLE_SECONDS = 30.0
CPU_PROGRESS_SECONDS = 1.0


def process_tree_cpu_seconds(pid: int | None) -> float:
    """User plus system CPU seconds of ``pid`` and its descendants; 0 once gone."""
    import psutil

    try:
        root = psutil.Process(int(pid))
        processes = [root, *root.children(recursive=True)]
    except (psutil.Error, TypeError, ValueError):
        return 0.0
    total = 0.0
    for process in processes:
        try:
            times = process.cpu_times()
        except psutil.Error:
            continue
        # Reaped children's time stays with their parent, so a finished
        # step does not make the tree look idle.
        total += sum(getattr(times, name, 0.0) for name in (
            "user", "system", "children_user", "children_system",
        ))
    return total


__all__ = ["CPU_PROGRESS_SECONDS", "CPU_SAMPLE_SECONDS", "process_tree_cpu_seconds"]
