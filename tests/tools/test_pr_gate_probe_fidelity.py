"""Offline paired-oracle and Python-origin protocol tests; never model calls."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

import pytest

from argus.release_tools.pr_gate.regression import oracle, probe

DRIVER = "import snapshot_product\nassert snapshot_product.VALUE == 1\n"


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "WORK", tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    probe.STOP.clear()
    for label, value in (("base", 1), ("candidate", 2)):
        root = tmp_path / label
        (root / "src/snapshot_product").mkdir(parents=True)
        (root / "src/snapshot_product/__init__.py").write_text(f"VALUE = {value}\n")
        (root / "tests/_regression_probe").mkdir(parents=True)
        (root / "tests/_regression_probe/check.py").write_text(DRIVER)
    return tmp_path


def pair(work, command=None, *, candidate_command=None, oracle_paths=()):
    directory = work / "evidence/pair"
    directory.mkdir(parents=True)
    command = command or shlex.join([sys.executable, "tests/_regression_probe/check.py"])
    return probe.run_pair(
        base_root=work / "base", candidate_root=work / "candidate",
        base_command=command, candidate_command=candidate_command or command,
        directory=directory, oracle_paths=oracle_paths,
    )


def origin(work, receipt, label="base"):
    observation = receipt[label]
    descriptor = observation["source_origin"]
    data = (work / descriptor["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == descriptor["sha256"]
    document = json.loads(data)
    assert document["status"] == descriptor["status"]
    assert document["main_pid"] == observation["process_pid"]
    return document


def replace_driver(work, program):
    for side in ("base", "candidate"):
        (work / side / "tests/_regression_probe/check.py").write_text(program)


def private_python(work, pth):
    environment = work / "private-python"
    venv.EnvBuilder(with_pip=False).create(environment)
    site_packages = next(environment.glob("lib/python*/site-packages"))
    (site_packages / "private-editable.pth").write_text(pth + "\n")
    return environment / "bin/python"


def developer_product(work):
    source = work / "developer/src"
    (source / "snapshot_product").mkdir(parents=True)
    (source / "snapshot_product/__init__.py").write_text("VALUE = 99\n")
    return source


def test_identical_oracle_accepts_a_real_paired_behavior_difference(work):
    receipt = pair(work)
    assert receipt["comparison"]["compatible"], receipt
    assert receipt["base"]["exit_code"] == 0
    assert receipt["candidate"]["exit_code"] == 1
    for side in ("base", "candidate"):
        observation = receipt[side]
        document = origin(work, receipt, side)
        assert document["status"] == "verified", document
        assert document["guard_loaded"] and document["completed"]
        assert document["main_pid"] > 0
        assert observation["execution_error"] is None
        assert not observation["interrupted"]
        assert observation["cleanup_complete"]
        assert document["files"] == [{
            "path": "src/snapshot_product/__init__.py",
            "git_blob": git_blob((work / side / "src/snapshot_product/__init__.py").read_bytes()),
            "lf_git_blob": git_blob((work / side / "src/snapshot_product/__init__.py").read_bytes()),
        }]
    mapping = receipt["comparison"]["oracle_files"]
    assert mapping["base"] == mapping["candidate"]
    frozen = work / mapping["base"]["tests/_regression_probe/check.py"]["evidence_path"]
    assert frozen.read_text() == DRIVER
    assert frozen.stat().st_mode & 0o222 == 0
    assert not list((work / "evidence/pair").glob(".pr-probe-*"))


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def test_same_command_with_different_oracle_never_executes_either_side(work):
    (work / "candidate/tests/_regression_probe/check.py").write_text(
        DRIVER.replace("== 1", "== 2"),
    )
    receipt = pair(work)
    assert not receipt["comparison"]["compatible"]
    assert "oracle_files_mismatch" in receipt["comparison"]["issues"]
    for side in ("base", "candidate"):
        assert receipt[side]["process_pid"] == 0
        assert receipt[side]["execution_error"].startswith("not_run:")
        assert origin(work, receipt, side)["status"] == "incomplete"


@pytest.mark.parametrize("relative", ["driver.py", "fixtures/value.json"])
def test_explicit_driver_and_extra_fixture_inputs_are_fingerprinted(work, relative):
    for side in ("base", "candidate"):
        path = work / side / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DRIVER if relative.endswith(".py") else side)
    command = shlex.join([sys.executable, "driver.py"]) if relative.endswith(".py") else None
    receipt = pair(work, command, oracle_paths=[relative])
    assert relative in receipt["comparison"]["oracle_files"]["base"]
    assert receipt["comparison"]["compatible"] == relative.endswith(".py")


@pytest.mark.parametrize("relative", ["tests/fixtures/expected.json", "config/expected.json"])
@pytest.mark.parametrize("read", [
    "Path(filename).read_text()",
    "Path(filename).read_bytes()",
    "open(filename, 'r+').read()",
    "open(filename, 'rb').read()",
    "os.read(os.open(os.fsencode(filename), os.O_RDONLY), 100)",
    "os.read(os.open(filename, os.O_RDWR), 100)",
])
def test_undeclared_snapshot_data_makes_successful_probes_incomplete(work, relative, read):
    for side, value in (("base", 1), ("candidate", 2)):
        path = work / side / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"expected": value}))
    replace_driver(work, (
        "import json, os, snapshot_product\nfrom pathlib import Path\n"
        f"filename = {relative!r}\n"
        f"assert snapshot_product.VALUE == json.loads({read})['expected']\n"
    ))
    receipt = pair(work)
    assert receipt["comparison"]["compatible"]
    for side in ("base", "candidate"):
        assert receipt[side]["exit_code"] == 0
        assert receipt[side]["execution_error"] == "source_origin_incomplete"
        document = origin(work, receipt, side)
        assert document["status"] == "incomplete"
        assert f"unfingerprinted_data_input: {relative}; declare with --oracle" in document["issues"]


@pytest.mark.parametrize("different", [False, True])
def test_declared_data_uses_existing_oracle_equality_check(work, different):
    relative = "tests/fixtures/expected.json"
    for side in ("base", "candidate"):
        path = work / side / relative
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"expected": 2 if different and side == "candidate" else 1}))
    replace_driver(work, (
        "import json, snapshot_product\nfrom pathlib import Path\n"
        f"expected = json.loads(Path({relative!r}).read_text())\n"
        "assert snapshot_product.VALUE == expected['expected']\n"
    ))
    receipt = pair(work, oracle_paths=[relative])
    assert receipt["comparison"]["compatible"] is not different
    for side in ("base", "candidate"):
        if different:
            assert receipt[side]["process_pid"] == 0
            assert "oracle_files_mismatch" in receipt["comparison"]["issues"]
        else:
            assert origin(work, receipt, side)["status"] == "verified"
            assert receipt[side]["exit_code"] == int(side == "candidate")


def test_data_written_only_or_read_from_private_temporary_state_is_not_an_input(work):
    for side in ("base", "candidate"):
        (work / side / "output.json").write_text("old output\n")
    replace_driver(work, (
        "import os, snapshot_product, tempfile\nfrom pathlib import Path\n"
        "Path('output.json').write_text('new output\\n')\n"
        "descriptor = os.open('new-output.bin', os.O_WRONLY | os.O_CREAT, 0o600)\n"
        "os.write(descriptor, b'output')\nos.close(descriptor)\n"
        "with tempfile.TemporaryDirectory() as state:\n"
        "    output = Path(state) / 'result.json'\n"
        "    output.write_text('temporary result')\n"
        "    assert output.read_text() == 'temporary result'\n"
        "try:\n"
        "    Path('missing.json').read_text()\n"
        "except FileNotFoundError:\n"
        "    pass\n"
    ))
    receipt = pair(work)
    for side in ("base", "candidate"):
        assert receipt[side]["exit_code"] == 0
        assert origin(work, receipt, side)["status"] == "verified"


@pytest.mark.parametrize("relative", ["pytest.ini", "tests/conftest.py"])
def test_pytest_selectors_include_configuration_and_conftests(work, relative):
    for side in ("base", "candidate"):
        (work / side / relative).write_text(f"# {side}\n")
    receipt = pair(work, shlex.join([
        sys.executable, "-m", "pytest", "tests/_regression_probe/check.py::test_value",
    ]))
    assert not receipt["comparison"]["compatible"]
    assert relative in receipt["comparison"]["oracle_files"]["base"]


def pytest_comparison(work, arguments):
    directory = work / "evidence/selection"
    directory.mkdir(parents=True)
    command = shlex.join([sys.executable, "-m", "pytest", *arguments])
    return oracle.prepare_comparison(
        base_root=work / "base", candidate_root=work / "candidate",
        base_command=command, candidate_command=command, evidence=directory, work=work,
    )


@pytest.mark.parametrize("arguments", [
    ["tests/selected/test_value.py::test_value"],
    ["--rootdir", ".", "--confcutdir", "tests", "tests/selected"],
    ["-k", "value", "--rootdir=.", "-c", "pytest.ini", "tests/selected/test_value.py"],
])
def test_explicit_pytest_target_excludes_large_changed_unselected_tests(work, arguments):
    for side in ("base", "candidate"):
        root = work / side
        (root / "tests/selected").mkdir()
        (root / "tests/unselected").mkdir()
        (root / "pytest.ini").write_text("[pytest]\n")
        (root / "tests/__init__.py").write_text("")
        (root / "tests/conftest.py").write_text("# Shared ancestor.\n")
        (root / "tests/selected/__init__.py").write_text("")
        (root / "tests/selected/test_value.py").write_text("def test_value(): pass\n")
        (root / "tests/selected/fixture.json").write_text('{"value": 1}\n')
        (root / "tests/unselected/test_large.py").write_text(
            f"# {side}\n" + "#" * (5 * 1024 * 1024),
        )
        (root / "tests/unselected/conftest.py").write_text(f"# {side}\n")
        (root / "tests/unselected/pytest.ini").write_text(f"# {side}\n")
    comparison = pytest_comparison(work, arguments)
    assert comparison["compatible"], comparison["issues"]
    files = comparison["oracle_files"]["base"]
    assert {
        "pytest.ini", "tests/conftest.py", "tests/__init__.py",
        "tests/selected/__init__.py", "tests/selected/test_value.py",
        "tests/_regression_probe/check.py",
    } <= files.keys()
    assert not any(path.startswith("tests/unselected/") for path in files)
    assert ("tests/selected/fixture.json" in files) == ("tests/selected" in arguments)
    assert sum(path.stat().st_size for path in (work / "evidence/selection/oracle").iterdir()) < 4096
    for side in ("base", "candidate"):
        assert oracle.verify_oracle_inputs(
            root=work / side, argv=comparison["command_argv"], oracle_paths=(),
            frozen=comparison["oracle_files"][side],
        ) == []


@pytest.mark.parametrize("changed", [
    "tests/selected/test_value.py",
    "tests/selected/conftest.py",
    "tests/conftest.py",
    "conftest.py",
    "tests/__init__.py",
    "pytest.ini",
])
def test_explicit_pytest_target_still_rejects_selected_or_ancestor_changes(work, changed):
    for side in ("base", "candidate"):
        root = work / side
        (root / "tests/selected").mkdir()
        (root / "tests/selected/test_value.py").write_text("def test_value(): pass\n")
        (root / changed).write_text(f"# {side}\n")
    comparison = pytest_comparison(work, ["tests/selected/test_value.py::test_value"])
    assert not comparison["compatible"]
    assert "oracle_files_mismatch" in comparison["issues"]
    assert changed in comparison["oracle_files"]["base"]


def test_pytest_without_explicit_target_retains_conservative_discovery(work):
    for side in ("base", "candidate"):
        (work / side / "tests/test_discovered.py").write_text(f"# {side}\n")
    comparison = pytest_comparison(work, ["--rootdir", ".", "-k", "discovered"])
    assert not comparison["compatible"]
    assert "tests/test_discovered.py" in comparison["oracle_files"]["base"]
    assert "src/snapshot_product/__init__.py" not in comparison["oracle_files"]["base"]


def test_inline_pytest_directory_selector_does_not_sweep_unselected_tests(work):
    for side in ("base", "candidate"):
        root = work / side
        (root / "tests/selected").mkdir()
        (root / "tests/selected/test_value.py").write_text("def test_value(): pass\n")
        (root / "tests/test_unselected.py").write_text(f"# {side}\n")
    directory = work / "evidence/selection"
    directory.mkdir(parents=True)
    command = shlex.join([
        sys.executable, "-c", "import pytest; pytest.main(['tests/selected'])",
    ])
    comparison = oracle.prepare_comparison(
        base_root=work / "base", candidate_root=work / "candidate",
        base_command=command, candidate_command=command, evidence=directory, work=work,
    )
    assert comparison["compatible"], comparison["issues"]
    assert "tests/test_unselected.py" not in comparison["oracle_files"]["base"]


def test_product_module_is_not_misclassified_as_a_changed_oracle(work):
    receipt = pair(work, shlex.join([sys.executable, "-m", "snapshot_product"]))
    assert receipt["comparison"]["compatible"]
    assert "src/snapshot_product/__init__.py" not in receipt["comparison"]["oracle_files"]["base"]


def test_private_editable_pth_cannot_select_developer_src_in_both_sides(work):
    source = developer_product(work)
    executable = private_python(work, f"import sys; sys.path.insert(0, {str(source)!r})")
    receipt = pair(work, shlex.join([str(executable), "tests/_regression_probe/check.py"]))
    assert receipt["comparison"]["compatible"]
    assert receipt["base"]["exit_code"] == 0, receipt
    assert receipt["candidate"]["exit_code"] == 1, receipt
    for side in ("base", "candidate"):
        document = origin(work, receipt, side)
        assert document["status"] == "verified", document
        assert all("developer" not in entry["path"] for entry in document["files"])


def test_project_module_preloaded_by_editable_pth_fails_closed(work):
    source = developer_product(work)
    executable = private_python(
        work, f"import sys; sys.path.insert(0, {str(source)!r}); import snapshot_product",
    )
    receipt = pair(work, shlex.join([str(executable), "tests/_regression_probe/check.py"]))
    for side in ("base", "candidate"):
        document = origin(work, receipt, side)
        assert document["status"] == "incomplete"
        assert any("external_project_origin" in issue for issue in document["issues"]), document
        assert receipt[side]["execution_error"]
        assert receipt[side]["exit_code"] != 0


def test_editable_meta_path_finder_cannot_override_snapshot_sources(work):
    source = developer_product(work)
    executable = private_python(work, "import editable_finder")
    site_packages = next(executable.parent.parent.glob("lib/python*/site-packages"))
    (site_packages / "editable_finder.py").write_text(
        "import importlib.util, sys\n"
        "class Finder:\n"
        "    @classmethod\n"
        "    def find_spec(cls, fullname, path=None, target=None):\n"
        "        if fullname == 'snapshot_product':\n"
        f"            return importlib.util.spec_from_file_location(fullname, {str(source / 'snapshot_product/__init__.py')!r})\n"
        "sys.meta_path.insert(0, Finder)\n",
    )
    receipt = pair(work, shlex.join([str(executable), "tests/_regression_probe/check.py"]))
    for side in ("base", "candidate"):
        document = origin(work, receipt, side)
        assert document["status"] == "incomplete"
        assert any("external_project_" in issue for issue in document["issues"]), document


def test_deleted_package_cannot_fall_back_to_editable_install(work):
    source = developer_product(work)
    executable = private_python(work, str(source))
    shutil.rmtree(work / "candidate/src/snapshot_product")
    receipt = pair(work, shlex.join([str(executable), "tests/_regression_probe/check.py"]))
    assert receipt["base"]["source_origin"]["status"] == "verified"
    document = origin(work, receipt, "candidate")
    assert document["status"] == "incomplete"
    assert any("external_project_" in issue for issue in document["issues"]), document


@pytest.mark.parametrize("option", ["-I", "-S", "-E", "-sI"])
def test_guard_dropping_python_children_make_evidence_incomplete(work, option):
    replace_driver(work, (
        "import snapshot_product, subprocess, sys\n"
        f"subprocess.run([sys.executable, {option!r}, '-c', 'pass'], check=True)\n"
    ))
    receipt = pair(work)
    for side in ("base", "candidate"):
        document = origin(work, receipt, side)
        assert document["status"] == "incomplete"
        assert any("python_child_untraceable_option" in issue for issue in document["issues"])
        assert receipt[side]["execution_error"] == "source_origin_incomplete"


def test_overridden_python_child_environment_is_not_certified(work):
    replace_driver(work, (
        "import snapshot_product, subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'pass'], env={}, check=True)\n"
    ))
    receipt = pair(work)
    document = origin(work, receipt)
    assert document["status"] == "incomplete"
    assert any("python_child_guard_environment_changed" in issue for issue in document["issues"])


def test_normal_python_children_inherit_guard_and_are_correlated(work):
    replace_driver(work, (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'import snapshot_product'], check=True)\n"
    ))
    receipt = pair(work)
    for side in ("base", "candidate"):
        document = origin(work, receipt, side)
        assert document["status"] == "verified", document
        assert document["process_count"] == 2


@pytest.mark.parametrize("code,expected", [
    ("print('only harness code')", "no_observable_product_source"),
    ("import importlib.util; importlib.util.find_spec('snapshot_product')",
     "no_observable_product_source"),
    ("import snapshot_product, os; os._exit(0)", "main_guard_completion_missing"),
])
def test_missing_execution_or_completion_is_incomplete(work, code, expected):
    receipt = pair(work, shlex.join([sys.executable, "-c", code]))
    document = origin(work, receipt)
    assert document["status"] == "incomplete"
    assert expected in document["issues"], document


def test_missing_guard_does_not_certify_successful_python(work, monkeypatch):
    monkeypatch.setattr(probe, "__file__", str(work / "missing-runner/probe.py"))
    receipt = pair(work)
    assert receipt["base"]["exit_code"] == 0
    document = origin(work, receipt)
    assert document["status"] == "incomplete"
    assert "main_guard_startup_missing" in document["issues"]


@pytest.mark.parametrize("command", [
    "python -c 'import snapshot_product' && python -c 'pass'",
    "python -I -c 'import snapshot_product'",
    "bash -c 'true'",
])
def test_untraceable_top_level_commands_are_not_executed(work, command):
    receipt = pair(work, command)
    assert not receipt["comparison"]["compatible"]
    assert receipt["base"]["process_pid"] == receipt["candidate"]["process_pid"] == 0


def test_different_side_commands_are_not_comparative_evidence(work):
    receipt = pair(
        work, "python -c 'import snapshot_product'",
        candidate_command="python -c 'import snapshot_product; assert False'",
    )
    assert "command_argv_mismatch" in receipt["comparison"]["issues"]
    assert receipt["base"]["process_pid"] == 0


def test_raw_and_lf_normalized_source_blobs_are_retained(work):
    for side in ("base", "candidate"):
        (work / side / "src/snapshot_product/__init__.py").write_bytes(b"VALUE = 1\r\n")
    receipt = pair(work)
    entry = origin(work, receipt)["files"][0]
    assert entry["git_blob"] == git_blob(b"VALUE = 1\r\n")
    assert entry["lf_git_blob"] == git_blob(b"VALUE = 1\n")
    assert entry["git_blob"] != entry["lf_git_blob"]


def test_oracle_change_during_base_execution_skips_candidate_and_preserves_copy(work):
    original = (
        "import snapshot_product\n"
        "from pathlib import Path\n"
        "Path(__file__).write_text('pass\\n')\n"
    )
    replace_driver(work, original)
    receipt = pair(work)
    assert receipt["base"]["process_pid"] > 0
    assert receipt["candidate"]["process_pid"] == 0
    assert not receipt["comparison"]["compatible"]
    assert any("oracle_inputs_changed" in issue for issue in receipt["comparison"]["issues"])
    assert origin(work, receipt)["status"] == "incomplete"
    copied = receipt["comparison"]["oracle_files"]["base"]["tests/_regression_probe/check.py"]
    assert (work / copied["evidence_path"]).read_text() == original


def test_real_pytest_selector_uses_identical_retained_oracle(work):
    for side in ("base", "candidate"):
        (work / side / "pytest.ini").write_text("[pytest]\n")
    replace_driver(work, (
        "import snapshot_product\n"
        "def test_value():\n"
        "    assert snapshot_product.VALUE == 1\n"
    ))
    receipt = pair(work, shlex.join([
        sys.executable, "-m", "pytest", "-q",
        "tests/_regression_probe/check.py::test_value",
    ]))
    assert receipt["comparison"]["compatible"], receipt
    assert receipt["base"]["exit_code"] == 0, (work / receipt["base"]["log"]).read_text()
    assert receipt["candidate"]["exit_code"] == 1
    assert origin(work, receipt)["status"] == "verified"
    assert origin(work, receipt, "candidate")["status"] == "verified"


def test_repeated_pytest_probes_keep_cache_outside_snapshot_inputs(work):
    replace_driver(work, (
        "import snapshot_product\n"
        "def test_value(pytestconfig):\n"
        "    assert pytestconfig.cache.get('probe/value', None) is None\n"
        "    pytestconfig.cache.set('probe/value', snapshot_product.VALUE)\n"
        "    assert snapshot_product.VALUE == 1\n"
    ))
    command = shlex.join([
        sys.executable, "-m", "pytest", "-q", "tests/_regression_probe/check.py",
    ])
    for number in range(2):
        directory = work / f"evidence/repeat-{number}"
        directory.mkdir(parents=True)
        receipt = probe.run_pair(
            base_root=work / "base", candidate_root=work / "candidate",
            base_command=command, candidate_command=command, directory=directory,
        )
        for side in ("base", "candidate"):
            assert receipt[side]["exit_code"] == int(side == "candidate")
            assert origin(work, receipt, side)["status"] == "verified"
            assert not (work / side / ".pytest_cache").exists()


def test_escaping_or_symlink_oracle_inputs_are_not_certified(work):
    divergent = DRIVER.replace("== 1", "== 2")
    (work / "shared.py").write_text(divergent)
    for side in ("base", "candidate"):
        (work / side / "extra.py").symlink_to(work / "shared.py")
    receipt = pair(work, oracle_paths=["extra.py", ".."])
    assert not receipt["comparison"]["compatible"]
    assert receipt["base"]["process_pid"] == 0
    assert (work / "shared.py").read_text() == divergent


def test_undeclared_test_helper_is_incomplete_instead_of_an_unfrozen_oracle(work):
    for side in ("base", "candidate"):
        (work / side / "tests/__init__.py").write_text("")
        (work / side / "tests/helper.py").write_text(f"VALUE = {side!r}\n")
    replace_driver(work, "import snapshot_product\nfrom tests import helper\n")
    receipt = pair(work)
    assert receipt["comparison"]["compatible"]
    document = origin(work, receipt)
    assert document["status"] == "incomplete"
    assert any("unfingerprinted_oracle_source" in issue for issue in document["issues"])


def test_pytest_cannot_silently_consume_unfrozen_parent_configuration(work):
    (work / "pytest.ini").write_text("[pytest]\n")
    receipt = pair(work, shlex.join([
        sys.executable, "-m", "pytest", "-q", "tests/_regression_probe/check.py",
    ]))
    document = origin(work, receipt)
    assert document["status"] == "incomplete"
    assert any("unfingerprinted_pytest_configuration" in issue for issue in document["issues"])


@pytest.mark.parametrize("code,error", [
    ("import sys; sys.exit(1)", None),
    ("import os,signal; os.kill(os.getpid(), signal.SIGKILL)", "signal_terminated"),
    (f"import sys; sys.stdout.write('x' * {probe.OUTPUT_LIMIT_BYTES * 3})", "output_limit"),
])
def test_legacy_run_one_preserves_exit_signal_and_output_cap_contract(work, code, error):
    observation = probe.run_one(
        root=work / "base", command=shlex.join([sys.executable, "-c", code]),
        log=work / "legacy.log",
    )
    assert observation["execution_error"] == error
    assert observation["cleanup_complete"]
    assert (work / "legacy.log").stat().st_size <= probe.OUTPUT_LIMIT_BYTES


def test_standalone_command_activates_local_v2_from_input(work):
    runner = work / "runner"
    runner.mkdir()
    source = Path(probe.__file__).parent
    for name in ("probe.py", "probe_contract.py", "oracle.py", "origin_guard/sitecustomize.py"):
        destination = runner / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    shutil.copy2(source.parent / "owned_process.py", runner / "owned_process.py")
    (work / "input.json").write_text(json.dumps({
        "base_sha": "a" * 40, "candidate_sha": "b" * 40, "local_gate": True,
    }))
    result = subprocess.run(
        [sys.executable, str(runner / "probe.py"), "--id", "standalone",
         "--command", shlex.join([sys.executable, "tests/_regression_probe/check.py"])],
        cwd=work, env={
            "PATH": os.environ["PATH"], "PR_GATE_WORK_ROOT": str(work),
            "TMPDIR": str(work), "PYTHONDONTWRITEBYTECODE": "1",
        }, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((work / "evidence/standalone/receipt.json").read_text())
    assert receipt["comparison"]["schema_version"] == "local-probe-comparison/v2"
    assert receipt["comparison"]["compatible"]
    assert origin(work, receipt)["status"] == "verified"


def test_python_command_uses_argv_not_quoting_style():
    first, issues = oracle.python_command('python -c "import snapshot_product"')
    second, other_issues = oracle.python_command("python -c 'import snapshot_product'")
    assert first == second and not issues and not other_issues
