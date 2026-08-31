from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Literal, Mapping, get_args

RunnerBackend = Literal[
    "codex", "claude", "copilot", "cursor", "opencode", "pi", "grok", "qoder", "dsh"
]

#: Every backend the runtime can drive, derived from the type above so the two
#: can never disagree. The CLI's `--backend` choices, the readiness check and
#: the operator control-knob help all render from this. They used to hold their
#: own copies and the knob help had drifted to five of the eight, so a fully
#: supported backend was one a user could not learn existed.
SUPPORTED_BACKENDS: tuple[str, ...] = get_args(RunnerBackend)

BACKEND_CODEX: RunnerBackend = "codex"
BACKEND_CLAUDE: RunnerBackend = "claude"
BACKEND_COPILOT: RunnerBackend = "copilot"
BACKEND_CURSOR: RunnerBackend = "cursor"
BACKEND_OPENCODE: RunnerBackend = "opencode"
BACKEND_PI: RunnerBackend = "pi"
BACKEND_GROK: RunnerBackend = "grok"
BACKEND_QODER: RunnerBackend = "qoder"
BACKEND_DSH: RunnerBackend = "dsh"
DEFAULT_RUNNER_BACKEND: RunnerBackend = BACKEND_CODEX

#: The in-process orchestration/test backend. It drives no external CLI and
#: talks to no provider, so it is deliberately NOT in ``SUPPORTED_BACKENDS`` and
#: ``normalize_runner_backend`` rejects it. It is nonetheless a first-class
#: value elsewhere — ``LifeWorkerConfig.backend`` is documented as
#: ``"codex" | "memory"`` — so call sites that can receive it must branch on it
#: BEFORE normalizing. Named here so those branches are greppable instead of
#: each spelling the literal.
BACKEND_MEMORY: str = "memory"

# Qoder's official CLI (``qodercli``) is a Claude Code fork: it accepts the same
# headless argv (``-p --output-format stream-json --model … --permission-mode …
# --resume …``) and emits the same stream-json event schema. So ``qoder`` reuses
# the ``claude`` command builder, event consumer, sandbox policy, and prompt
# delivery verbatim. This family set is the single source of truth for "treat it
# like claude" so those call sites never drift apart.
CLAUDE_FAMILY: frozenset[str] = frozenset({BACKEND_CLAUDE, BACKEND_QODER})


#: Historical spelling accepted for ``opencode``; kept so existing configs and
#: the persisted knob store keep resolving after the strictening below.
_BACKEND_ALIASES: dict[str, RunnerBackend] = {"opencod": BACKEND_OPENCODE}

_SUPPORTED_BACKEND_SET: frozenset[str] = frozenset(SUPPORTED_BACKENDS)

# npm installs Windows command wrappers such as ``codex.cmd`` under
# ``%APPDATA%\\npm``.  Those wrappers do not contain Node themselves: they invoke
# a bare ``node`` and therefore fail when a GUI host inherited an old/stunted
# PATH (common after an nvm-windows install until Explorer is restarted).  Keep
# the repair at the runner boundary so every host, including frozen Desktop,
# gets the same launch environment.
_NODE_WRAPPER_SUFFIXES = frozenset({".cmd", ".bat"})
_NODE_FILENAMES = ("node.exe", "node")
_PERCENT_ENV_RE = re.compile(r"%([^%]+)%")


def _environment_value(env: Mapping[str, str], name: str) -> str:
    """Read an environment variable case-insensitively for Windows maps."""
    direct = env.get(name)
    if direct is not None:
        return str(direct)
    wanted = name.casefold()
    for key, value in env.items():
        if str(key).casefold() == wanted:
            return str(value)
    return ""


def _expand_percent_environment(value: str, env: Mapping[str, str]) -> str:
    """Expand only ``%NAME%`` entries using the supplied child environment."""
    return _PERCENT_ENV_RE.sub(
        lambda match: _environment_value(env, match.group(1)) or match.group(0),
        value,
    )


def _node_in_directory(directory: Path) -> Path | None:
    for name in _NODE_FILENAMES:
        candidate = directory / name
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _path_entries(value: str, env: Mapping[str, str]) -> list[Path]:
    return [
        Path(_expand_percent_environment(entry.strip().strip('"'), env))
        for entry in value.split(os.pathsep)
        if entry.strip()
    ]


def _nvm_settings_paths(env: Mapping[str, str]) -> list[Path]:
    """Known nvm-windows settings locations, without scanning user directories."""
    home = Path(_environment_value(env, "USERPROFILE") or Path.home())
    local_app_data = Path(
        _environment_value(env, "LOCALAPPDATA")
        or home / "AppData" / "Local"
    )
    app_data = Path(
        _environment_value(env, "APPDATA")
        or home / "AppData" / "Roaming"
    )
    paths = [
        Path(_environment_value(env, "NVM_HOME")) / "settings.txt"
        if _environment_value(env, "NVM_HOME")
        else None,
        local_app_data / "nvm" / "settings.txt",
        app_data / "nvm" / "settings.txt",
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        if path is None:
            continue
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _nvm_node_dirs(env: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []
    for settings in _nvm_settings_paths(env):
        try:
            lines = settings.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            key, separator, value = line.partition(":")
            if not separator or key.strip().casefold() not in {"path", "symlink"}:
                continue
            text = _expand_percent_environment(value.strip().strip('"'), env)
            if text:
                candidates.append(Path(text))
    return candidates


def _node_runtime_candidates(agent_bin: str, env: Mapping[str, str]) -> list[Path]:
    """Return ordered, bounded directories where a batch runner may find Node."""
    runner = Path(agent_bin).expanduser()
    home = Path(_environment_value(env, "USERPROFILE") or Path.home())
    local_app_data = Path(
        _environment_value(env, "LOCALAPPDATA")
        or home / "AppData" / "Local"
    )
    candidates: list[Path] = []
    if runner.parent != Path("."):
        candidates.append(runner.parent)
    candidates.extend(_path_entries(_environment_value(env, "PATH"), env))
    for name in ("NVM_SYMLINK", "NVM_HOME"):
        value = _environment_value(env, name).strip()
        if value:
            candidates.append(Path(_expand_percent_environment(value, env)))
    candidates.extend(_nvm_node_dirs(env))
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        value = _environment_value(env, name).strip()
        if value:
            candidates.append(Path(value) / "nodejs")
    candidates.extend([
        local_app_data / "Volta" / "bin",
        local_app_data / "nvs" / "default",
        home / "scoop" / "apps" / "nodejs" / "current",
    ])
    seen: set[str] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        key = str(candidate).casefold()
        if key and key not in seen:
            seen.add(key)
            ordered.append(candidate)
    return ordered


def runner_child_environment(
    agent_bin: str,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Return an environment that makes a Node-backed batch runner runnable.

    ``None`` means the inherited environment is already sufficient (or the
    runner is not a Windows batch wrapper).  The helper never writes settings
    or invokes Node; it merely prepends a verified ``node.exe`` directory when
    a non-interactive host discovered an npm ``.cmd`` launcher independently
    of its inherited PATH.
    """
    runner = Path(str(agent_bin or "")).expanduser()
    if runner.suffix.casefold() not in _NODE_WRAPPER_SUFFIXES:
        return None
    source = dict(os.environ if env is None else env)
    current_path = _environment_value(source, "PATH")
    if any(_node_in_directory(directory) for directory in _path_entries(current_path, source)):
        return None
    node_dir = next(
        (
            directory
            for directory in _node_runtime_candidates(str(runner), source)
            if _node_in_directory(directory) is not None
        ),
        None,
    )
    if node_dir is None:
        return None
    # Windows treats environment variable names case-insensitively.  Passing
    # both ``Path`` and ``PATH`` lets the child observe an arbitrary one, so
    # normalize to a single explicit entry before spawning it.
    for key in tuple(source):
        if key.casefold() == "path":
            source.pop(key)
    source["PATH"] = os.pathsep.join(
        str(entry)
        for entry in (node_dir, *_path_entries(current_path, source))
    )
    return source


def normalize_runner_backend(raw: str | None) -> RunnerBackend:
    """Canonicalize a backend name.

    Empty/``None`` means "not configured" and still yields
    ``DEFAULT_RUNNER_BACKEND`` — that is a real state with a real default, and
    several callers depend on it.

    A NON-empty value that names no known backend now raises ``ValueError``.
    It used to fall through to codex, so a typo'd ``ARGUS_SKILL_*_BACKEND``
    ("copilto") produced a working-looking run against an entirely different
    provider, with the operator's actual choice never mentioned anywhere.

    Callers that must tolerate an unknown value (display paths that echo back
    whatever the operator typed) already guard this call — see
    ``core.backend_readiness.resolve_backend_profile`` and
    ``core.role_config._normalize_backend``.
    """
    value = str(raw or "").strip().lower()
    if not value:
        return DEFAULT_RUNNER_BACKEND
    alias = _BACKEND_ALIASES.get(value)
    if alias is not None:
        return alias
    if value in _SUPPORTED_BACKEND_SET:
        # SUPPORTED_BACKENDS holds the canonical lowercase spellings, so a
        # membership hit IS the canonical name.
        return value  # type: ignore[return-value]
    raise ValueError(
        f"unknown agent-CLI backend {raw!r}; supported backends are: "
        f"{', '.join(SUPPORTED_BACKENDS)}"
    )


def default_runner_bin(backend: RunnerBackend) -> str:
    if backend == BACKEND_CLAUDE:
        return "claude"
    if backend == BACKEND_COPILOT:
        return "copilot"
    if backend == BACKEND_CURSOR:
        # ``agent`` is the current official command; old builds used
        # ``cursor-agent`` and are discovered below for compatibility.
        return "agent"
    if backend == BACKEND_OPENCODE:
        return "opencode"
    if backend == BACKEND_PI:
        return "pi"
    if backend == BACKEND_GROK:
        return "grok"
    if backend == BACKEND_QODER:
        return "qodercli"
    if backend == BACKEND_DSH:
        return "dsh"
    return "codex"


def _resolve_explicit_candidate(candidate: Path) -> str | None:
    # Test fixtures, portable shims, and some user-local launchers are valid
    # extensionless files even on Windows. ``shutil.which`` applies PATHEXT and
    # can miss those exact candidates, so honor an explicitly located file
    # before probing sibling .exe/.cmd variants.
    try:
        is_file = candidate.is_file()
    except OSError:
        # PATH can contain an inaccessible launcher owned by another user.
        # Treat that entry as unavailable instead of breaking discovery for
        # every backend that appears later on PATH.
        return None
    if is_file and (os.name == "nt" or os.access(candidate, os.X_OK)):
        return str(candidate)
    resolved = shutil.which(str(candidate))
    if resolved:
        return resolved
    if os.name != "nt" or candidate.suffix:
        return None
    extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
    wanted = {f"{candidate.name}{extension}".casefold() for extension in extensions if extension}
    try:
        for entry in candidate.parent.iterdir():
            if entry.is_file() and entry.name.casefold() in wanted:
                return str(entry)
    except OSError:
        return None
    return None


def resolve_runner_bin(
    backend: RunnerBackend | str | None,
    configured: str | None = None,
) -> str | None:
    """Resolve a CLI independently of service-manager PATH omissions."""
    chosen = normalize_runner_backend(backend)
    explicit = bool(str(configured or "").strip())
    requested = str(configured or default_runner_bin(chosen)).strip()
    if not requested:
        return None
    expanded = str(Path(requested).expanduser())
    resolved = shutil.which(expanded)
    if resolved:
        return resolved
    if Path(expanded).parent != Path("."):
        return None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        resolved = _resolve_explicit_candidate(Path(entry) / expanded)
        if resolved:
            return resolved
    if chosen == BACKEND_OPENCODE:
        opencode_home = Path.home() / ".opencode" / "bin" / expanded
        resolved = _resolve_explicit_candidate(opencode_home)
        if resolved:
            return resolved
    if chosen == BACKEND_DSH:
        # User-local Node installations often expose `node` through a stable
        # shim while npm's global package links remain beside the shim target.
        # Resolve that target before falling back to nvm-specific locations.
        node = shutil.which("node")
        if node:
            try:
                node_sibling = Path(node).resolve().parent / expanded
            except (OSError, RuntimeError):
                node_sibling = None
            if node_sibling is not None:
                resolved = _resolve_explicit_candidate(node_sibling)
                if resolved:
                    return resolved
        # dsh is installed through the nvm-managed Node toolchain, whose bin
        # directory is absent from non-interactive PATHs (the daemon may be
        # started from one). Probe the per-version nvm bins newest-first.
        nvm_versions = Path.home() / ".nvm" / "versions" / "node"
        if nvm_versions.is_dir():
            for version_dir in sorted(nvm_versions.iterdir(), reverse=True):
                candidate = version_dir / "bin" / expanded
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
    user_local = Path.home() / ".local" / "bin" / expanded
    resolved = _resolve_explicit_candidate(user_local)
    if resolved:
        return resolved
    if chosen == BACKEND_CURSOR and not explicit:
        for alias in ("cursor-agent",):
            resolved = shutil.which(alias)
            if resolved:
                return resolved
            for entry in os.environ.get("PATH", "").split(os.pathsep):
                if not entry:
                    continue
                resolved = _resolve_explicit_candidate(Path(entry) / alias)
                if resolved:
                    return resolved
            resolved = _resolve_explicit_candidate(Path.home() / ".local" / "bin" / alias)
            if resolved:
                return resolved
        if os.name == "nt":
            local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
            if local_app_data:
                install_root = Path(local_app_data) / "cursor-agent"
                for name in ("agent.cmd", "agent.exe", "cursor-agent.cmd", "cursor-agent.exe"):
                    resolved = _resolve_explicit_candidate(install_root / name)
                    if resolved:
                        return resolved
    return None


def resolve_available_runner(
    backend: RunnerBackend | str | None,
    configured: str | None = None,
) -> tuple[RunnerBackend, str]:
    """Resolve the requested CLI, falling back only when Codex is absent."""
    raw = str(backend or "").strip().lower()
    chosen = normalize_runner_backend(backend)
    resolved = resolve_runner_bin(chosen, configured)
    if resolved:
        return chosen, resolved
    if chosen == BACKEND_CODEX and raw in ("", BACKEND_CODEX):
        copilot = resolve_runner_bin(BACKEND_COPILOT)
        if copilot:
            return BACKEND_COPILOT, copilot
    return chosen, str(configured or default_runner_bin(chosen)).strip()
