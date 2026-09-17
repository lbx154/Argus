"""Service generation uses only caller-provided deployment paths."""
import importlib.util
import sys
from pathlib import Path


def test_generate_only_five_hosted_units_without_legacy_demo(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "deploy/trial/web_services.py"
    spec = importlib.util.spec_from_file_location("hosted_service_generator", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "operator")
    monkeypatch.setattr(sys, "argv", [str(source), "--root", str(tmp_path / "deployment")])
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: calls.append(command))
    module.main()
    units = sorted((tmp_path / "operator/.config/systemd/user").glob("*.service"))
    assert {unit.stem for unit in units} == {
        "argus-web-trial-meter", "argus-web-trial-egress", "argus-web-trial-compute",
        "argus-web-trial-relay-guardian", "argus-web-trial-portal",
    }
    assert len(calls) == 2 and calls[0] == ["systemctl", "--user", "daemon-reload"]
    for unit in units:
        content = unit.read_text()
        assert str(tmp_path / "deployment") in content
        assert "demo_backend" not in content
        assert unit.stat().st_mode & 0o777 == 0o600
