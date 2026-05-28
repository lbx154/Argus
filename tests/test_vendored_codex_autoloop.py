"""Sanity tests for the vendored ``codex_autoloop`` module.

After dropping ArgusBot as an external optional dependency, argus-skill
must be able to drive the codex/claude/copilot CLI using nothing more
than its own wheel. These tests fail loudly if the vendored copy ever
gets dropped or its public surface diverges from what
``argus_skill.adapters.codex_backend`` expects.
"""
from __future__ import annotations


def test_vendored_codex_runner_importable() -> None:
    from argus_skill.codex_autoloop.codex_runner import (
        CodexRunner,
        RunnerOptions,
    )
    assert CodexRunner.__module__ == "argus_skill.codex_autoloop.codex_runner"
    assert RunnerOptions.__module__ == "argus_skill.codex_autoloop.codex_runner"


def test_vendored_runner_backend_constants() -> None:
    from argus_skill.codex_autoloop.runner_backend import (
        BACKEND_CLAUDE,
        BACKEND_CODEX,
        BACKEND_COPILOT,
        DEFAULT_RUNNER_BACKEND,
        normalize_runner_backend,
    )
    assert BACKEND_CODEX == "codex"
    assert BACKEND_CLAUDE == "claude"
    assert BACKEND_COPILOT == "copilot"
    assert DEFAULT_RUNNER_BACKEND in {BACKEND_CODEX, BACKEND_CLAUDE, BACKEND_COPILOT}
    assert normalize_runner_backend("CODEX") == BACKEND_CODEX
    assert normalize_runner_backend("copilot") == BACKEND_COPILOT


def test_codex_backend_resolver_uses_vendored_module() -> None:
    from argus_skill.adapters.codex_backend import _import_argusbot
    deps = _import_argusbot()
    runner_cls = deps["CodexRunner"]
    # Even if a stale top-level ``codex_autoloop`` happens to be on path,
    # the resolver should prefer the vendored copy that ships with us.
    assert runner_cls.__module__.startswith("argus_skill.codex_autoloop"), (
        f"expected vendored codex_runner; got {runner_cls.__module__}"
    )
    for required in (
        "CodexRunner",
        "ArgusRunnerOptions",
        "BACKEND_CLAUDE",
        "BACKEND_CODEX",
        "BACKEND_COPILOT",
        "DEFAULT_RUNNER_BACKEND",
        "default_runner_bin",
        "normalize_runner_backend",
    ):
        assert required in deps, f"resolver missing {required}"
