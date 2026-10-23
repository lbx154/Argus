"""Sandboxes for the agent CLIs Argus starts.

``isolated_workdir_command`` is the agent sandbox every role's CLI runs in,
on every backend: it sees only its workdir and what its call is given.
``forbidden_write_roots`` names what no role may ever write (the "gate's
brain", the package, the active Python), and ``sandboxed_child_env`` keeps
push-capable credentials out of a sandboxed call's environment.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import site
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path


def _package_root() -> str | None:
    try:
        import argus

        return str(Path(argus.__file__).resolve().parent.parent)
    except Exception:  # pragma: no cover — defensive
        return None


def _user_site_packages(home: Path) -> Path:
    """User-scheme site-packages for the running interpreter, re-anchored
    under ``home``: ``site.getusersitepackages()`` is resolved once against
    the process's startup HOME, and tests substitute ``Path.home()``, so the
    forbidden root must move with the other home-derived roots. A user site
    outside the startup home (``PYTHONUSERBASE``) is forbidden where it is."""
    user_site = Path(site.getusersitepackages())
    try:
        return home / user_site.relative_to(Path(os.path.expanduser("~")))
    except ValueError:
        return user_site


def forbidden_write_roots() -> list[str]:
    """Paths a sandboxed builder must NEVER be able to write."""
    from .paths import global_root

    home = Path.home()
    # ~/.argus-skill = the gate's brain; ~/.codex = codex config; the package
    # source = the harness itself; ``sys.prefix`` = the active venv, whose
    # site-packages auto-runs ``.pth`` / ``sitecustomize.py`` at the start of
    # EVERY python process — so a write there escapes the sandbox into the next
    # un-sandboxed interpreter (the daemon worker, the gate-check subprocess,
    # the reviewer/planner). On a non-editable install ``sys.prefix`` also *is*
    # where the package source lives.
    # The *user* scheme is the same ``.pth`` escape one directory over: with
    # the system site unwritable, pip silently falls back to it ("Defaulting
    # to user installation", 2026-09-05 incident), planting an editable
    # ``.pth`` in the user site-packages and rewriting the ~/.local/bin
    # launchers that later restart the daemon on old code. ``~/.local/lib``
    # forbids the user site of EVERY interpreter version — the incident pip
    # ran under the system 3.11, not this venv's 3.12, so the per-interpreter
    # ``_user_site_packages`` alone misses it; that helper stays for a
    # ``PYTHONUSERBASE`` relocated outside home. ``~/.local/state`` and
    # ``~/.local/share`` are siblings, not children, and stay unaffected.
    # (The maintenance worktree under ~/.argus-skill stays writable
    # regardless: it is the codex ``-C`` / bwrap bind root, which this list
    # never filters.)
    roots = [
        str(global_root()),
        str(home / ".argus-skill"),
        str(home / ".codex"),
        str(Path(sys.prefix)),
        str(_user_site_packages(home)),
        str(home / ".local" / "lib"),
        str(home / ".local" / "bin"),
    ]
    pkg = _package_root()
    if pkg:
        roots.append(pkg)
    # de-dup, normalized
    out: list[str] = []
    for r in roots:
        rp = str(Path(r))
        if rp not in out:
            out.append(rp)
    return out


# Environment keys carrying push-capable VCS credentials. A sandboxed builder
# role must NOT inherit these, else the engineer can ``gh push`` / ``git push``
# to the package's own remote and rewrite the harness at its source of truth,
# bypassing the local-FS sandbox entirely (network egress is not policed).
_VCS_CRED_ENV_KEYS = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_API_TOKEN", "COPILOT_GITHUB_TOKEN", "GIT_TOKEN", "GIT_ASKPASS",
    "SSH_AUTH_SOCK",
)


def sandboxed_child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Child env for a sandboxed codex role.

    (1) Drops push-capable VCS credentials so a sandboxed engineer cannot push
        to the package's own remote and rewrite the harness at its source of
        truth (the local-FS sandbox does not police network egress).
    (2) Sets ``PIP_USER=0`` so pip against an unwritable site fails loudly
        (Errno 13) instead of silently "Defaulting to user installation",
        which rewrites the ~/.local/bin launchers and plants a user-site
        ``.pth`` (the 2026-09-05 escape); ``--target`` and in-venv installs
        are unaffected.
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key in _VCS_CRED_ENV_KEYS or key.startswith(("GH_", "GITHUB_")):
            env.pop(key, None)
    env["PIP_USER"] = "0"
    return env


# ---------------------------------------------------------------------------
# The agent sandbox: every role's agent CLI sees only what its call is given.

# System software, read-only. A deployment keeps tenant data and keys elsewhere.
_SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/opt", "/sys")


def sandbox_tool() -> str | None:
    """The host's sandbox: bubblewrap, else macOS ``sandbox-exec``, else none."""
    if os.name != "posix":
        return None
    return shutil.which("bwrap") or (shutil.which("sandbox-exec") if sys.platform == "darwin" else None)


def _private_root(working_dir: str | os.PathLike[str] | None) -> Path:
    from .paths import global_root

    key = hashlib.sha256(os.path.realpath(working_dir or os.sep).encode()).hexdigest()[:16]
    return Path(os.path.realpath(global_root())) / "sandbox" / key


def private_path(path: str | os.PathLike[str], working_dir: str | os.PathLike[str] | None) -> Path:
    """Where agents working in ``working_dir`` keep ``path``, a file of their
    home or of the Argus runtime.

    Under bubblewrap each workdir has its own copy of both, so no project sees
    another's CLI sessions. ``sandbox-exec`` cannot remap paths: the real one.
    """
    tool = sandbox_tool()
    if tool is None or Path(tool).name != "bwrap":
        return Path(path)
    real = Path(os.path.realpath(path))
    return _private_root(working_dir) / real.relative_to(real.anchor)


def private_copies(path: str | os.PathLike[str]) -> list[Path]:
    """Every workdir's private copy of ``path`` (see :func:`private_path`)."""
    real = Path(os.path.realpath(path))
    return sorted(_private_root(None).parent.glob(f"*/{real.relative_to(real.anchor)}"))


def _cli_files(backend: str, env: Mapping[str, str], home: Path) -> tuple[list[Path], list[Path]]:
    """The login files ``backend`` refreshes, and the settings it only reads,
    from the operator's own setup. The rest of its home is the workdir's."""
    def at(variable: str, default: str) -> Path:
        return Path(env.get(variable) or home / default).expanduser()

    codex, claude, copilot = at("CODEX_HOME", ".codex"), at("CLAUDE_CONFIG_DIR", ".claude"), at("COPILOT_HOME", ".copilot")
    pi, qoder = at("PI_CODING_AGENT_DIR", ".pi/agent"), at("QODER_CONFIG_DIR", ".qoder")
    return {
        "codex": ([codex / "auth.json"], [codex / "config.toml", codex / "AGENTS.md"]),
        "claude": ([claude / ".credentials.json"], [claude / "settings.json", claude / "CLAUDE.md"]),
        "qoder": ([qoder / ".credentials.json"], [qoder / "settings.json"]),
        "copilot": ([copilot / "config.json"], [
            copilot / "settings.json", copilot / "permissions-config.json", home / ".cache/copilot",
        ]),
        "pi": ([pi / "auth.json"], [pi / "models.json", pi / "settings.json"]),
        "opencode": ([home / ".local/share/opencode/auth.json"], [home / ".config/opencode"]),
        "cursor": ([home / ".config/cursor/auth.json", home / ".cursor/cli-config.json"], []),
        "grok": ([at("GROK_HOME", ".grok") / "auth.json"], []),
        "dsh": ([], [at("DSH_HOME", ".dsh") / ".env"]),
    }.get(backend, ([], []))


def _outermost(paths: Iterable[Path]) -> list[Path]:
    kept: list[Path] = []
    for path in sorted(set(paths), key=lambda value: len(value.parts)):
        if not any(path.is_relative_to(outer) for outer in kept):
            kept.append(path)
    return kept


def _install_root(executable: Path) -> Path:
    """An npm-installed CLI needs the ``node_modules`` it was installed into;
    anything else is one file."""
    parts = executable.parts
    return Path(*parts[:parts.index("node_modules") + 1]) if "node_modules" in parts[:-1] else executable


def _interpreter(executable: Path, search_path: str | None) -> list[Path]:
    """The interpreter a script's ``#!`` line names, e.g. ``node``."""
    try:
        with executable.open("rb") as handle:
            line = handle.readline(256)
    except OSError:
        return []
    words = line[2:].decode(errors="replace").split() if line.startswith(b"#!") else []
    if words and Path(words[0]).name == "env":
        words = [word for word in words[1:] if not word.startswith("-")]
    found = shutil.which(words[0], path=search_path) if words else None
    return [Path(os.path.realpath(found))] if found else []


def _git_common_dir(workdir: Path) -> list[Path]:
    """A linked worktree's repository, whose history it reads but never edits."""
    if not (workdir / ".git").is_file():
        return []
    try:
        found = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=workdir, capture_output=True, text=True, check=False,
        ).stdout.strip()
    except OSError:
        return []
    return [Path(found)] if found else []


def _resolver_config_target() -> list[Path]:
    """The real resolver file when /etc/resolv.conf points outside /etc."""
    target = Path(os.path.realpath("/etc/resolv.conf"))
    return [target] if target.is_file() and target != Path("/etc/resolv.conf") else []


def _backend_support_executables(executable: Path) -> list[Path]:
    """Return narrowly scoped executables needed by a user-level CLI wrapper.

    The local ``codex`` command may be a stable wrapper that selects the newest
    VS Code extension binary at runtime, and ``copilot`` one that selects the
    native binary of an nvm install. Bind only matching executable files, never
    the extension tree or the user home.
    """
    if executable.name == "copilot":
        pattern = "*/lib/node_modules/@github/copilot/node_modules/@github/copilot-*/copilot"
        roots = [Path.home() / ".nvm" / "versions" / "node"]
    elif executable.name == "codex":
        pattern = "openai.chatgpt-*/bin/*/codex"
        roots = [Path.home() / name / "extensions" for name in (".vscode-server", ".vscode-server-insiders", ".vscode")]
    else:
        return []
    found = {
        candidate.resolve()
        for root in roots for candidate in root.glob(pattern)
        if candidate.is_file() and os.access(candidate, os.X_OK) and candidate.resolve() != executable
    }
    return sorted(found, key=str)


def isolated_workdir_command(
    command: list[str],
    *,
    backend: str,
    working_dir: str | os.PathLike[str] | None,
    handed: Iterable[str | os.PathLike[str]] = (),
    named: Iterable[str] = (),
    writable: bool = True,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Run ``command`` where it sees only what its call is given.

    Visible: the workdir and the directories ``handed`` to the call (writable
    unless the call only reads), the files ``named`` on its command line,
    system software, the CLI itself and the Python that runs Argus's action
    tools. Its home and the Argus runtime are the workdir's private copies,
    into which only the CLI's login and settings are brought. No other
    project, tenant, Argus setting or CLI transcript exists in there.
    """
    tool = sandbox_tool()
    if tool is None:
        raise RuntimeError("this host has no sandbox; install bubblewrap")
    from .paths import global_root

    env = os.environ if env is None else env
    found = shutil.which(command[0], path=env.get("PATH"))
    if not found:
        raise RuntimeError(f"cannot find the agent CLI {command[0]}")
    executable = Path(os.path.realpath(found))
    support = _backend_support_executables(executable)
    if executable.name == "copilot" and support:
        executable, support = support[-1], []
    command = [str(executable), *command[1:]]

    def real(path: str | os.PathLike[str]) -> Path:
        return Path(os.path.realpath(path))

    def named_as(path: str | os.PathLike[str]) -> set[Path]:
        """A path as the CLI may name it: as given and resolved."""
        return {Path(os.path.abspath(path)), real(path)}

    home = Path(env.get("HOME") or Path.home())
    login, settings = _cli_files(backend, env, home)
    private = _outermost(
        real(path) for path in [home, global_root(), *(file.parent for file in [*login, *settings])]
    )
    workdir = Path(os.path.abspath(working_dir)) if working_dir else None
    given = [*([workdir] if workdir else []), *map(Path, handed)]
    shared = [real(path) for path in given]
    files = [
        path for path in (Path(arg.lstrip("@")) for arg in named if arg.lstrip("@").startswith("/"))
        if os.path.exists(path) and not os.path.isdir(path)
    ]
    argus = [real(path) for path in (sys.prefix, sys.base_prefix, sys.executable, _package_root()) if path]
    # The workdir and handed directories are writable when the call writes,
    # except a library handed from inside Argus or its Python.
    writes = [
        real(path) for path in given
        if writable and (path == workdir or not any(real(path).is_relative_to(inside) for inside in argus))
    ]
    readable = _outermost(
        path for path in (
            *map(Path, _SYSTEM_DIRS), *argus, _install_root(executable), *support,
            *_interpreter(executable, env.get("PATH")), *_resolver_config_target(),
            *(_git_common_dir(workdir) if workdir else []), *map(real, files),
        )
        if not any(path.is_relative_to(inside) for inside in shared)
    )
    if Path(tool).name == "sandbox-exec":
        homes = [file.parent for file in [*login, *settings] if not file.is_dir()]
        return _seatbelt(
            tool, command, workdir=workdir, hidden=private, readable=[*readable, *settings, *shared],
            writable=[*writes, *homes],
        )

    # (source, destination, writable). Deeper mounts cover shallower ones, and
    # at equal depth a private copy covers the real directory.
    mounts = [(path, path, False) for path in readable]
    for path in private:
        copy = _private_root(working_dir) / path.relative_to(path.anchor)
        copy.mkdir(parents=True, exist_ok=True, mode=0o700)
        mounts += [(copy, target, True) for target in named_as(path)]
    for files_, rw in ((login, True), (settings, False)):
        mounts += [(real(file), target, rw) for file in files_ for target in named_as(file)]
    for path in given:
        mounts += [(real(path), target, real(path) in writes) for target in named_as(path)]
    if workdir and (workdir / ".git").is_file():
        # A linked worktree's pointer stays put: the daemon's own git commands
        # later run in this tree and must not follow it anywhere else.
        mounts += [(real(workdir / ".git"), target, False) for target in named_as(workdir / ".git")]
    wrapped = [
        tool, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-uts", "--unshare-ipc",
        "--proc", "/proc", "--dev-bind", "/dev", "/dev", "--tmpfs", "/dev/shm", "--tmpfs", "/tmp",
    ]
    for source, target, rw in sorted(dict.fromkeys(mounts), key=lambda m: (len(m[1].parts), m[0] != m[1])):
        wrapped += ["--bind-try" if rw else "--ro-bind-try", str(source), str(target)]
    return [*wrapped, "--setenv", "TMPDIR", "/tmp", "--chdir", str(workdir or home), "--", *command]


def _seatbelt(
    tool: str, command: list[str], *, workdir: Path | None,
    hidden: list[Path], readable: list[Path], writable: list[Path],
) -> list[str]:
    """``sandbox-exec`` cannot remap paths: the home and the Argus runtime are
    hidden rather than replaced, and each CLI keeps its real home."""
    tmp = _private_root(workdir) / "tmp"
    tmp.mkdir(parents=True, exist_ok=True, mode=0o700)

    def any_of(paths: Iterable[Path | str]) -> str:
        quoted = (os.path.realpath(path).replace("\\", "\\\\").replace('"', '\\"') for path in paths)
        return "(require-any " + "".join(f'(subpath "{path}")' for path in quoted) + ")"

    profile = (
        "(version 1)(allow default)"
        f"(deny file-read-data (require-all {any_of([*hidden, '/Users', '/Volumes', tempfile.gettempdir()])}"
        f" (require-not {any_of([*readable, *writable, tmp])})))"
        f"(deny file-write* (require-not {any_of([*writable, tmp, '/dev'])}))"
    )
    return [tool, "-p", profile, "/usr/bin/env", f"TMPDIR={tmp}", *command]
