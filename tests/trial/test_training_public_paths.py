"""Only the exact hosted project/task references are public path literals."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.role_session import RoleSessionCapsule
from argus_skill.engineer.checkpoint import shared_checkpoint_instructions
from argus_skill.roles.prompts.engineer import (
    assemble_round_prompt,
    build_mission_prompt,
    mission_request,
)
from argus_skill.roles.prompts.registry import RolePromptCatalog
from argus_skill.skills.role_library import render_skill_library_paths
from argus_skill.trial.training_capture import HOSTED_PROFILE, _hosted_sensitive
from argus_skill.trial.training_validate import InvalidPackage, _check_public_sources, _sample

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
                               project_root=Path(WORKSPACE), project_skill_dir=roots[0] + "/engineer",
                               compact_team=True, operator_context="Carry out only the current task and verify its public result.")
    return assemble_round_prompt(base, checkpoint_block="\n\n".join((
        f"## MissionBrief\n- Workdir: `{WORKSPACE}`\n- Stage: delivery",
        capsule.prompt_block(), shared_checkpoint_instructions(checkpoint, role="engineer"),
    )))


def test_full_direct_role_template_includes_only_exact_public_library_directories():
    text = hosted_engineer_prompt("Write result.txt and read it back.")
    assert not _hosted_sensitive(text, sid=SID, mission_id=MISSION)
    sample = {"messages": [{"role": "user", "content": text}, {"role": "assistant", "content": "Public result."}]}
    assert _sample(sample, "synthetic", source=SOURCE)[0] == sample


def test_actual_checkpoint_and_role_capsule_prompt_literals_preserve_bytes():
    checkpoint = Path(HANDOFF + "/CHECKPOINT.md")
    capsule = RoleSessionCapsule(role="engineer", policy="rolling", objective_revision="synthetic",
                                 workdir=WORKSPACE, branch="", backend="pi", model="synthetic",
                                 checkpoint_path=str(checkpoint), mission_context_path=HANDOFF + "/mission.json",
                                 path=Path(HANDOFF + "/role-sessions/engineer.json"))
    text = f"Workdir: `{WORKSPACE}`\n" + shared_checkpoint_instructions(checkpoint, role="engineer") + capsule.prompt_block()
    assert _hosted_sensitive(text)
    assert not _hosted_sensitive(text, sid=SID, mission_id=MISSION)
    assert _hosted_sensitive(text, sid=SID, mission_id="ffffffffffff")
    sample = {"messages": [{"role": "user", "content": text}, {"role": "assistant", "content": "Public result."}]}
    normalized, _, _ = _sample(sample, "synthetic", source=SOURCE)
    assert normalized == sample and normalized["messages"][0]["content"] == text
    with pytest.raises(InvalidPackage, match="sensitive_training_content"):
        _sample(sample, "synthetic", source={**SOURCE, "sid": "s-ffffffff"})
    with pytest.raises(InvalidPackage, match="sensitive_training_content"):
        _sample(sample, "synthetic")


@pytest.mark.parametrize("path", [
    WORKSPACE, WORKSPACE + "/", WORKSPACE + "/src/main.py", WORKSPACE + "/.gitignore",
    HANDOFF + "/mission.json", HANDOFF + "/latest.json", HANDOFF + "/frontier.json",
    HANDOFF + "/CHECKPOINT.md", HANDOFF + "/role-sessions/engineer.json",
    HANDOFF + "/role-sessions/reviewer.json",
    f"/tenant/home/.argus-skill/projects/{SID}/skills",
    f"/tenant/home/.argus-skill/projects/{SID}/skills/engineer",
    "/tenant/home/.argus-skill/skills", "/tenant/home/.argus-skill/skills/_shared_verticals/software",
    "/tenant/home/.argus-skill",
])
def test_current_bound_generated_path_literals_are_public(path):
    assert not _hosted_sensitive({"content": f"Use `{path}`."}, sid=SID, mission_id=MISSION)


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
    f"/tenant/home/.argus-skill/projects/{SID}/skills/engineer/private.md",
    "/tenant/home/.argus-skill/skills/private.md", "/tenant/home/.argus-skill/skills/../private",
    "/tenant/home/.argus-skill/skills/_shared_verticals/custom-private",
    "/tenant/home/.argus-skill/skills/_shared_verticals/software/private.md",
    "/tenant/home/.argus-skill-other", "/tenant/home/.argus-skill/../private",
    "/tenant/home/.argus-skill/agent_io.jsonl", "/tenant/home/.argus-skill/credentials.json",
])
def test_private_other_project_and_noncanonical_paths_stay_rejected(path):
    assert _hosted_sensitive({"content": f"Use `{path}`."}, sid=SID, mission_id=MISSION)


def test_source_payload_checks_use_the_same_hosted_task_binding():
    source = {**SOURCE, "events": [{"id": "synthetic", "sequence": 0, "kind": "context", "observed_at": 1,
                                   "payload": {"messages": [{"role": "user", "content": [
                                       {"type": "text", "text": HANDOFF + "/mission.json"}], "timestamp": 1}], "tools": []}}]}
    _check_public_sources([source])
    with pytest.raises(InvalidPackage, match="sensitive_source_content"):
        _check_public_sources([{**source, "runtime": {"profile": HOSTED_PROFILE, "mission_id": "ffffffffffff"}}])


def test_actual_public_framework_discovery_command_keeps_exact_bytes():
    command = ("python3 - <<'PY'\ntry:\n import argus_skill\n print(argus_skill.__file__)\n"
               "except Exception as e:\n print('no argus_skill', e)\nPY\n"
               "find /tenant/home/.argus-skill /opt/argus-pi -path '*paper_chart_style.py' -type f 2>/dev/null | head -20")
    before = command
    assert not _hosted_sensitive({"input": {"command": command}}, sid=SID, mission_id=MISSION)
    assert command == before


@pytest.mark.parametrize("text", [
    r"('integer count', '恰有 \\(g-1\\) 个' in text)",
    r"('pairing proof', '第 \\(k\\) 项和第 \\(a-k\\) 项' in text)",
    r"re.findall(r'\\d+\\.\\d+', value)",
    r"\\begin{equation} \\frac{a}{b} \\end{equation}",
])
def test_public_latex_and_regex_escapes_are_not_unc_paths(text):
    from argus_skill.trial.training_data import _SENSITIVE

    assert _SENSITIVE.search(text) is None
    assert not _hosted_sensitive({"input": {"command": text}}, sid=SID, mission_id=MISSION)


@pytest.mark.parametrize("text", [
    r"\\server\share\private.txt", r"\\server.example.org\share\private.txt",
    r"\\192.168.1.5\C$\private.txt", r"\\[fe80::1]\share\private.txt",
    r"\\?\C:\private.txt", r"\\.\pipe\private-service", r"C:\Users\Alice\private.txt",
])
def test_actual_unc_drive_and_windows_device_paths_stay_private(text):
    from argus_skill.trial.training_data import _SENSITIVE

    assert _SENSITIVE.search(text) is not None
    assert _hosted_sensitive({"input": {"command": text}}, sid=SID, mission_id=MISSION)


@pytest.mark.parametrize("suffix", [
    "/training_acceptance/monte_carlo_paper/experiment.py:368:",
    "/src/main.py:42:9: warning: example",
    "/实验结果/方差对比+基准.csv", "/paper figures/variance + runtime.png",
    "/.venv/lib/python3.11/site-packages/numpy/_core/fromnumeric.py:3860:",
    "/node_modules/@scope/package/dist/index.js:18:3",
    "/results/data/statistics.csv", "/snapshots/home/reference.json",
    "/figures/*.png", "/results/run[12]/{a,b}+(x).csv",
    "/a & b/file.txt", "/a; b/file.txt",
])
def test_bound_posix_workspace_literals_and_standard_diagnostics_preserve_source(suffix):
    text = 'Use "' + WORKSPACE + suffix + '".'
    payload = {"content": text}
    before = {"content": text}
    assert not _hosted_sensitive(payload, sid=SID, mission_id=MISSION)
    assert payload == before
    sample = {"messages": [{"role": "user", "content": text},
                           {"role": "assistant", "content": "Public result."}]}
    assert _sample(sample, "synthetic", source=SOURCE)[0] == sample
    assert _hosted_sensitive(payload, sid="s-ffffffff", mission_id=MISSION)


@pytest.mark.parametrize("suffix", [
    "/a /../../private", "/a[1]/../../private", "/a{b}/../../private",
    "/a & b/../../private", "/a; b/../../private", "/a|b/../../private",
    "/space name/../private", "/x/..", "/a\\..\\private",
    "/%2e%2e/private", "/%2Froot/private", "/%5cUsers/private",
    "/file\x00/../../private", "/dir\tname/file",
    "[private]/agent_io.jsonl", "(private)/agent_io.jsonl", "{private}/agent_io.jsonl",
])
def test_posix_filename_delimiters_never_hide_traversal_or_change_binding(suffix):
    assert _hosted_sensitive('"' + WORKSPACE + suffix + '"', sid=SID, mission_id=MISSION)


@pytest.mark.parametrize("separator", [" ", ",", ":", "=", ";", " | ", " && ", "\n", "](", "(", "[", "{"])
@pytest.mark.parametrize("private", [
    "/root/private", "/home/alice/private", "/tenant/home/.ssh/id_rsa",
    "/tenant/home/.argus-skill/workspaces/s-ffffffff/code.py",
    f"/tenant/home/.argus-skill/projects/{SID}/agent_io.jsonl",
])
def test_following_independent_private_path_is_never_masked(separator, private):
    assert _hosted_sensitive(WORKSPACE + "/ok.py" + separator + private, sid=SID, mission_id=MISSION)


@pytest.mark.parametrize("path", [
    "/tenant/home/.argus-skill[private]/agent_io.jsonl",
    "/tenant/home/.argus-skill(private)/agent_io.jsonl",
    f"/tenant/home/.argus-skill/projects/{SID}/skills[private]/agent_io.jsonl",
    HANDOFF + "/mission.json[private]/agent_io.jsonl",
])
def test_exact_public_literal_does_not_end_at_a_filename_bracket(path):
    assert _hosted_sensitive('"' + path + '"', sid=SID, mission_id=MISSION)


def test_multiple_current_workspace_paths_are_scanned_independently():
    text = WORKSPACE + "/数据+1.csv " + WORKSPACE + "/figures/result.png:12:"
    assert not _hosted_sensitive(text, sid=SID, mission_id=MISSION)


@pytest.mark.parametrize("suffix", ["/用户/user@example.com", "/code/sk-123456789secret", "/电话/+16505551234"])
def test_posix_workspace_filename_secret_and_pii_checks_remain_enabled(suffix):
    assert _hosted_sensitive(WORKSPACE + suffix, sid=SID, mission_id=MISSION)
