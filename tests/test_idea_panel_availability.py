"""Ideas come from whatever labs this machine can actually reach.

A panel is only worth seating when two independently-trained models are
installed. Everything here is about what happens when they are not: a missing
CLI, an unsupported name, or a single-backend box must leave ideation exactly
as it was rather than half-running a debate.
"""

from __future__ import annotations

from argus_skill.verticals.research import idea_panel


def test_an_unsupported_name_is_not_a_seat() -> None:
    assert idea_panel.available_panel("nope,alsonope") == []


def test_a_supported_backend_that_is_not_installed_is_not_a_seat(monkeypatch) -> None:
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: None)
    assert idea_panel.available_panel("codex,claude,copilot") == []


def test_one_installed_backend_leaves_ideation_alone(monkeypatch) -> None:
    """A single seat is not a panel. The campaign must fall through to the
    single-model path rather than run a debate with itself."""
    monkeypatch.setattr(
        idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x" if backend == "codex" else None
    )
    assert idea_panel.available_panel("codex,claude") == [("codex", "")]
    assert idea_panel.run_panel(".", direction="d", proposal_prompt="p") == ""


def test_operator_config_survives_a_missing_seat(monkeypatch) -> None:
    installed = {"codex", "copilot"}
    monkeypatch.setattr(
        idea_panel,
        "_resolve_bin",
        lambda backend: "/usr/bin/x" if backend in installed else None,
    )
    seats = idea_panel.available_panel("codex,claude,copilot:gemini-3.1-pro-preview,grok")

    assert seats == [("codex", ""), ("copilot", "gemini-3.1-pro-preview")]


def test_the_panel_is_auto_seated_from_installed_clis(monkeypatch) -> None:
    monkeypatch.delenv(idea_panel.PANEL_KNOB, raising=False)
    monkeypatch.setattr(
        idea_panel,
        "_resolve_bin",
        lambda backend: "/usr/bin/x" if backend in {"claude", "grok"} else None,
    )
    assert idea_panel.available_panel() == [("claude", ""), ("grok", "")]


def test_a_silent_panellist_does_not_end_the_panel(monkeypatch) -> None:
    """One model returning nothing is normal. Two are needed for a debate, so
    the run degrades to nothing rather than to a one-sided argument."""
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    monkeypatch.setattr(idea_panel, "_ask", lambda *a, **k: "")

    assert idea_panel.run_panel(".", direction="d", proposal_prompt="p") == ""


def test_proposals_and_cross_examination_are_both_recorded(monkeypatch) -> None:
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")

    def _ask(seat, prompt, workdir, label):
        if label == "idea-panel-propose":
            return f"## Candidate {seat[0].upper()}-1: something"
        return f"### Panel bet\n{seat[0]} bets on the other one."

    monkeypatch.setattr(idea_panel, "_ask", _ask)
    out = idea_panel.run_panel(
        ".", direction="d", proposal_prompt="p", configured="codex,claude"
    )

    assert "## Candidates from `codex`" in out
    assert "## Candidates from `claude`" in out
    assert "## Cross-examination by `codex`" in out
    assert "## Cross-examination by `claude`" in out


def test_a_reviewer_never_cross_examines_itself(monkeypatch) -> None:
    """The value is in being argued with by a model that shares none of your
    training, so a seat must not be handed back its own candidates."""
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    seen: list[tuple[str, str]] = []

    def _ask(seat, prompt, workdir, label):
        if label == "idea-panel-propose":
            return f"## Candidate {seat[0]}-only-marker"
        seen.append((seat[0], prompt))
        return "### Panel bet\nx"

    monkeypatch.setattr(idea_panel, "_ask", _ask)
    idea_panel.run_panel(".", direction="d", proposal_prompt="p", configured="codex,claude")

    for backend, prompt in seen:
        assert f"{backend}-only-marker" not in prompt


def test_the_panel_never_raises(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("panel exploded")

    monkeypatch.setattr(idea_panel, "available_panel", _boom)
    assert idea_panel.run_panel(".", direction="d", proposal_prompt="p") == ""


def _runner_result(text: str):
    import types

    return types.SimpleNamespace(exit_code=0, agent_messages=[text])


class _Runner:
    def run_exec(self, *_a, **_k):  # the gateway is stubbed; this only has to exist
        raise AssertionError("the gateway should have been used")


def test_one_backend_produces_exactly_the_old_single_model_call(tmp_path, monkeypatch) -> None:
    """The whole feature has to be invisible on a machine with one CLI."""
    from argus_skill.verticals.research import idea_search

    labels: list[str] = []

    def _gateway(runner, *, prompt, options, run_label):
        labels.append(run_label)
        return _runner_result("## Candidate A: something")

    monkeypatch.setattr(
        idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x" if backend == "codex" else None
    )
    monkeypatch.setattr(idea_search, "gateway_run_exec", _gateway)
    monkeypatch.setattr(idea_search, "_resolve_direction", lambda w, d: "direction")

    count = idea_search.augment_idea_candidates(_Runner(), tmp_path, direction="d")
    body = (tmp_path / "research" / "IDEA_CANDIDATES.md").read_text(encoding="utf-8")

    assert labels == ["idea-search"]
    assert count == 1
    assert "Panel ideation" not in body


def test_two_backends_debate_instead(tmp_path, monkeypatch) -> None:
    from argus_skill.verticals.research import idea_search

    labels: list[str] = []

    def _gateway(runner, *, prompt, options, run_label):
        labels.append(run_label)
        return _runner_result("## Candidate A: something")

    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    monkeypatch.setattr(idea_panel, "_runner_for", lambda backend, agent_bin: _Runner())
    monkeypatch.setattr(idea_panel, "PANEL_KNOB", "ARGUS_SKILL_IDEA_PANEL")
    monkeypatch.setenv("ARGUS_SKILL_IDEA_PANEL", "codex,claude")
    monkeypatch.setattr("argus_skill.core.run_gateway.run_exec", _gateway)
    monkeypatch.setattr(idea_search, "_resolve_direction", lambda w, d: "direction")

    idea_search.augment_idea_candidates(_Runner(), tmp_path, direction="d")
    body = (tmp_path / "research" / "IDEA_CANDIDATES.md").read_text(encoding="utf-8")

    assert "idea-panel-propose" in labels
    assert "idea-panel-review" in labels
    assert "idea-search" not in labels
    assert "Panel ideation" in body
