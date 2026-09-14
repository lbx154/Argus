"""Real Pi/Jiti loading, including extensions extracted from the actual wheel.

Set ARGUS_PI_TEST_CLI to the bundled CLI. A session_start probe exits before
any provider request; its only configured provider is a local counting fixture.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PI_CLI = os.environ.get("ARGUS_PI_TEST_CLI")
ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {"consult_advisor", "list_peer_projects", "send_peer_message", "peer_message_status",
            "search_experiences", "get_experience", "revise_experience", "retract_experience",
            "list_learned_tools", "run_learned_tool", "evolve_runtime", "rollback_runtime"}


@pytest.fixture(scope="module")
def wheel_root(tmp_path_factory):
    directory = tmp_path_factory.mktemp("role-extension-wheel")
    output = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(directory)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert output.returncode == 0, output.stdout + output.stderr
    wheel, = directory.glob("*.whl")
    required = {
        "argus_skill/advisor/pi_extension.mjs", "argus_skill/advisor/pi_tools.mjs",
        "argus_skill/messaging/pi_extension.mjs", "argus_skill/messaging/pi_tools.mjs",
        "argus_skill/core/role_tool_bridge.mjs", "argus_skill/core/role_tool_bridge.py",
        "argus_skill/core/scoped_file.py",
        "argus_skill/tools/advisor.py", "argus_skill/tools/peer.py",
        "argus_skill/tools/experience.py", "argus_skill/life/experience_tools.py",
        "argus_skill/life/experience_runtime.py", "argus_skill/life/experience_extension.mjs",
        "argus_skill/life/experience_pi_tools.mjs",
        "argus_skill/skills/runtime_extension.mjs", "argus_skill/skills/runtime_pi_tools.mjs",
        "argus_skill/skills/runtime_worker.py", "argus_skill/skills/runtime_tools.py",
        "argus_skill/skills/runtime_tools_context.py",
        *[str(path.relative_to(ROOT)) for package in ("advisor", "messaging")
          for path in (ROOT / "argus_skill" / package).glob("*.py")],
    }
    extracted = directory / "installed"
    with zipfile.ZipFile(wheel) as archive:
        assert required <= set(archive.namelist()), sorted(required - set(archive.namelist()))
        archive.extractall(extracted)
    return extracted


@pytest.mark.skipif(not PI_CLI, reason="Set ARGUS_PI_TEST_CLI to validate the actual bundled Pi loader")
@pytest.mark.parametrize("package_source", ["checkout", "wheel"])
def test_bundle_loads_composed_role_tools_before_any_provider_request(tmp_path, request, package_source):
    package_root = request.getfixturevalue("wheel_root") if package_source == "wheel" else ROOT
    node = shutil.which("node")
    assert node and PI_CLI and Path(PI_CLI).is_file()
    provider_requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            provider_requests.append(self.path)
            self.send_response(500)
            self.end_headers()

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text(json.dumps({"providers": {"argus-loader-test": {
        "baseUrl": f"http://127.0.0.1:{provider.server_port}/v1", "api": "openai-completions",
        "apiKey": "offline-loader-fixture", "models": [{"id": "loader-only", "reasoning": False}],
    }}}))
    receipt = tmp_path / "registered-tools.json"
    probe = tmp_path / "registration-probe.mjs"
    probe.write_text('''import {writeFileSync} from "node:fs";
export default function(pi) {
  pi.on("before_provider_request", () => {
    writeFileSync(process.env.ARGUS_LOADER_RECEIPT, JSON.stringify({provider_request_reached:true}));
    process.exit(97);
  });
  pi.on("session_start", () => {
    const required=["consult_advisor","list_peer_projects","send_peer_message","peer_message_status",
      "search_experiences","get_experience","revise_experience","retract_experience",
      "list_learned_tools","run_learned_tool","evolve_runtime","rollback_runtime"];
    const tools=pi.getAllTools().filter(tool=>required.includes(tool.name));
    const active=pi.getActiveTools();
    writeFileSync(process.env.ARGUS_LOADER_RECEIPT, JSON.stringify({stage:"session_start",tools,active}));
    process.exit(required.every(name=>tools.some(tool=>tool.name===name)&&active.includes(name)) ? 0 : 96);
  });
}
''')
    command = [
        node, PI_CLI, "--mode", "json", "--no-session", "--no-extensions", "--no-skills",
        "--no-prompt-templates", "--no-themes", "--no-context-files", "--no-approve",
        "--model", "argus-loader-test/loader-only", "--tools", "read,grep,find,ls," + ",".join(sorted(EXPECTED)),
        "--extension", str(package_root / "argus_skill/advisor/pi_extension.mjs"),
        "--extension", str(package_root / "argus_skill/messaging/pi_extension.mjs"),
        "--extension", str(package_root / "argus_skill/life/experience_extension.mjs"),
        "--extension", str(package_root / "argus_skill/skills/runtime_extension.mjs"),
        "--extension", str(probe), "Load tools only; do not send a provider request.",
    ]
    try:
        result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20, env={
            "PATH": os.defpath, "PI_CODING_AGENT_DIR": str(agent), "PI_OFFLINE": "1", "CI": "true",
            "PI_HARNESS_PROFILE": "argus", "ARGUS_LOADER_RECEIPT": str(receipt),
            "ARGUS_PLUGIN_EXPERIENCE_WRITABLE": "1",
            "ARGUS_PLUGIN_RUNTIME_WRITABLE": "1",
        })
        assert result.returncode == 0, result.stdout + result.stderr
        record = json.loads(receipt.read_text())
        assert record["stage"] == "session_start"
        assert {tool["name"] for tool in record["tools"]} == EXPECTED
        assert EXPECTED <= set(record["active"])
        assert not {"bash", "write", "edit"} & set(record["active"])
        for tool in record["tools"]:
            assert tool["parameters"]["type"] == "object"
        assert "Failed to load extension" not in result.stderr
        assert provider_requests == []
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=1)
