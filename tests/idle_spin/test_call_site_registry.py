"""Every label that reaches a model backend is classified by what makes it run.

The idle-spin proof rests on one fact per model call site: something outside
Argus starts it (an operator message, a web request, a CLI command, a mission
round), or a consumed event, a count cap, or a gate pinned by the idle-spin
tests bounds it. A call that only waits for the clock is the one shape that
spins. This registry records that fact for every label in ``argus/``: a new
call site fails here until someone classifies it, a new wrapper that passes a
label through fails until it is listed, and a background label can never hide
behind the daemon guard's foreground exemption.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest

import argus
from argus.core import call_guard

ARGUS = Path(argus.__file__).resolve().parent
RELEASE = ARGUS.parent

FOREGROUND = "foreground"  # one operator message, web request, CLI command or mission round
EVENT = "event"            # once per consumed event or changed input fingerprint
CAPPED = "capped"          # a count cap that time never resets
GATED_TIME = "gated_time"  # a time cadence behind a gate the idle-spin tests pin
BACKGROUND = frozenset({EVENT, CAPPED, GATED_TIME})


class Site(NamedTuple):
    pattern: str
    trigger: str
    reason: str
    where: tuple[str, ...]
    examples: tuple[str, ...]


REGISTRY: tuple[Site, ...] = (
    # -- foreground ---------------------------------------------------------
    Site(
        r"engineer-r\d+(-\w+)?(\.winddown)?", FOREGROUND,
        "One Engineer turn of a mission round; the round's own stop checks bound it.",
        ("argus/engineer/round_execution.py:218", "argus/engineer/round_execution.py:1183"),
        ("engineer-r3", "engineer-r3-ec540a8ca32e", "engineer-r3.winddown"),
    ),
    Site(
        r"reviewer(-[a-z0-9]+)?", FOREGROUND,
        "The review of one Engineer round, including its parallel final passes.",
        ("argus/reviewer/_core.py:999", "argus/reviewer/_core.py:415"),
        ("reviewer", "reviewer-scientific", "reviewer-coldread"),
    ),
    Site(
        r"advisor\.[^.]+\..+", FOREGROUND,
        "A consultation a mission role asks for from inside its round.",
        ("argus/advisor/service.py:228",),
        ("advisor.engineer.math",),
    ),
    Site(
        r"peer-message-response", FOREGROUND,
        "Answers one message another project sent; at most MAX_ATTEMPTS tries per message, "
        "then the sender gets a host notice. Replies are filed without a call.",
        ("argus/messaging/handler.py:85",),
        ("peer-message-response",),
    ),
    Site(
        r"manager-(project-grounding|research-target)", FOREGROUND,
        "Part of the vertical decision for one objective (research-target has no caller).",
        ("argus/manager/_vertical_ops.py:289", "argus/manager/_vertical_ops.py:368"),
        ("manager-project-grounding", "manager-research-target"),
    ),
    Site(
        r"manager-ask", FOREGROUND,
        "Answers one question asked through /ask on the web or `argus ask`.",
        ("argus/webapi/manager_bridge.py:240", "argus/apps/cli/_core.py:1343"),
        ("manager-ask",),
    ),
    Site(
        r"manager-rewrite", FOREGROUND,
        "Rewrites one operator prompt on request.",
        ("argus/webapi/manager_bridge.py:1212", "argus/manager/prompt_rewrite.py:239"),
        ("manager-rewrite",),
    ),
    Site(
        r"prompt-complete", FOREGROUND,
        "Suggests how the line an operator is typing ends, once per web request after a pause.",
        ("argus/webapi/manager_bridge.py:839",),
        ("prompt-complete",),
    ),
    Site(
        r"manager-plan", FOREGROUND,
        "Default of draft_plan; every caller passes its own label.",
        ("argus/manager/plan_mode.py:349",),
        ("manager-plan",),
    ),
    Site(
        r"planner-preview", FOREGROUND,
        "The plan preview for one operator request, cached per request.",
        ("argus/webapi/manager_bridge.py:1049",),
        ("planner-preview",),
    ),
    Site(
        r"simple-1|manager-quick-reply|self-[\w-]+", FOREGROUND,
        "Answers one operator message, retried at most once for a stalled or tool-less turn.",
        ("argus/apps/_self_reply.py:921", "argus/webapi/manager_state.py:221"),
        ("simple-1", "manager-quick-reply", "self-micro", "self-implement"),
    ),
    Site(
        r"reader-foundation", FOREGROUND,
        "One reader foundation per question (reserved once).",
        ("argus/webapi/reader_foundation.py:278",),
        ("reader-foundation",),
    ),
    Site(
        r"map-summary", FOREGROUND,
        "The map model's default label. The map no longer writes card words, line notes "
        "or explanations on its own; only an explicit reader request runs this model.",
        ("argus/webapi/map_model.py:314",),
        ("map-summary",),
    ),
    Site(
        r"setup-smoke", FOREGROUND,
        "One smoke probe per `argus setup` run.",
        ("argus/tools/setup.py:314",),
        ("setup-smoke",),
    ),
    Site(
        r"model-probe", FOREGROUND,
        "One test call per click of Test in the model chooser; a second test of the same model in flight is refused.",
        ("argus/webapi/model_choices.py:242",),
        ("model-probe",),
    ),
    Site(
        r"doctor-repair", FOREGROUND,
        "One repair prompt per `argus doctor` run.",
        ("argus/maintenance/advisor.py:265", "argus/core/agent_probe.py:125"),
        ("doctor-repair",),
    ),
    # -- event --------------------------------------------------------------
    Site(
        r"planner\.acceptance_dependencies", EVENT,
        "Once per dispatched mission attempt whose acceptance contract has no cached "
        "assessment; two tries at most.",
        ("argus/life/supervisor/_acceptance_guard.py:147",),
        ("planner.acceptance_dependencies",),
    ),
    Site(
        r"manager\.reviewed_facts", EVENT,
        "Once per Reviewer-confirmed research result at mission settlement.",
        ("argus/manager/reviewed_facts.py:290",),
        ("manager.reviewed_facts",),
    ),
    Site(
        r"manager-supervision", EVENT,
        "Per settled-work event (mission, verdict, degraded daemon, review, stall backstop); "
        "one applied decision per evidence and control revision.",
        ("argus/manager/supervision.py:870",),
        ("manager-supervision",),
    ),
    Site(
        r"observer", EVENT,
        "Once per Engineer, Reviewer or Planner call, when its role.session.turn ends the turn; "
        "its own calls end none, and turns that end during a look share the next one.",
        ("argus/life/observer.py:91",),
        ("observer",),
    ),
    Site(
        r"manager\.skill_placement_batch", EVENT,
        "End-of-mission skill tidy-up; no caller in this release.",
        ("argus/manager/skill_review.py:159",),
        ("manager.skill_placement_batch",),
    ),
    Site(
        r"team-learning-review", EVENT,
        "Once per settled mission that changed a project skill.",
        ("argus/manager/skill_tidy.py:473",),
        ("team-learning-review",),
    ),
    Site(
        r"reflection", EVENT,
        "Once per settled mission.",
        ("argus/life/reflection.py:831",),
        ("reflection",),
    ),
    Site(
        r"manager-prose", EVENT,
        "Default of Manager.write_prose; every caller passes its own label.",
        ("argus/manager/_stage_ops.py:419",),
        ("manager-prose",),
    ),
    # -- capped -------------------------------------------------------------
    Site(
        r"manager-proxy-decision", CAPPED,
        "The Manager decides a parked card in the operator's place: at most "
        "PROXY_DECISION_ATTEMPTS tries per card revision while the operator cannot be asked, "
        "and once per venue-stall question left unanswered for twelve hours.",
        ("argus/manager/proxy_decision.py:48",),
        ("manager-proxy-decision",),
    ),
    # -- gated by time, bounded by a gate -------------------------------------
    Site(
        r"planner\.cycle\d+(\.repair\d+)?", GATED_TIME,
        "Event waits take no call; with nothing running, one per daemon pass after the idle "
        "backoff, within the daemon's BACKGROUND_CALL_BUDGET; failing turns back off and stop "
        "after AUTOMATIC_RETRY_WINDOW_SECONDS; an operator wait is re-granted only on changed input.",
        ("argus/planner/planner.py:328", "argus/planner/planner.py:541"),
        ("planner.cycle12", "planner.cycle12.repair1"),
    ),
    Site(
        r"manager-stage", GATED_TIME,
        "Per mission outcome or Planner verdict; an open-ended wait is re-checked once per "
        "wait key, and backend failures stop after AUTOMATIC_RETRY_WINDOW_SECONDS.",
        ("argus/manager/_stage_ops.py:364",),
        ("manager-stage",),
    ),
    Site(
        r"manager-letter", GATED_TIME,
        "One attempt per letter interval, recorded before the call, and only with new "
        "missions or decisions to report.",
        ("argus/life/supervisor/_letters.py:153",),
        ("manager-letter",),
    ),
    Site(
        r"knowledge-consolidation", GATED_TIME,
        "Per interval only when the lesson files changed; lessons that keep failing stop "
        "after AUTOMATIC_RETRY_WINDOW_SECONDS.",
        ("argus/life/consolidation.py:578",),
        ("knowledge-consolidation",),
    ),
)

# Background labels the daemon guard exempts, and why that is safe.
OUTSIDE_THE_DAEMON: dict[str, str] = {}

# Literal labels that name a usage-ledger record or a fixture, not a model call.
NOT_MODEL_CALLS = {
    ("argus/core/usage.py", "_legacy_event_records", "legacy.mission.aggregate"),
    (
        "argus/life/supervisor/_mission_execution_runtime.py", "_derive_basic_outcome_fields",
        "memory.mission.aggregate",
    ),
}

# Functions that pass a caller's label on (or record it) without choosing one.
PASS_THROUGH = {
    # The backend and its transport.
    ("argus/adapters/agent_cli_backend/_core.py", "run_exec", "run_label"),
    ("argus/adapters/agent_cli_backend/_exec.py", "_execute_prepared", "run_label"),
    ("argus/adapters/agent_cli_backend/_exec.py", "execute", "run_label"),
    ("argus/adapters/agent_cli_backend/_exec_finalize.py", "finalize_result", "ctx.run_label"),
    ("argus/adapters/agent_cli_backend/_exec_spawn.py", "spawn_and_finish", "ctx.run_label"),
    ("argus/agent_cli/_run_exec.py", "_run_prepared_exec", "run_label"),
    ("argus/agent_cli/_run_exec.py", "_stream_turn_output.check_idle_deadlines", "run_label"),
    ("argus/agent_cli/_run_exec.py", "run_exec", "run_label"),
    ("argus/provider_integrations/authorization_retry.py", "run_agent_cli.request", "run_label"),
    ("argus/core/run_gateway.py", "run_exec", "run_label"),
    ("argus/core/cost_events.py", "metered_run_exec.wrapped", "run_label"),
    ("argus/core/workbench_plugins.py", "prepare_plugin_run", "run_label"),
    # Role wrappers whose callers choose the label.
    ("argus/core/agent_probe.py", "run_agent_repair_prompt", "run_label"),
    ("argus/core/agent_probe.py", "run_read_only_agent_prompt", "run_label"),
    ("argus/engineer/runner.py", "_run_engineer", "run_label"),
    ("argus/manager/_session_ops.py", "run_exec", "run_label"),
    ("argus/manager/_session_ops.py", "run_exec._no_session", "run_label"),
    ("argus/manager/_stage_ops.py", "_build_stage_run_exec", "run_label"),
    ("argus/manager/_stage_ops.py", "_build_stage_run_exec._run_exec", "run_label"),
    ("argus/manager/_stage_ops.py", "run_turn", "run_label"),
    ("argus/manager/_stage_ops.py", "write_prose", "run_label"),
    ("argus/manager/plan_mode.py", "_resolve_run_exec._call", "run_label"),
    ("argus/manager/plan_mode.py", "draft_plan", "run_label"),
    ("argus/manager/prompt_rewrite.py", "_resolve_run_exec._call", "run_label"),
    ("argus/manager/prompt_rewrite.py", "rewrite_prompt", "run_label"),
    ("argus/webapi/map_model.py", "_run_map_turn", "run_label"),
    ("argus/webapi/map_model.py", "run_map_model", "run_label"),
    # The reader's provenance record carries the literal "reader-foundation".
    ("argus/webapi/reader_foundation.py", "generate_foundation", "record['provenance']['run_label']"),
    # A deterministic backend for tests that replays scripted Planner answers.
    ("argus/apps/_runtime_backends.py", "run_exec", "str(run_label)"),
    # Usage-ledger records of calls that already ran.
    ("argus/core/usage.py", "_legacy_aggregate_record", "run_label"),
    ("argus/core/usage.py", "_legacy_event_records", "str(row.get('run_label') or '')"),
    (
        "argus/core/usage.py", "_legacy_event_records",
        "str(row.get('run_label') or started.get('run_label') or '')",
    ),
    ("argus/core/usage.py", "build_usage_record", "run_label"),
    ("argus/core/usage.py", "build_usage_record", "str(run_label or '')"),
    ("argus/core/usage.py", "from_jsonable", "str(row.get('run_label') or '')"),
}


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------

class Found(NamedTuple):
    where: str
    function: str
    label: str | None  # a label chosen here, rendered with "7" for each placeholder
    passed: str | None  # the expression of a label passed on from elsewhere


def _string_leaves(node: ast.AST) -> list[str] | None:
    """Every label an expression can evaluate to, or ``None`` if not knowable here."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        if node.values and isinstance(node.values[0], ast.FormattedValue):
            return None  # derived from another label; the wrapper allow-list owns it
        return ["".join(
            part.value if isinstance(part, ast.Constant) else "7" for part in node.values
        )]
    if isinstance(node, ast.IfExp):
        body, orelse = _string_leaves(node.body), _string_leaves(node.orelse)
        return None if body is None or orelse is None else body + orelse
    return None


def _module_constants(tree: ast.Module) -> dict[str, list[str]]:
    constants: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            leaves = _string_leaves(node.value)
            if isinstance(target, ast.Name) and leaves is not None:
                constants[target.id] = leaves
    return constants


def _local_assignments(function: ast.AST | None, name: str) -> list[str] | None:
    if function is None:
        return None
    labels: list[str] = []
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        ):
            leaves = _string_leaves(node.value)
            if leaves is None:
                return None
            labels.extend(leaves)
    return labels or None


def _positional_label_slots() -> dict[str, int]:
    """Function name -> positional index of its ``run_label`` parameter at a call."""
    slots: dict[str, int] = {}
    for path in sorted(ARGUS.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
            if "run_label" in names:
                index = names.index("run_label")
                slots[node.name] = index - 1 if names[0] in {"self", "cls"} else index
    return slots


def _sweep() -> Iterator[Found]:
    slots = _positional_label_slots()
    for path in sorted(ARGUS.rglob("*.py")):
        relative = path.relative_to(RELEASE).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_constants(tree)

        def resolve(value: ast.AST, scope: list[ast.AST]) -> list[str] | None:
            leaves = _string_leaves(value)
            if leaves is not None:
                return leaves
            if isinstance(value, ast.Name):
                own = _local_assignments(scope[-1] if scope else None, value.id)
                return own if own is not None else constants.get(value.id)
            return None

        def visit(node: ast.AST, scope: list[ast.AST], names: list[str]) -> Iterator[Found]:
            function = ".".join(names) or "<module>"
            where = f"{relative}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                positional = [*args.posonlyargs, *args.args]
                defaults = dict(zip(
                    [arg.arg for arg in positional[len(positional) - len(args.defaults):]],
                    args.defaults,
                ))
                defaults.update({
                    arg.arg: default
                    for arg, default in zip(args.kwonlyargs, args.kw_defaults)
                    if default is not None
                })
                default = defaults.get("run_label")
                inner = ".".join([*names, node.name])
                if default is not None:
                    for label in _string_leaves(default) or []:
                        if label:
                            yield Found(where, inner, label, None)
                for child in ast.iter_child_nodes(node):
                    yield from visit(child, [*scope, node], [*names, node.name])
                return
            if isinstance(node, ast.Call):
                values = [kw.value for kw in node.keywords if kw.arg == "run_label"]
                callee = node.func.attr if isinstance(node.func, ast.Attribute) else (
                    node.func.id if isinstance(node.func, ast.Name) else ""
                )
                slot = slots.get(callee)
                positional = (
                    [node.args[slot]] if slot is not None and 0 <= slot < len(node.args) else []
                )
                for value in values:
                    labels = resolve(value, scope)
                    if labels is None:
                        yield Found(where, function, None, ast.unparse(value))
                    else:
                        yield from (Found(where, function, label, None) for label in labels)
                for value in positional:
                    # A positional label is a call site only when it is chosen here.
                    labels = resolve(value, [])
                    if labels is not None:
                        yield from (Found(where, function, label, None) for label in labels)
                    elif isinstance(value, ast.JoinedStr):
                        yield Found(where, function, None, ast.unparse(value))
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "run_label":
                        # Only a label chosen here can start a call; recorded ones
                        # were classified where they were chosen.
                        for label in _string_leaves(value) or []:
                            yield Found(where, function, label, None)
            for child in ast.iter_child_nodes(node):
                yield from visit(child, scope, names)

        yield from visit(tree, [], [])


@pytest.fixture(scope="module")
def found() -> list[Found]:
    return list(_sweep())


def _matches(label: str) -> list[Site]:
    return [site for site in REGISTRY if re.fullmatch(site.pattern, label)]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_every_label_that_reaches_a_model_is_classified(found: list[Found]) -> None:
    """A model call nobody classified is the one the idle-spin proof cannot see.

    Every label chosen in ``argus/`` (a literal, an f-string with its fields
    replaced by ``7``, a module constant, a local assignment, a signature
    default, or a ``"run_label"`` dict entry) must match exactly one registry
    entry, so its trigger and its bound are written down.
    """
    chosen = [
        item for item in found
        if item.label is not None
        and (item.where.rsplit(":", 1)[0], item.function, item.label) not in NOT_MODEL_CALLS
    ]
    assert len(chosen) > 35, "the sweep no longer sees the call sites"
    unclassified = [f"{item.where} {item.label!r}" for item in chosen if not _matches(item.label)]
    ambiguous = [
        f"{item.where} {item.label!r}: {[site.pattern for site in _matches(item.label)]}"
        for item in chosen if len(_matches(item.label)) > 1
    ]
    assert not unclassified, "classify these model call sites in REGISTRY:\n" + "\n".join(
        unclassified
    )
    assert not ambiguous, "\n".join(ambiguous)


def test_every_registry_entry_still_has_a_call_site(found: list[Found]) -> None:
    """A stale entry would let a future, different call reuse its classification."""
    labels = {item.label for item in found if item.label is not None}
    unused = [
        site.pattern for site in REGISTRY
        if not any(re.fullmatch(site.pattern, label) for label in labels)
    ]
    assert not unused, unused
    for site in REGISTRY:
        for where in site.where:
            assert (RELEASE / where.rsplit(":", 1)[0]).is_file(), (site.pattern, where)


def test_every_passed_on_label_belongs_to_a_known_wrapper(found: list[Found]) -> None:
    """A new wrapper that forwards a label must be looked at once.

    Forwarding is how labels reach the backend; a label chosen by a new
    wrapper's caller is caught by the sweep above, but only if the wrapper
    really forwards rather than inventing one from runtime data.
    """
    passed = {
        (item.where.rsplit(":", 1)[0], item.function, item.passed)
        for item in found if item.passed is not None
    }
    assert passed - PASS_THROUGH == set(), "review these label forwards:\n" + "\n".join(
        map(str, sorted(passed - PASS_THROUGH))
    )
    assert PASS_THROUGH - passed == set(), sorted(PASS_THROUGH - passed)
    labels = {item.label for item in found if item.label is not None}
    recorded = {label for _path, _function, label in NOT_MODEL_CALLS}
    assert recorded <= labels, sorted(recorded - labels)


def test_registry_examples_name_their_own_entry_and_no_other() -> None:
    for site in REGISTRY:
        assert site.trigger in {FOREGROUND, *BACKGROUND}, site.pattern
        assert site.reason.strip() and site.where and site.examples, site.pattern
        for example in site.examples:
            assert _matches(example) == [site], (example, [s.pattern for s in _matches(example)])


def test_the_daemon_guard_backstops_every_background_label(monkeypatch) -> None:
    """The guard's foreground exemption must not cover a background call.

    In a daemon, background calls are refused after ``BACKGROUND_CALL_BUDGET``
    calls without progress; labels the guard treats as foreground are never
    counted. A background label under a foreground prefix would lose that
    backstop, and a foreground-exempt label must really be started by input.
    """
    monkeypatch.setattr(call_guard, "BACKGROUND_CALL_BUDGET", 0)
    call_guard.enable()
    try:
        exempt = {
            example: call_guard.refusal(example) == ""
            for site in REGISTRY for example in site.examples
        }
    finally:
        call_guard.disable()
    for site in REGISTRY:
        for example in site.examples:
            if site.trigger in BACKGROUND and exempt[example]:
                assert site.pattern in OUTSIDE_THE_DAEMON, (
                    f"{example!r} is {site.trigger} but the daemon guard exempts it"
                )
    assert set(OUTSIDE_THE_DAEMON) <= {
        site.pattern for site in REGISTRY
        if site.trigger in BACKGROUND and all(exempt[example] for example in site.examples)
    }
