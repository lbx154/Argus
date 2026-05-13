"""Contract tests for the documented live architecture map."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_architecture_docs_list_shared_helper_modules() -> None:
    readme = (_repo_root() / "README.md").read_text(encoding="utf-8")
    architecture = (_repo_root() / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")

    helper_modules = [
        "argus_skill/apps/_inbox.py",
        "argus_skill/apps/_life_actions.py",
        "argus_skill/apps/_target_paths.py",
        "argus_skill/life/status.py",
    ]
    shared_surfaces = [
        "apps/cli.py",
        "apps/_life_repl.py",
        "apps/_watch.py",
        "life/telegram_bot.py",
    ]

    for module in helper_modules:
        leaf = module.rsplit("/", 1)[-1]
        assert module in architecture
        assert leaf in readme

    for surface in shared_surfaces:
        assert surface in readme
        assert surface in architecture
