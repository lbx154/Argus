"""Trusted-source Python provenance guard, not a malicious-code sandbox.

Loaded by Python's normal site initialization. Missing initialization, abrupt
exit, unsupported launchers, and guard-dropping children are incomplete evidence.
"""

import atexit
import hashlib
import json
import os
import re
import sys
from pathlib import Path


def _install():
    root = Path(os.environ["PR_GATE_SOURCE_ROOT"]).resolve()
    context = os.environ["PR_GATE_CONTEXT_ID"]
    directory = Path(os.environ["PR_GATE_ORIGIN_TRACE_DIR"])
    prefixes = set(json.loads(os.environ["PR_GATE_PROJECT_PREFIXES"]))
    oracle_paths = set(json.loads(os.environ["PR_GATE_ORACLE_PATHS"]))
    controlled_keys = (
        "PR_GATE_SOURCE_ROOT", "PR_GATE_CONTEXT_ID", "PR_GATE_ORIGIN_TRACE_DIR",
        "PR_GATE_PROJECT_PREFIXES", "PR_GATE_ORACLE_PATHS", "PYTHONPATH",
    )
    controlled = {key: os.environ[key] for key in controlled_keys}
    seen, issues = {}, set()
    busy = False

    def trace(event, **fields):
        record = dict(event=event, pid=os.getpid(), context_id=context, **fields)
        descriptor = os.open(
            directory / f"{os.getpid()}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600,
        )
        data = (json.dumps(record, sort_keys=True) + "\n").encode()
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)

    def issue(message):
        if message not in issues:
            issues.add(message)
            trace("issue", message=message)

    def product_name(name):
        return name.split(".", 1)[0] in prefixes

    def oracle_file(relative):
        return (
            relative in oracle_paths
            or relative.startswith("tests/")
            or relative.startswith("test/")
            or Path(relative).name == "conftest.py"
            or Path(relative).name.startswith("test_")
            or Path(relative).name.endswith("_test.py")
        )

    def source_file(filename, name, *, reject=True, record=True):
        if not filename or filename in {"built-in", "frozen", "namespace"}:
            if product_name(name):
                issue(f"untraceable_project_module: {name}")
            return
        path = Path(filename).resolve()
        if not path.is_relative_to(root):
            issue(f"external_project_origin: {name}: {path}")
            if reject:
                raise ImportError(f"Project module {name} is outside its assigned snapshot: {path}")
            return
        relative = path.relative_to(root).as_posix()
        if path.suffix != ".py":
            issue(f"unsupported_project_source: {name}: {relative}")
            if reject:
                raise ImportError(f"Project module {name} has no traceable Python source")
            return
        if oracle_file(relative):
            if record and relative not in oracle_paths:
                issue(f"unfingerprinted_oracle_source: {relative}")
            return
        if not record:
            return
        try:
            data = path.read_bytes()
        except OSError as exc:
            issue(f"unreadable_project_source: {relative}: {exc}")
            if reject:
                raise ImportError(f"Cannot fingerprint project source: {relative}") from exc
            return
        normalized = data.replace(b"\r\n", b"\n")

        def blob(content):
            return hashlib.sha1(
                b"blob " + str(len(content)).encode("ascii") + b"\0" + content,
            ).hexdigest()

        entry = {"path": relative, "git_blob": blob(data), "lf_git_blob": blob(normalized)}
        if relative in seen:
            if seen[relative] != entry:
                issue(f"project_source_changed_during_execution: {relative}")
            return
        seen[relative] = entry
        trace("file", module=name, **entry)

    def check_spec(name, spec, *, reject=True, record=True):
        locations = getattr(spec, "submodule_search_locations", None)
        if locations is not None:
            for location in locations:
                if not Path(location).resolve().is_relative_to(root):
                    issue(f"external_project_package_path: {name}: {location}")
                    if reject:
                        raise ImportError(f"Project package {name} escapes its assigned snapshot")
        origin = getattr(spec, "origin", None)
        if origin is not None:
            source_file(origin, name, reject=reject, record=record)
        elif not locations:
            issue(f"untraceable_project_module: {name}")
        cached = getattr(spec, "cached", None)
        if cached and Path(cached).is_file():
            issue(f"project_bytecode_cache_present: {name}")

    def scan_modules():
        for name, module in list(sys.modules.items()):
            if module is None or not product_name(name) or name == "sitecustomize":
                continue
            spec = getattr(module, "__spec__", None)
            if spec is not None:
                check_spec(name, spec, reject=False)
            filename = getattr(module, "__file__", None)
            if filename:
                source_file(filename, name, reject=False)
            elif spec is None:
                issue(f"untraceable_preloaded_project_module: {name}")

    trace("startup", ppid=os.getppid(), source_root=str(root))
    # Editable .pth files run before sitecustomize. Repair path priority, but
    # never quietly accept project modules those files have already imported.
    assigned = [str(root / "src"), str(root)]
    sys.path[:] = assigned + [item for item in sys.path if item not in assigned]
    scan_modules()
    if issues:
        trace("issue", message="preloaded_project_origin_could_not_be_verified")
        raise SystemExit(86)

    class GuardFinder:
        def find_spec(self, fullname, path=None, target=None):
            nonlocal busy
            if busy or not product_name(fullname):
                return None
            busy = True
            try:
                for finder in list(sys.meta_path):
                    if finder is self:
                        continue
                    method = getattr(finder, "find_spec", None)
                    if method is None:
                        issue(f"unsupported_project_finder: {fullname}")
                        continue
                    spec = method(fullname, path, target)
                    if spec is not None:
                        check_spec(fullname, spec, record=False)
                        return spec
                return None
            finally:
                busy = False

    sys.meta_path.insert(0, GuardFinder())

    def audit_child(executable, argv, environment):
        try:
            executable = os.fsdecode(executable)
            arguments = [os.fsdecode(value) for value in argv]
            supplied = os.environ if environment is None else {
                os.fsdecode(key): os.fsdecode(value) for key, value in environment.items()
            }
        except (TypeError, ValueError, AttributeError) as exc:
            issue(f"untraceable_child_arguments: {exc}")
            return
        if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", Path(executable).name):
            issue(f"untracked_child_executor: {executable}")
            return
        valid = True
        for key, value in controlled.items():
            if supplied.get(key) != value:
                issue(f"python_child_guard_environment_changed: {key}")
                valid = False
        for option in arguments[1:]:
            if option in {"-c", "-m", "--"} or not option.startswith("-"):
                break
            if option not in {"-B", "-u", "-O", "-OO", "-s", "-q"}:
                issue(f"python_child_untraceable_option: {option}")
                valid = False
        trace("python_child", guarded=valid)

    def audit(event, arguments):
        nonlocal busy
        if busy:
            return
        busy = True
        try:
            if event == "subprocess.Popen":
                audit_child(arguments[0], arguments[1], arguments[3])
            elif event == "os.posix_spawn":
                audit_child(arguments[0], arguments[1], arguments[2])
            elif event in {"os.system", "os.exec", "os.fork", "os.forkpty"}:
                issue(f"untracked_process_operation: {event}")
            elif event == "open":
                filename, _mode, flags = arguments
                if flags & os.O_ACCMODE == os.O_WRONLY:
                    return
                if isinstance(filename, (str, bytes, os.PathLike)):
                    path = Path(os.path.abspath(os.fsdecode(filename)))
                    if not path.is_file():
                        return
                    if path.is_relative_to(root):
                        relative = path.relative_to(root).as_posix()
                        if path.suffix != ".py" and relative not in oracle_paths:
                            issue(f"unfingerprinted_data_input: {relative}; declare with --oracle")
                    if "pytest" in sys.modules and path.name in {
                        "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg",
                    }:
                        if (
                            not path.is_relative_to(root)
                            or path.relative_to(root).as_posix() not in oracle_paths
                        ):
                            issue(f"unfingerprinted_pytest_configuration: {path}")
            elif event == "exec":
                filename = arguments[0].co_filename
                if not filename or filename.startswith("<"):
                    return
                path = Path(filename).resolve()
                relative = path.relative_to(root) if path.is_relative_to(root) else None
                if relative is not None:
                    source_file(filename, "__execution__")
                elif path.stem in prefixes or any(part in prefixes for part in path.parts[:-1]):
                    source_file(filename, "__external_execution__")
        finally:
            busy = False

    sys.addaudithook(audit)

    def complete():
        scan_modules()
        trace("completion", source_root=str(root))

    atexit.register(complete)


if os.environ.get("PR_GATE_ORIGIN_TRACE_DIR"):
    _install()
