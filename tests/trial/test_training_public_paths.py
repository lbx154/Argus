"""Only the exact hosted project/task references are public path literals."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from hosted_platform import hosted_posix_paths

from argus.core.role_session import RoleSessionCapsule
from argus.engineer.checkpoint import shared_checkpoint_instructions
from argus.roles.prompts.engineer import (
    assemble_round_prompt,
    build_mission_prompt,
    mission_request,
)
from argus.roles.prompts.registry import RolePromptCatalog
from argus.skills.role_library import render_skill_library_paths
from argus.trial.training_capture import HOSTED_PROFILE, _hosted_sensitive
from argus.trial.training_validate import _sample

SID = "s-a1b2c3d4"
MISSION = "a1b2c3d4e5f6"
WORKSPACE = f"/tenant/home/.argus-skill/workspaces/{SID}"
HANDOFF = f"/tenant/home/.argus-skill/projects/{SID}/handoffs/{MISSION}"
SOURCE = {"kind": "pi.training_episode", "sid": SID, "runtime_profile": HOSTED_PROFILE,
          "runtime": {"profile": HOSTED_PROFILE, "mission_id": MISSION}}


def hosted_engineer_prompt(task):
    """Same full direct-role template stack as the owned UI smoke task.

    The first fixture omitted the normal skill-library/durable-learning pieces.
    These are public, fixed runtime paths, never a transcript-file import.
    """
    roots = [f"/tenant/home/.argus-skill/projects/{SID}/skills",
             "/tenant/home/.argus-skill/skills/_shared_verticals/software", "/tenant/home/.argus-skill/skills"]
    # Model the hosted public library path, not this test runner's private checkout.
    with patch("argus.skills.role_library.builtin_skill_source_path", return_value=Path(roots[-1])):
        libraries = render_skill_library_paths(SimpleNamespace(library_roots=lambda: roots), role="engineer", task=task)
    banner = RolePromptCatalog().resolve(mission_request(
        Path(f"/tenant/home/.argus-skill/projects/{SID}"), vertical="software", altitude_root=Path(WORKSPACE), stage="delivery",
    )).role_banner
    checkpoint = Path(HANDOFF + "/CHECKPOINT.md")
    capsule = RoleSessionCapsule(role="engineer", policy="rolling", objective_revision="synthetic",
                                 workdir=WORKSPACE, branch="", backend="pi", model="synthetic",
                                 checkpoint_path=str(checkpoint), mission_context_path=HANDOFF + "/mission.json",
                                 path=Path(HANDOFF + "/role-sessions/engineer.json"))
    base = build_mission_prompt(task=task, skill_text=libraries, next_action=None,
                               role_banner=banner, require_post_task_learning=True,
                               project_root=Path(WORKSPACE),
                               compact_team=True, operator_context="Carry out only the current task and verify its public result.")
    return assemble_round_prompt(base, checkpoint_block="\n\n".join((
        f"## MissionBrief\n- Workdir: `{WORKSPACE}`\n- Stage: delivery",
        capsule.prompt_block(), shared_checkpoint_instructions(checkpoint, role="engineer"),
    )))


@hosted_posix_paths
def test_full_direct_role_template_includes_only_exact_public_library_directories():
    text = hosted_engineer_prompt("Write result.txt and read it back.")
    assert not _hosted_sensitive(text, sid=SID, mission_id=MISSION)
    sample = {"messages": [{"role": "user", "content": text}, {"role": "assistant", "content": "Public result."}]}
    assert _sample(sample, "synthetic", source=SOURCE)[0] == sample


@pytest.mark.parametrize("path", [
    WORKSPACE + "/../private", WORKSPACE + "/src/../../private", WORKSPACE + "/./file",
    WORKSPACE + "/%2e%2e/private", WORKSPACE + "/%2fhome/private", WORKSPACE + "/a\\..\\private",
    WORKSPACE + "suffix/file", WORKSPACE.replace(SID, "s-ffffffff") + "/file",
    "prefix" + WORKSPACE, "/root" + WORKSPACE, WORKSPACE.upper(),
    WORKSPACE + "/sk-123456789secret", WORKSPACE + "/user@example.com",
    HANDOFF.replace(MISSION, "ffffffffffff") + "/latest.json",
    HANDOFF + "/../agent_io.jsonl", HANDOFF + "/agent_io.jsonl", HANDOFF + "/raw-pi-session.jsonl",
    HANDOFF + "/role-sessions/other.json", HANDOFF + "/role-sessions/engineer.json/private",
    f"/tenant/home/.argus-skill/projects/{SID}/agent_io.jsonl",
    "/home/alice/private", "/root/private", "/data/private", "/tenant/home/.ssh/id_rsa",
    f"/tenant/home/.argus-skill/projects/{SID}/skills/private.md",
    f"/tenant/home/.argus-skill/projects/{SID}/skills/engineer",
    f"/tenant/home/.argus-skill/projects/{SID}/skills/engineer/private.md",
    "/tenant/home/.argus-skill/skills/private.md", "/tenant/home/.argus-skill/skills/../private",
    "/tenant/home/.argus-skill/skills/_shared_verticals/custom-private",
    "/tenant/home/.argus-skill/skills/_shared_verticals/software/private.md",
    "/tenant/home/.argus-skill-other", "/tenant/home/.argus-skill/../private",
    "/tenant/home/.argus-skill/agent_io.jsonl", "/tenant/home/.argus-skill/credentials.json",
])
def test_private_other_project_and_noncanonical_paths_stay_rejected(path):
    assert _hosted_sensitive({"content": f"Use `{path}`."}, sid=SID, mission_id=MISSION)


@pytest.mark.parametrize("text", [
    r"('integer count', '恰有 \\(g-1\\) 个' in text)",
    r"('pairing proof', '第 \\(k\\) 项和第 \\(a-k\\) 项' in text)",
    r"re.findall(r'\\d+\\.\\d+', value)",
    r"\\begin{equation} \\frac{a}{b} \\end{equation}",
])
def test_public_latex_and_regex_escapes_are_not_unc_paths(text):
    from argus.trial.training_data import _SENSITIVE

    assert _SENSITIVE.search(text) is None
    assert not _hosted_sensitive({"input": {"command": text}}, sid=SID, mission_id=MISSION)


@pytest.mark.parametrize("text", [
    r"\\server\share\private.txt", r"\\server.example.org\share\private.txt",
    r"\\192.168.1.5\C$\private.txt", r"\\[fe80::1]\share\private.txt",
    r"\\?\C:\private.txt", r"\\.\pipe\private-service", r"C:\Users\Alice\private.txt",
])
def test_actual_unc_drive_and_windows_device_paths_stay_private(text):
    from argus.trial.training_data import _SENSITIVE

    assert _SENSITIVE.search(text) is not None
    assert _hosted_sensitive({"input": {"command": text}}, sid=SID, mission_id=MISSION)


@pytest.mark.parametrize("separator", [" ", ",", ":", "=", ";", " | ", " && ", "\n", "](", "(", "[", "{"])
@pytest.mark.parametrize("private", [
    "/root/private", "/home/alice/private", "/tenant/home/.ssh/id_rsa",
    "/tenant/home/.argus-skill/workspaces/s-ffffffff/code.py",
    f"/tenant/home/.argus-skill/projects/{SID}/agent_io.jsonl",
])
def test_following_independent_private_path_is_never_masked(separator, private):
    assert _hosted_sensitive(WORKSPACE + "/ok.py" + separator + private, sid=SID, mission_id=MISSION)


def test_multiple_current_workspace_paths_are_scanned_independently():
    text = WORKSPACE + "/数据+1.csv " + WORKSPACE + "/figures/result.png:12:"
    assert not _hosted_sensitive(text, sid=SID, mission_id=MISSION)
