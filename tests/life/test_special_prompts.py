"""Tests for operator special-prompt injection."""
from __future__ import annotations

from pathlib import Path

from argus_skill.life import special_prompts


def test_no_dir_returns_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR",
                       str(tmp_path / "absent"))
    assert special_prompts.load_special_prompts() == []
    assert special_prompts.render_special_prompts_context() == ""


def test_loads_sorted_and_skips_empty(tmp_path: Path, monkeypatch) -> None:
    d = tmp_path / "sp"
    d.mkdir()
    (d / "20-second.md").write_text("Second rule.", encoding="utf-8")
    (d / "10-first.md").write_text("First rule.", encoding="utf-8")
    (d / "30-blank.md").write_text("   \n  ", encoding="utf-8")
    (d / "notes.txt").write_text("ignored, not markdown", encoding="utf-8")
    # The trust check rejects group/world-writable files; the sandbox umask
    # yields 0664, so normalize trusted directives to 0644.
    (d / "20-second.md").chmod(0o644)
    (d / "10-first.md").chmod(0o644)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(d))

    prompts = special_prompts.load_special_prompts()
    assert [name for name, _ in prompts] == ["10-first", "20-second"]


def test_render_has_authoritative_header_and_bodies(
    tmp_path: Path, monkeypatch
) -> None:
    d = tmp_path / "sp"
    d.mkdir()
    (d / "10-gpu.md").write_text("Free the keep-alive before training.",
                                 encoding="utf-8")
    (d / "10-gpu.md").chmod(0o644)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(d))

    rendered = special_prompts.render_special_prompts_context()
    assert "Operator Directives" in rendered
    assert "authoritative" in rendered
    assert "Free the keep-alive before training." in rendered
    assert "### 10-gpu" in rendered


def test_render_requires_live_probe_for_mutable_hardware_claims(
    tmp_path: Path, monkeypatch
) -> None:
    d = tmp_path / "sp"
    d.mkdir()
    directive = d / "10-gpu.md"
    directive.write_text(
        "The benchmark GPUs are 8 NVIDIA B200 cards on host b200-nano.",
        encoding="utf-8",
    )
    directive.chmod(0o644)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(d))

    rendered = special_prompts.render_special_prompts_context()

    assert "do not prove current availability" in rendered
    assert "probe mutable runtime facts" in rendered
    assert "failed probe leaves availability unconfirmed" in rendered
    assert rendered.index("do not prove current availability") < rendered.index(
        "The benchmark GPUs are 8 NVIDIA B200"
    )


def test_explicit_paper_scope_is_omitted_from_bounded_missions(
    tmp_path: Path, monkeypatch
) -> None:
    d = tmp_path / "sp"
    d.mkdir()
    paper = d / "10-paper.md"
    paper.write_text(
        "---\nscope: paper\n---\nPaper-only research direction.",
        encoding="utf-8",
    )
    paper.chmod(0o644)
    global_rule = d / "20-machine.md"
    global_rule.write_text("Machine rule for every task.", encoding="utf-8")
    global_rule.chmod(0o644)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(d))

    bounded = special_prompts.render_special_prompts_context(paper_mission=False)
    assert "Paper-only" not in bounded
    assert "Machine rule" in bounded
    paper_context = special_prompts.render_special_prompts_context(paper_mission=True)
    assert "Paper-only" in paper_context
    assert "Machine rule" in paper_context


def test_world_writable_directive_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    """Untrusted (world-writable) directive files are not loaded."""
    import os

    d = tmp_path / "sp"
    d.mkdir()
    trusted = d / "10-ok.md"
    trusted.write_text("trusted rule", encoding="utf-8")
    trusted.chmod(0o644)
    untrusted = d / "20-evil.md"
    untrusted.write_text("malicious injected rule", encoding="utf-8")
    os.chmod(untrusted, 0o666)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(d))

    names = [name for name, _ in special_prompts.load_special_prompts()]
    assert names == ["10-ok"]


def test_windows_preview_does_not_apply_posix_mode_bits(
    tmp_path: Path, monkeypatch
) -> None:
    d = tmp_path / "sp"
    d.mkdir()
    directive = d / "10-windows.md"
    directive.write_text("PowerShell machine rule", encoding="utf-8")
    directive.chmod(0o666)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(d))
    monkeypatch.setattr(special_prompts, "_enforce_posix_trust_bits", lambda: False)

    assert special_prompts.load_special_prompts() == [
        ("10-windows", "PowerShell machine rule")
    ]
