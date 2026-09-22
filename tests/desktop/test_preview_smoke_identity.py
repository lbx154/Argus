"""The manual preview smoke retains the existing native single-instance boundary."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def smoke_module():
    spec = importlib.util.spec_from_file_location("preview_host_smoke", ROOT / "desktop-tauri/scripts/smoke-host.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_smoke_removes_bypass_and_uses_validated_random_namespace():
    env = {"ARGUS_DESKTOP_DISABLE_SINGLE_INSTANCE": "1"}
    smoke_module().configure_test_instance(env, preview=True)
    assert "ARGUS_DESKTOP_DISABLE_SINGLE_INSTANCE" not in env
    namespace = env["ARGUS_DESKTOP_TEST_INSTANCE"]
    assert len(namespace) == 32
    assert all(char in "0123456789abcdef" for char in namespace)


def test_preview_namespaces_are_per_run():
    first, second = {}, {}
    module = smoke_module()
    module.configure_test_instance(first, preview=True)
    module.configure_test_instance(second, preview=True)
    assert first != second


def test_non_preview_isolated_smoke_contract_is_unchanged():
    env = {}
    smoke_module().configure_test_instance(env, preview=False)
    assert env["ARGUS_DESKTOP_DISABLE_SINGLE_INSTANCE"] == "1"
    assert env["ARGUS_DESKTOP_TEST_INSTANCE"]
