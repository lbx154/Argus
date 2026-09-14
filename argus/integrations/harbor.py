"""Let Harbor Framework invoke the complete Argus runtime as an installed agent."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .. import __version__
from ..core.usage import project_usage_summary


class HarborUnavailableError(RuntimeError):
    """Raised when the Harbor adapter is used without a compatible Harbor."""


try:
    from harbor.agents.installed.codex import Codex as _HarborCodex
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext
    from harbor.models.trial.paths import EnvironmentPaths
except ImportError as exc:
    _HARBOR_IMPORT_ERROR: ImportError | None = exc

    class _HarborCodex:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise HarborUnavailableError(
                "Argus's Harbor adapter requires Harbor Framework >=0.21,<0.22 "
                "on Python 3.12 or newer. Install it with `pip install "
                "'argus-skill[harbor]'`."
            ) from _HARBOR_IMPORT_ERROR

    BaseEnvironment = Any  # type: ignore[misc,assignment]
    AgentContext = Any  # type: ignore[misc,assignment]

    class EnvironmentPaths:  # type: ignore[no-redef]
        agent_dir = PurePosixPath("/logs/agent")

else:
    _HARBOR_IMPORT_ERROR = None


_ARGUS_VENV = PurePosixPath("/tmp/argus-harbor-venv")
_ARGUS_STATE_DIRNAME = "argus-state"
_OBJECTIVE_FILENAME = "argus-objective.txt"
_RUNTIME_LOG_FILENAME = "argus-runtime.log"
_HARBOR_HOUSE_RULES = """Harbor supplies one isolated, bounded evaluation task.
Work only in the current Harbor task workspace and Argus trial state directory.
Do not access unrelated host paths, credentials, or services. Do not weaken
Argus review, verification, sandbox, or completion gates. Stop when the bounded
objective is independently verified or a real external blocker is recorded."""


def harbor_available() -> bool:
    """Return whether the supported Harbor installed-agent API can be imported."""

    return _HARBOR_IMPORT_ERROR is None


def _positive_timeout(value: int | str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be an integer number of seconds") from exc
    if parsed <= 0:
        raise ValueError("timeout must be greater than zero")
    return parsed


def _safe_package_spec(value: str | None) -> str | None:
    spec = str(value or "").strip()
    if not spec:
        return None
    if "\n" in spec or "\r" in spec or "\x00" in spec:
        raise ValueError("argus_package must be a single-line pip requirement")
    return spec


def _argus_source_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file() and (candidate / "argus_skill").is_dir():
        return candidate
    return None


def _build_local_wheel(source_root: Path, output_dir: Path) -> Path:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(output_dir),
            str(source_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown build failure").strip()
        raise RuntimeError(f"Could not build the Argus wheel for Harbor: {detail}")
    wheels = sorted(output_dir.glob("argus_skill-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            "Argus Harbor packaging expected exactly one wheel, "
            f"found {[path.name for path in wheels]}"
        )
    return wheels[0]


def _latest_project_root(state_root: Path) -> Path | None:
    projects_dir = state_root / "projects"
    candidates = (
        [
            path
            for path in projects_dir.iterdir()
            if path.is_dir() and (path / "continuous.json").is_file()
        ]
        if projects_dir.is_dir()
        else []
    )
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path / "continuous.json").stat().st_mtime)


class ArgusHarborAgent(_HarborCodex):  # type: ignore[misc,valid-type]
    """Harbor agent whose one ``run`` call executes the full Argus team."""

    SUPPORTS_ATIF = False
    SUPPORTS_RESUME = False
    SUPPORTS_LOAD_NATIVE_TRAJECTORY = False
    SUPPORTS_LOAD_ATIF_TRAJECTORY = False
    SUPPORTS_HANDOFF = False

    @staticmethod
    def name() -> str:
        return "argus"

    def __init__(
        self,
        *args: Any,
        version: str | None = None,
        argus_package: str | None = None,
        codex_version: str | None = None,
        reasoning_effort: str = "high",
        timeout: int | str | None = None,
        **kwargs: Any,
    ) -> None:
        self._argus_version = str(version or __version__).strip()
        self._argus_package = _safe_package_spec(argus_package)
        self._argus_codex_version = str(codex_version or "").strip() or None
        self._argus_reasoning_effort = str(reasoning_effort or "high").strip()
        self._argus_timeout = _positive_timeout(timeout)
        super().__init__(
            *args,
            version=None,
            reasoning_effort=self._argus_reasoning_effort,
            **kwargs,
        )

    def version(self) -> str:
        return self._argus_version

    def get_version_command(self) -> None:
        return None

    async def install(self, environment: BaseEnvironment) -> None:
        original_version = self._version
        self._version = self._argus_codex_version
        try:
            await super().install(environment)
        finally:
            self._version = original_version

        await self.ensure_system_dependencies(
            environment,
            ("python3", "python_pip", "python_venv", "git"),
        )
        if self._argus_package is not None:
            await self._install_argus_requirement(environment, self._argus_package)
            return

        source_root = _argus_source_root()
        if source_root is None:
            raise RuntimeError(
                "ArgusHarborAgent was not loaded from a source checkout and "
                "argus_package was not provided. Pass an immutable wheel or Git "
                "requirement with `--ak argus_package=...`."
            )
        with tempfile.TemporaryDirectory(prefix="argus-harbor-wheel-") as temp_dir:
            wheel = await asyncio.to_thread(
                _build_local_wheel,
                source_root,
                Path(temp_dir),
            )
            remote_wheel = PurePosixPath("/tmp") / wheel.name
            await environment.upload_file(wheel, remote_wheel.as_posix())
            await self._install_argus_requirement(environment, remote_wheel.as_posix())

    async def _install_argus_requirement(
        self,
        environment: BaseEnvironment,
        requirement: str,
    ) -> None:
        venv = shlex.quote(_ARGUS_VENV.as_posix())
        package = shlex.quote(requirement)
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "if python3 -c 'import sys; raise SystemExit("
                "sys.version_info < (3, 11))'; then "
                f"  python3 -m venv {venv}; "
                f"  {venv}/bin/python -m pip install --upgrade pip; "
                f"  {venv}/bin/python -m pip install {package}; "
                "else "
                "  curl -LsSf https://astral.sh/uv/install.sh | sh; "
                '  UV_BIN="$(command -v uv || printf %s "$HOME/.local/bin/uv")"; '
                '  "$UV_BIN" python install 3.12; '
                f'  "$UV_BIN" venv --python 3.12 {venv}; '
                f'  "$UV_BIN" pip install --python {venv}/bin/python {package}; '
                "fi; "
                f"{venv}/bin/argus-skill --version"
            ),
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        _ = context
        if not self.model_name:
            raise ValueError("Harbor must provide a model for the Argus agent")

        agent_dir = PurePosixPath(EnvironmentPaths.agent_dir)
        state_root = agent_dir / _ARGUS_STATE_DIRNAME
        objective_path = agent_dir / _OBJECTIVE_FILENAME
        runtime_log = agent_dir / _RUNTIME_LOG_FILENAME
        codex_home = agent_dir / "codex-home"
        secrets_dir = agent_dir / "codex-secrets"
        remote_auth_path = secrets_dir / "auth.json"
        remote_config_path = codex_home / "config.toml"

        local_objective = self.logs_dir / _OBJECTIVE_FILENAME
        local_objective.parent.mkdir(parents=True, exist_ok=True)
        local_objective.write_text(instruction, encoding="utf-8")
        await environment.upload_file(local_objective, objective_path.as_posix())

        env, auth_setup = await self._argus_codex_environment(
            environment,
            codex_home=codex_home,
            secrets_dir=secrets_dir,
            remote_auth_path=remote_auth_path,
            remote_config_path=remote_config_path,
        )
        await self.exec_as_agent(environment, command=auth_setup, env=env)

        model = self.model_name.split("/")[-1]
        env.update(
            {
                "ARGUS_SKILL_RUNNER_BACKEND": "codex",
                "ARGUS_SKILL_LIFE_BACKEND": "codex",
                "ARGUS_SKILL_MODEL": model,
                "ARGUS_SKILL_MANAGER_MODEL": model,
                "ARGUS_SKILL_PLANNER_MODEL": model,
                "ARGUS_SKILL_ENGINEER_MODEL": model,
                "ARGUS_SKILL_REVIEWER_MODEL": model,
                "ARGUS_SKILL_MANAGER_REASONING_EFFORT": self._argus_reasoning_effort,
                "ARGUS_SKILL_PLANNER_REASONING_EFFORT": self._argus_reasoning_effort,
                "ARGUS_SKILL_ENGINEER_REASONING_EFFORT": self._argus_reasoning_effort,
                "ARGUS_SKILL_REVIEWER_REASONING_EFFORT": self._argus_reasoning_effort,
                "ARGUS_SKILL_REQUIRE_INDEPENDENT_REVIEW": "1",
                "ARGUS_SKILL_FORCE_STAGE_CLOSING": "1",
                "ARGUS_SKILL_SPECIAL_PROMPTS_DIR": (
                    state_root / "special-prompts"
                ).as_posix(),
                "ARGUS_SKILL_DAEMON_POLL_S": "0.1",
            }
        )

        house_rules = state_root / "special-prompts" / "10-harbor-house-rules.md"
        prepare_result = await environment.exec(
            command=(
                f"mkdir -p {shlex.quote(house_rules.parent.as_posix())}; "
                f"printf '%s\\n' {shlex.quote(_HARBOR_HOUSE_RULES)} > "
                f"{shlex.quote(house_rules.as_posix())}; "
                f"chmod 0644 {shlex.quote(house_rules.as_posix())}"
            ),
            user=getattr(environment, "default_user", None),
        )
        if prepare_result.return_code != 0:
            detail = prepare_result.stderr or prepare_result.stdout or "unknown error"
            raise RuntimeError(f"Could not prepare Argus Harbor house rules: {detail}")

        command = " ".join(
            [
                shlex.quote((_ARGUS_VENV / "bin" / "argus-skill").as_posix()),
                "--daemon-fg",
                "--continuous",
                "--bounded",
                "--new",
                "--backend",
                "codex",
                "--auth-mode",
                "subscription_cli",
                "--objective-file",
                shlex.quote(objective_path.as_posix()),
                "--life-dir",
                shlex.quote(state_root.as_posix()),
                "2>&1",
                "|",
                "tee",
                shlex.quote(runtime_log.as_posix()),
            ]
        )
        try:
            await self.exec_as_agent(
                environment,
                command=command,
                env=env,
                timeout_sec=self._argus_timeout,
            )
        finally:
            cleanup = await environment.exec(
                command=(
                    f"rm -rf {shlex.quote(secrets_dir.as_posix())}; "
                    f"rm -f {shlex.quote((codex_home / 'auth.json').as_posix())}"
                ),
                user=getattr(environment, "default_user", None),
            )
            if cleanup.return_code != 0:
                detail = cleanup.stderr or cleanup.stdout or "unknown error"
                self.logger.warning("Could not clean Harbor Argus credentials: %s", detail)

    async def _argus_codex_environment(
        self,
        environment: BaseEnvironment,
        *,
        codex_home: PurePosixPath,
        secrets_dir: PurePosixPath,
        remote_auth_path: PurePosixPath,
        remote_config_path: PurePosixPath,
    ) -> tuple[dict[str, str], str]:
        env: dict[str, str] = {"CODEX_HOME": codex_home.as_posix()}
        await self.exec_as_agent(
            environment,
            command=(
                f"mkdir -p {shlex.quote(codex_home.as_posix())} "
                f"{shlex.quote(secrets_dir.as_posix())}"
            ),
            env=env,
        )

        auth_json_path = self._resolve_auth_json_path()
        if auth_json_path:
            await environment.upload_file(auth_json_path, remote_auth_path.as_posix())
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    command=(
                        f"chown {shlex.quote(str(environment.default_user))} "
                        f"{shlex.quote(remote_auth_path.as_posix())}"
                    ),
                )
            auth_setup = (
                f"ln -sf {shlex.quote(remote_auth_path.as_posix())} "
                f"{shlex.quote((codex_home / 'auth.json').as_posix())}"
            )
        else:
            access = self.model_connection
            if not access.api_key:
                raise ValueError(
                    "Harbor did not provide OPENAI_API_KEY or CODEX_AUTH_JSON_PATH "
                    "for Argus's Codex backend"
                )
            env["OPENAI_API_KEY"] = access.api_key
            auth_setup = (
                "python3 - <<'PY'\n"
                "import json\n"
                "import os\n"
                "from pathlib import Path\n"
                f"path = Path({remote_auth_path.as_posix()!r})\n"
                'path.write_text(json.dumps({"OPENAI_API_KEY": '
                'os.environ["OPENAI_API_KEY"]}), encoding="utf-8")\n'
                "PY\n"
                f"ln -sf {shlex.quote(remote_auth_path.as_posix())} "
                f"{shlex.quote((codex_home / 'auth.json').as_posix())}"
            )

        access = self.model_connection
        if access.configured_base_url:
            env["OPENAI_BASE_URL"] = access.configured_base_url
        effective_config = self._build_effective_config(access.configured_base_url)
        await self._upload_effective_config(
            environment,
            effective_config,
            remote_config_path.as_posix(),
        )
        return env, auth_setup

    def populate_context_post_run(self, context: AgentContext) -> None:
        state_root = self.logs_dir / _ARGUS_STATE_DIRNAME
        project_root = _latest_project_root(state_root)
        metadata = dict(context.metadata or {})
        argus_metadata: dict[str, Any] = {
            "state_dir": _ARGUS_STATE_DIRNAME,
            "runtime_log": _RUNTIME_LOG_FILENAME,
        }
        if project_root is not None:
            continuous_path = project_root / "continuous.json"
            try:
                continuous = json.loads(continuous_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.logger.warning("Could not read Argus completion state: %s", exc)
            else:
                argus_metadata.update(
                    {
                        "project": project_root.name,
                        "completed": not bool(continuous.get("enabled", False)),
                        "done_reason": str(continuous.get("done_reason") or ""),
                        "done_at": str(continuous.get("done_at") or ""),
                    }
                )
            try:
                usage = project_usage_summary(project_root)
            except OSError as exc:
                self.logger.warning("Could not read Argus usage summary: %s", exc)
            else:
                context.n_input_tokens = usage.input_tokens
                context.n_cache_tokens = usage.cached_input_tokens
                context.n_output_tokens = usage.output_tokens + usage.reasoning_output_tokens
                context.cost_usd = usage.cost_usd
                argus_metadata["calls"] = usage.call_count
                argus_metadata["pricing_status"] = usage.pricing_status
        else:
            argus_metadata["completed"] = False
            argus_metadata["done_reason"] = "Argus project state was not captured"

        metadata["argus"] = argus_metadata
        context.metadata = metadata


ArgusHarborCodex = ArgusHarborAgent

__all__ = [
    "ArgusHarborAgent",
    "ArgusHarborCodex",
    "HarborUnavailableError",
    "harbor_available",
]
