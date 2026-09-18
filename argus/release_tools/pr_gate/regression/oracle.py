"""Mechanical command/oracle equality checks, not semantic oracle validation.

Computed fixture/driver dependencies must be declared with repeatable --oracle
paths. Root/src Python projects are supported; arbitrary build layouts are not
inferred from an installed distribution.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shlex
import sys
from pathlib import Path

CACHE_NAMES = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git",
    ".venv", "venv", ".tox", ".nox",
}
PYTEST_CONFIGS = {"pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"}
PYTEST_VALUE_OPTIONS = {
    "-c", "--config-file", "-k", "-m", "-p", "-o", "--override-ini", "-r",
    "--rootdir", "--confcutdir", "--basetemp", "--ignore", "--ignore-glob", "--deselect",
    "--import-mode", "--maxfail", "--tb", "--show-capture", "--capture", "--color",
    "--code-highlight", "--verbosity", "--durations", "--durations-min",
    "--junitxml", "--junit-xml", "--junitprefix", "--junit-prefix",
    "--log-level", "--log-format", "--log-date-format", "--log-cli-level",
    "--log-cli-format", "--log-cli-date-format", "--log-file", "--log-file-mode",
    "--log-file-level", "--log-file-format", "--log-file-date-format", "--log-disable",
    "--assert", "--doctest-glob", "--doctest-report", "--pastebin", "--pdbcls",
    "--cov", "--cov-report", "--cov-config", "--cov-fail-under", "--cov-context",
}
MAX_ORACLE_FILES = 10000
MAX_ORACLE_BYTES = 64 * 1024 * 1024


def python_command(command: str) -> tuple[list[str], list[str]]:
    """Accept direct Python only; shell syntax is never interpreted in local v2."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        argv = list(lexer)
    except ValueError as exc:
        return [], [f"invalid_command: {exc}"]
    if not argv:
        return [], ["empty_command"]
    issues = []
    if any(token and set(token) <= set(";&|<>()") for token in argv):
        issues.append("shell_operators_are_not_supported")
    executable = Path(argv[0])
    if not (
        re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", executable.name)
        or str(executable) == sys.executable
    ):
        issues.append("only_direct_python_commands_are_supported")
    index = 1
    while index < len(argv):
        option = argv[index]
        if option in {"-c", "-m"}:
            if index + 1 >= len(argv):
                issues.append("missing_python_program")
            break
        if option == "--":
            if index + 1 >= len(argv):
                issues.append("missing_python_program")
            break
        if not option.startswith("-"):
            break
        if option not in {"-B", "-u", "-O", "-OO", "-s", "-q"}:
            issues.append(f"unsupported_python_option: {option}")
        index += 1
    else:
        issues.append("interactive_python_is_not_supported")
    return argv, issues


def _program(argv: list[str]) -> tuple[str, str, list[str]]:
    index = 1
    while index < len(argv) and argv[index] in {"-B", "-u", "-O", "-OO", "-s", "-q"}:
        index += 1
    if index >= len(argv):
        return "", "", []
    if argv[index] in {"-c", "-m", "--"}:
        return (
            argv[index], argv[index + 1] if index + 1 < len(argv) else "",
            argv[index + 2:],
        )
    return "script", argv[index], argv[index + 1:]


def _walk_error(error):
    raise error


def _walk(root: Path):
    for directory, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
        directories[:] = sorted(name for name in directories if name not in CACHE_NAMES)
        for name in directories:
            path = Path(directory) / name
            if path.is_symlink():
                yield path
        for name in sorted(files):
            if not name.endswith((".pyc", ".pyo")):
                yield Path(directory) / name


def project_prefixes(*roots: Path) -> list[str]:
    """Use the union so deleted packages cannot fall back to an editable install."""
    prefixes = {"conftest"}
    for root in roots:
        for source in (root, root / "src"):
            if not source.is_dir():
                continue
            for path in source.iterdir():
                if path.name in CACHE_NAMES:
                    continue
                if path.suffix == ".py" and path.stem.isidentifier():
                    prefixes.add(path.stem)
                elif path.is_dir() and path.name.isidentifier():
                    if any(child.suffix == ".py" for child in _walk(path)):
                        prefixes.add(path.name)
    return sorted(prefixes)


def _pytest_arguments(arguments: list[str]) -> tuple[list[str], list[str]]:
    """Separate targets from option values without importing pytest or plugins."""
    targets, inputs = [], []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            targets.extend(arguments[index + 1:])
            break
        option, separator, value = argument.partition("=")
        if option in PYTEST_VALUE_OPTIONS:
            if not separator and index + 1 < len(arguments):
                index += 1
                value = arguments[index]
            if option in {"-c", "--config-file", "--cov-config"} and value:
                inputs.append(value)
        elif argument.startswith("-c") and not argument.startswith("--") and len(argument) > 2:
            inputs.append(argument[2:])
        elif argument.startswith("@"):
            # Response files can contain computed target lists; retain the file
            # and use conservative discovery unless another target is explicit.
            inputs.append(argument[1:])
        elif not argument.startswith("-"):
            targets.append(argument)
        index += 1
    return targets, inputs


def _oracle_paths(root: Path, argv: list[str], extra: list[str]) -> set[Path]:
    paths = set()
    explicit_pytest_target = False

    def include(value: str, *, required: bool = False) -> None:
        value = value.split("::", 1)[0]
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        if required or path.exists() or path.is_symlink():
            paths.add(path)

    probe = root / "tests/_regression_probe"
    if probe.exists() or probe.is_symlink():
        paths.add(probe)
    mode, program, arguments = _program(argv)
    pytest = (mode == "-m" and program == "pytest") or (mode == "-c" and "pytest" in program)
    if mode in {"script", "--"} and program:
        include(program, required=True)
        pytest = Path(program).name in {"pytest", "pytest.py"}
    elif mode == "-m" and program.startswith(("tests.", "test_", "_regression_probe.")):
        module = program.replace(".", "/")
        if (root / f"{module}.py").exists():
            include(f"{module}.py", required=True)
        elif (root / module).is_dir():
            include(module, required=True)
        else:
            include(f"{module}.py", required=True)
    elif mode == "-c":
        # Literal file dependencies are observable; computed dependencies need --oracle.
        try:
            tree = ast.parse(program)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    value = node.value
                    if "\n" not in value and value.split("::", 1)[0].endswith((".py", ".pyw")):
                        include(value, required=True)
                        explicit_pytest_target = pytest
            if pytest:
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call) and node.args
                        and isinstance(node.func, ast.Attribute) and node.func.attr == "main"
                        and isinstance(node.args[0], (ast.List, ast.Tuple))
                        and all(isinstance(item, ast.Constant) and isinstance(item.value, str)
                                for item in node.args[0].elts)
                    ):
                        arguments.extend(item.value for item in node.args[0].elts)
    if pytest:
        targets, inputs = _pytest_arguments(arguments)
        explicit_pytest_target = explicit_pytest_target or bool(targets)
        for value in targets + inputs:
            include(value, required=True)
    else:
        for argument in arguments:
            value = argument.split("=", 1)[1] if argument.startswith("-") and "=" in argument else argument
            if value.startswith("-") or not value:
                continue
            include(value, required=value.split("::", 1)[0].endswith((".py", ".pyw")))
    for value in extra:
        include(value, required=True)
    if pytest and not explicit_pytest_target:
        # Without explicit selectors, retain conservative default discovery.
        # Explicit file/directory targets are already in paths; do not sweep
        # unrelated tests or their configurations into a lightweight probe.
        for path in _walk(root):
            if path.name == "conftest.py" or path.name in PYTEST_CONFIGS:
                paths.add(path)
            elif path.suffix == ".py" and (
                path.name.startswith("test_") or path.name.endswith("_test.py")
            ):
                paths.add(path)
    for path in list(paths):
        ancestors = (path, *path.parents) if path.is_dir() else path.parents
        for parent in ancestors:
            if not parent.is_relative_to(root):
                break
            names = PYTEST_CONFIGS | {"conftest.py"} if pytest else set()
            if parent != root:
                names = names | {"__init__.py"}
            for name in names:
                dependency = parent / name
                if dependency.exists() or dependency.is_symlink():
                    paths.add(dependency)
            if parent == root:
                break
    return paths


def _freeze(
    root: Path, argv: list[str], extra: list[str], evidence: Path | None, work: Path,
) -> tuple[dict, list[str]]:
    mapping, issues = {}, []
    total = 0
    files = set()
    try:
        selected_paths = _oracle_paths(root, argv, extra)
    except (OSError, ValueError) as exc:
        return {}, [f"oracle_discovery_failed: {exc}"]
    for selected in selected_paths:
        try:
            relative = selected.relative_to(root)
            if ".." in relative.parts or not selected.resolve().is_relative_to(root):
                raise ValueError("oracle path escapes its snapshot")
            if any((root / Path(*relative.parts[:i])).is_symlink()
                   for i in range(1, len(relative.parts) + 1)):
                raise ValueError("symlink oracle inputs are not supported")
            if selected.is_dir():
                files.update(_walk(selected))
            else:
                files.add(selected)
        except (OSError, ValueError) as exc:
            issues.append(f"oracle_discovery_failed: {selected}: {exc}")
    for path in sorted(files):
        try:
            relative = path.relative_to(root)
            if ".." in relative.parts or not path.resolve().is_relative_to(root):
                raise ValueError("oracle path escapes its snapshot")
            if any((root / Path(*relative.parts[:i])).is_symlink()
                   for i in range(1, len(relative.parts) + 1)):
                raise ValueError("symlink oracle inputs are not supported")
            if not path.is_file():
                raise ValueError("oracle input is missing or not a regular file")
            if len(mapping) >= MAX_ORACLE_FILES or total + path.stat().st_size > MAX_ORACLE_BYTES:
                raise ValueError("oracle evidence budget exceeded")
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
            ):
                raise ValueError("oracle input changed while being fingerprinted")
            total += len(data)
            digest = hashlib.sha256(data).hexdigest()
            item = {"sha256": digest}
            if evidence is not None:
                copied = evidence / "oracle" / digest
                copied.parent.mkdir(mode=0o700, exist_ok=True)
                if not copied.exists():
                    with copied.open("xb") as stream:
                        stream.write(data)
                    copied.chmod(0o400)
                elif copied.read_bytes() != data:
                    raise ValueError("oracle evidence content collision")
                item["evidence_path"] = copied.relative_to(work).as_posix()
            mapping[relative.as_posix()] = item
        except (OSError, ValueError) as exc:
            issues.append(f"oracle_input: {path}: {exc}")
    return mapping, issues


def verify_oracle_inputs(
    *, root: Path, argv: list[str], oracle_paths: list[str] | tuple[str, ...], frozen: dict,
) -> list[str]:
    """Detect changed, missing, or newly added inputs around paired execution."""
    current, issues = _freeze(root.resolve(), argv, list(oracle_paths), None, root)
    if {path: item["sha256"] for path, item in current.items()} != {
        path: item["sha256"] for path, item in frozen.items()
    }:
        issues.append("oracle_inputs_changed_after_fingerprint")
    return issues


def prepare_comparison(
    *, base_root: Path, candidate_root: Path, base_command: str, candidate_command: str,
    evidence: Path, work: Path, oracle_paths: list[str] | tuple[str, ...] = (),
) -> dict:
    """Freeze both oracle manifests before either side executes."""
    base_argv, base_issues = python_command(base_command)
    candidate_argv, candidate_issues = python_command(candidate_command)
    issues = [f"base: {issue}" for issue in base_issues]
    issues += [f"candidate: {issue}" for issue in candidate_issues]
    if base_argv != candidate_argv:
        issues.append("command_argv_mismatch")
    manifests = {}
    for label, root, argv in (
        ("base", base_root.resolve(), base_argv),
        ("candidate", candidate_root.resolve(), candidate_argv),
    ):
        mapping, failures = _freeze(root, argv, list(oracle_paths), evidence, work)
        manifests[label] = mapping
        issues.extend(f"{label}: {issue}" for issue in failures)
    hashes = {
        side: {path: item["sha256"] for path, item in files.items()}
        for side, files in manifests.items()
    }
    if hashes["base"] != hashes["candidate"]:
        issues.append("oracle_files_mismatch")
    return {
        "schema_version": "local-probe-comparison/v2",
        "command_argv": base_argv,
        "compatible": not issues,
        "issues": issues,
        "oracle_files": manifests,
    }
