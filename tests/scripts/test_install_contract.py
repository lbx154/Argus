from __future__ import annotations

import tomllib
from dataclasses import fields
from pathlib import Path

from packaging.requirements import Requirement

from argus_skill.agent_cli.agent_cli_runner import RunnerOptions as CliRunnerOptions
from argus_skill.core.models import RunnerOptions


def _project() -> dict:
    root = Path(__file__).parents[2]
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_base_install_contains_the_webapi_required_by_argus_cockpit() -> None:
    project = _project()
    names = {Requirement(value).name.casefold() for value in project["dependencies"]}

    assert {"fastapi", "uvicorn", "websockets"} <= names
    assert project["optional-dependencies"]["web"] == []
    assert project["scripts"]["argus"] == "argus_skill.apps.tui_launcher:main"


def test_model_facing_runner_options_have_no_retired_output_schema() -> None:
    assert "output_schema_path" not in {field.name for field in fields(RunnerOptions)}
    assert "output_schema_path" not in {
        field.name for field in fields(CliRunnerOptions)
    }
