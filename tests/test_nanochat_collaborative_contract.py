from argus_skill.verticals.nanochat.stages import role_banner


def test_common_banner_supports_both_scaffolds_and_canonical_workdir():
    banner = role_banner("manager")

    assert "prepare.py" in banner
    assert "lib.py" in banner
    assert "Never require a file from" in banner
    assert "canonical workdir" in banner
    assert "STANDING/open-ended" in banner
    assert "return to" in banner and "optimize" in banner
    assert "about 5 minutes" in banner
    assert "ONE clean scorer run" in banner
    assert "Do NOT hedge" in banner


def test_planner_banner_enforces_collaborative_protocol_and_local_reproduction():
    banner = role_banner("planner")

    assert "coordinator.py" in banner
    assert "reproduce its source locally" in banner
    assert "CLAIM before editing" in banner
    assert "PUBLISH the result" in banner
    assert "refresh the live best every five runs" in banner
    assert "runtime-provenance blocker" in banner
    assert "pin exact dependency versions" in banner


def test_noise_gate_uses_measured_local_variance_not_a_generic_constant():
    banners = "\n".join(
        role_banner(role) for role in ("planner", "engineer", "reviewer")
    )

    assert "LOCALLY MEASURED" in banners
    assert "same-seed" in banners
    assert "cross-seed" in banners
    assert "0.001-0.002" not in banners


def test_engineer_and_reviewer_preserve_real_fa4_and_scrubbed_provenance():
    engineer = role_banner("engineer")
    reviewer = role_banner("reviewer")

    assert "real FA-4 only" in engineer
    assert "exact pinned upstream FA-4 source/revision" in engineer
    assert "result + insight" in engineer
    assert "ONE-run/1-seed screen first" in engineer
    assert "source-log metadata" in reviewer
    assert "source_log_sha256" not in reviewer
    assert "stale provenance metadata needs record repair" in reviewer
    assert "stale digest" not in reviewer
    assert "Do NOT demand multi-seed repeats" in reviewer
