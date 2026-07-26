"""Retiring factory copies of builtins this release no longer ships.

Seeding only ever adds files, so a skill that is deleted or relocated upstream
survives in every already-initialised runtime layer and keeps costing matcher
tokens on every single match. These tests pin the two properties that make the
cleanup safe to run automatically: it removes exactly the bodies we shipped,
and it never touches anything an agent has edited.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from argus_skill.skills import builtins as builtins_module
from argus_skill.skills.builtins import (
    _RETIRED_BUILTIN_DIGESTS,
    iter_builtin_skill_texts,
    iter_context_skill_texts,
    retire_orphaned_builtin_seeds,
)

# Domain playbooks that used to sit in the flat global engineer pool, where a
# maths or paper-writing project paid for them on every match.
MOVED_TO_VERTICALS = {
    "engineer/b200-kernelbench-runtime.md": "kernelbench",
    "engineer/official-sol-execbench-env.md": "kernelbench",
    "engineer/sol-kernel-sota-optimization.md": "kernelbench",
    "engineer/sol-kernel-hands-on-trace.md": "kernelbench",
    "engineer/kernel-optimization-knowledge.md": "kernel_engineering",
    "engineer/kernel-optimization-process-trace.md": "kernel_engineering",
    "engineer/kernel-benchmark-measurement-integrity.md": "kernel_engineering",
    "engineer/modern-gpu-blackwell-kernel-techniques.md": "kernel_engineering",
    "engineer/nanochat-pretrain-runner.md": "nanochat",
    "engineer/nanochat-autoresearch-sota-optimization.md": "nanochat",
    "engineer/nanochat-autoresearch-hands-on-trace.md": "nanochat",
    "engineer/nanogpt-speedrun-h100-sota.md": "nanogpt_speedrun",
    "engineer/speedrun-sota-optimization.md": "speedrun",
    "engineer/speedrun-hands-on-trace.md": "speedrun",
}


def _seed(root: Path, relative: str, body: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _shipped_body(relative: str) -> str:
    """The exact body whose digest is pinned for ``relative``."""
    vertical = MOVED_TO_VERTICALS[relative]
    return dict(iter_context_skill_texts(vertical, None))[relative]


def test_retired_paths_are_no_longer_shipped() -> None:
    # A stale entry would be worse than useless: it would delete a file the
    # current release still wants.
    current = {name for name, _text in iter_builtin_skill_texts()}
    assert not (current & set(_RETIRED_BUILTIN_DIGESTS))


def test_pinned_digest_matches_the_relocated_body() -> None:
    # The move must be byte-preserving, otherwise the pinned digest silently
    # stops matching and every existing install keeps the orphan forever.
    for relative in MOVED_TO_VERTICALS:
        digest = hashlib.sha256(_shipped_body(relative).encode()).hexdigest()
        assert digest in _RETIRED_BUILTIN_DIGESTS[relative], relative


def test_unmodified_factory_copy_is_retired(tmp_path: Path) -> None:
    relative = "engineer/b200-kernelbench-runtime.md"
    path = _seed(tmp_path, relative, _shipped_body(relative))

    assert retire_orphaned_builtin_seeds(tmp_path) == [relative]
    assert not path.exists()


def test_agent_edited_copy_survives(tmp_path: Path) -> None:
    # The whole safety argument rests on this: a skill the agent has learned
    # from and rewritten is its work, not our factory copy.
    relative = "engineer/b200-kernelbench-runtime.md"
    edited = _shipped_body(relative) + "\n## Learned on 2026-07-25\nB200 SSH...\n"
    path = _seed(tmp_path, relative, edited)

    assert retire_orphaned_builtin_seeds(tmp_path) == []
    assert path.read_text(encoding="utf-8") == edited


def test_retirement_is_idempotent_and_ignores_absent_files(tmp_path: Path) -> None:
    assert retire_orphaned_builtin_seeds(tmp_path) == []
    relative = "engineer/b200-kernelbench-runtime.md"
    _seed(tmp_path, relative, _shipped_body(relative))
    assert retire_orphaned_builtin_seeds(tmp_path) == [relative]
    assert retire_orphaned_builtin_seeds(tmp_path) == []


def test_a_still_shipped_builtin_is_never_retired(tmp_path: Path, monkeypatch) -> None:
    # Reverse assertion: if a path is re-added to the bundled set, the cleanup
    # must back off even though its digest is still pinned.
    relative = "engineer/b200-kernelbench-runtime.md"
    body = _shipped_body(relative)
    path = _seed(tmp_path, relative, body)
    monkeypatch.setattr(
        builtins_module,
        "iter_builtin_skill_texts",
        lambda: [(relative, body)],
    )

    assert retire_orphaned_builtin_seeds(tmp_path) == []
    assert path.exists()


@pytest.mark.parametrize(("relative", "vertical"), sorted(MOVED_TO_VERTICALS.items()))
def test_moved_skill_still_reaches_its_vertical(relative: str, vertical: str) -> None:
    # Retiring a location must not retire a capability.
    assert relative in dict(iter_context_skill_texts(vertical, None))


@pytest.mark.parametrize("vertical", ["kernelbench", "nanochat", "nanogpt_speedrun"])
def test_benchmark_verticals_inherit_speedrun_methodology(vertical: str) -> None:
    # These three are concrete instances of the generic speedrun mission shape;
    # splitting the pool must not cut them off from that methodology.
    names = dict(iter_context_skill_texts(vertical, None))
    assert "engineer/speedrun-sota-optimization.md" in names


def test_kernelbench_still_sees_general_kernel_priors() -> None:
    names = dict(iter_context_skill_texts("kernelbench", None))
    assert "engineer/kernel-optimization-knowledge.md" in names


@pytest.mark.parametrize("vertical", ["research", "math"])
def test_paper_verticals_no_longer_carry_gpu_playbooks(vertical: str) -> None:
    # The point of the move: a paper or maths project stops paying matcher
    # tokens for B200 kernel and speedrun playbooks it will never select.
    names = set(dict(iter_context_skill_texts(vertical, None)))
    assert not (names & set(MOVED_TO_VERTICALS))
