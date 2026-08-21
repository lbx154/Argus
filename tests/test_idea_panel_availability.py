"""Ideas come from whatever labs this machine can actually reach.

A panel is only worth seating when two independently-trained models are
installed. Everything here is about what happens when they are not: a missing
CLI, an unsupported name, or a single-backend box must leave ideation exactly
as it was rather than half-running a debate.
"""

from __future__ import annotations

import pytest

from argus_skill.verticals.research import idea_panel

_real_is_usable = idea_panel._is_usable


@pytest.fixture(autouse=True)
def _assume_installed_backends_work(monkeypatch):
    """Usability is cached per process, and most tests are about resolution
    rather than subscriptions. Tests that care override this."""
    idea_panel._usable.clear()
    monkeypatch.setattr(idea_panel, "_is_usable", lambda backend: True)
    yield
    idea_panel._usable.clear()


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


def test_two_models_on_one_backend_still_see_each_other(monkeypatch) -> None:
    """The single-subscription panel: both seats share a launcher, so keying
    identity on the backend handed each of them an empty page and they reviewed
    candidates they had invented."""
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    reviews: list[str] = []

    def _ask(seat, prompt, workdir, label):
        if label == "idea-panel-propose":
            return f"## Candidate from {seat[1]}"
        if label == "idea-panel-review":
            reviews.append(prompt)
        return "### Panel bet\nx"

    monkeypatch.setattr(idea_panel, "_ask", _ask)
    out = idea_panel.run_panel(
        ".", direction="d", proposal_prompt="p",
        configured="copilot:gpt-5.5,copilot:gemini-3.1-pro-preview",
    )

    assert len(reviews) == 2
    assert "## Candidate from gemini-3.1-pro-preview" in reviews[0]
    assert "## Candidate from gpt-5.5" in reviews[1]
    assert "## Cross-examination by `copilot:gpt-5.5`" in out
    assert "## Cross-examination by `copilot:gemini-3.1-pro-preview`" in out


def test_a_reviewer_never_cross_examines_itself(monkeypatch) -> None:
    """The value is in being argued with by a model that shares none of your
    training, so a seat must not be handed back its own candidates."""
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    seen: list[tuple[str, str]] = []

    def _ask(seat, prompt, workdir, label):
        if label == "idea-panel-propose":
            return f"## Candidate {seat[0]}-only-marker"
        if label == "idea-panel-review":
            # The verdict round deliberately sees every candidate; only the
            # cross-examination must withhold a seat's own proposals.
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

    def _gateway(runner, *, prompt, options, run_label, **_kw):
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

    def _gateway(runner, *, prompt, options, run_label, **_kw):
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


def test_an_installed_cli_without_a_subscription_is_not_a_seat(monkeypatch) -> None:
    """The case that would have hurt: someone pays for one vendor and has the
    other launchers on PATH anyway. Seating them spends an ideation round
    discovering they cannot log in."""
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    monkeypatch.setattr(idea_panel, "_is_usable", lambda backend: backend == "codex")

    assert idea_panel.available_panel("codex,claude,copilot,grok") == [("codex", "")]
    # One usable seat is not a panel, so ideation is untouched.
    assert idea_panel.run_panel(".", direction="d", proposal_prompt="p") == ""


def test_usability_is_probed_once_per_backend(monkeypatch) -> None:
    """The readiness probe spawns a CLI. Asking twice per campaign is waste."""
    calls: list[str] = []

    class _Report:
        ok = True

    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    monkeypatch.setattr(idea_panel, "_is_usable", idea_panel._is_usable.__wrapped__
                        if hasattr(idea_panel._is_usable, "__wrapped__") else _real_is_usable)
    monkeypatch.setattr(
        "argus_skill.core.backend_readiness.check_backend_readiness",
        lambda backend, **kw: (calls.append(backend), _Report())[1],
    )

    idea_panel.available_panel("codex,claude")
    idea_panel.available_panel("codex,claude")

    assert sorted(calls) == ["claude", "codex"]


def test_an_unverifiable_backend_is_not_seated(monkeypatch) -> None:
    """A probe that raises means unknown, and unknown is not usable."""
    def _boom(backend, **_kw):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    monkeypatch.setattr(idea_panel, "_is_usable", _real_is_usable)
    monkeypatch.setattr(
        "argus_skill.core.backend_readiness.check_backend_readiness", _boom
    )

    assert idea_panel.available_panel("codex,claude") == []


def test_one_subscription_can_still_seat_two_labs(monkeypatch) -> None:
    """A single usable backend that serves several labs' weights is a panel, as
    long as the operator names the models."""
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    monkeypatch.setattr(idea_panel, "_is_usable", lambda backend: backend == "copilot")

    seats = idea_panel.available_panel(
        "copilot:gpt-5.5,copilot:gemini-3.1-pro-preview,codex"
    )

    assert seats == [("copilot", "gpt-5.5"), ("copilot", "gemini-3.1-pro-preview")]


def test_the_debate_survives_the_ranking_agent(tmp_path, monkeypatch) -> None:
    """The candidate file belongs to the ranking agent and it rewrites the file
    whole. The first real run lost 21KB of cross-examination that way, so the
    argument has to live somewhere that rewrite cannot reach."""
    from argus_skill.verticals.research import idea_search

    def _gateway(runner, *, prompt, options, run_label, **_kw):
        return _runner_result("## Candidate A: something")

    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    monkeypatch.setattr(idea_panel, "_runner_for", lambda backend, agent_bin: _Runner())
    monkeypatch.setenv("ARGUS_SKILL_IDEA_PANEL", "codex,claude")
    monkeypatch.setattr("argus_skill.core.run_gateway.run_exec", _gateway)
    monkeypatch.setattr(idea_search, "_resolve_direction", lambda w, d: "direction")

    idea_search.augment_idea_candidates(_Runner(), tmp_path, direction="d")

    research = tmp_path / "research"
    panel_file = research / "IDEA_PANEL.md"
    assert panel_file.is_file()
    assert "Cross-examination by" in panel_file.read_text(encoding="utf-8")

    # The candidate file points at it, and carries the candidates themselves.
    candidates = (research / "IDEA_CANDIDATES.md").read_text(encoding="utf-8")
    assert "research/IDEA_PANEL.md" in candidates
    assert "## Candidate A" in candidates

    # Now the ranking agent replaces the candidate file, as it does.
    (research / "IDEA_CANDIDATES.md").write_text("# Ranked\n", encoding="utf-8")
    assert "Cross-examination by" in panel_file.read_text(encoding="utf-8")


def test_one_backend_serving_one_model_is_not_a_panel(monkeypatch) -> None:
    """Naming the same launcher twice with no model is one model arguing with
    itself, which is worse than no panel because it looks like one."""
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")

    assert idea_panel.available_panel("copilot,copilot") == [("copilot", "")]
    assert idea_panel.available_panel("copilot:m,copilot:m") == [("copilot", "m")]
    # Different models on that one backend are still two labs.
    assert len(idea_panel.available_panel("copilot:a,copilot:b")) == 2


def test_seats_are_asked_at_the_same_time(monkeypatch) -> None:
    """Sequential rounds cost the sum of every model's latency. A round should
    cost the slowest one."""
    import threading
    import time

    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    inside = threading.Barrier(2, timeout=5)

    def _ask(seat, prompt, workdir, label):
        if label == "idea-panel-propose":
            inside.wait()  # both seats must be in flight together or this times out
            return f"## Candidate {seat[0]}"
        return "### Winner\nx"

    monkeypatch.setattr(idea_panel, "_ask", _ask)
    started = time.time()
    out = idea_panel.run_panel(
        ".", direction="d", proposal_prompt="p", configured="codex,claude"
    )

    assert out
    assert time.time() - started < 5


def test_the_panel_converges_on_one_candidate(monkeypatch) -> None:
    """A campaign runs one idea, so the panel has to end by naming one."""
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")
    seen: list[str] = []

    def _ask(seat, prompt, workdir, label):
        if label == "idea-panel-propose":
            return f"## Candidate {seat[0]}-1"
        if label == "idea-panel-verdict":
            seen.append(prompt)
            return "### Winner\ncodex-1\n### Week-one check\nrun it"
        return "### Panel review — x\nobjection"

    monkeypatch.setattr(idea_panel, "_ask", _ask)
    out = idea_panel.run_panel(
        ".", direction="d", proposal_prompt="p", configured="codex,claude"
    )

    assert "## Verdict from `codex`" in out
    assert "## Verdict from `claude`" in out
    # The verdict is taken with the whole argument in view, not just one's own.
    for prompt in seen:
        assert "## Candidate codex-1" in prompt
        assert "## Candidate claude-1" in prompt
        assert "Cross-examination" in prompt


def test_a_seat_that_raises_costs_only_its_own_seat(monkeypatch) -> None:
    monkeypatch.setattr(idea_panel, "_resolve_bin", lambda backend: "/usr/bin/x")

    def _ask(seat, prompt, workdir, label):
        if seat[0] == "claude":
            raise RuntimeError("that seat exploded")
        return "## Candidate codex-1" if label == "idea-panel-propose" else "### Winner\nx"

    monkeypatch.setattr(idea_panel, "_ask", _ask)
    # One surviving proposal is not a panel, so this degrades rather than crashes.
    assert idea_panel.run_panel(".", direction="d", proposal_prompt="p",
                                configured="codex,claude") == ""
