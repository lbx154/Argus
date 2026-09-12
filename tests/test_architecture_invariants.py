"""Architecture invariants, in executable form.

Argus is a domain-agnostic runtime: four persistent roles, and verticals as
duck-typed providers that supply domain knowledge without acquiring authority.
Most of that contract currently lives in prose and in the reading habits of
whoever last touched the code, which means it can only be violated silently.

These tests make the load-bearing parts of the boundary fail out loud. What is
pinned here is deliberately the *shape* of each seam, not the behaviour behind
it, so ordinary refactoring stays green and only a genuine boundary change goes
red. Every test below names the property it protects and says, in its
docstring, what breaks in production when that property stops holding.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import argus_skill
from argus_skill.core import project_api
from argus_skill.core.research_contract import normalize_research_result
from argus_skill.core.vertical_contract import (
    _COMPLETION_GATES,
    VerticalContractError,
    vertical_contract,
)
from argus_skill.engineer import external_work
from argus_skill.life.memory import Backlog, BacklogItem
from argus_skill.reviewer import parse_decision_text
from argus_skill.roles.prompts.registry import PROMPT_CATALOG, resolve_role_prompt
from argus_skill.roles.prompts.types import RoleName, RolePromptRequest
from argus_skill.skills.stage_machine import ChecklistItem
from argus_skill.tools.subagent import _registry as subagent_registry
from argus_skill.verticals import _registry as vertical_registry

ARGUS = Path(argus_skill.__file__).resolve().parent


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _python_files(*packages: str) -> list[Path]:
    files: list[Path] = []
    for package in packages:
        base = ARGUS / package
        assert base.is_dir(), f"argus_skill/{package} no longer exists"
        files.extend(sorted(base.rglob("*.py")))
    assert files, f"no python files found under {packages}"
    return files


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    """Absolute dotted names imported by ``path``, relative imports resolved."""
    own_package = ["argus_skill", *path.relative_to(ARGUS).parts[:-1]]
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            tail = node.module.split(".") if node.module else []
            if node.level:
                # level 1 == the module's own package; each extra level pops one.
                base = own_package[: len(own_package) - (node.level - 1)]
            else:
                base = []
            found.append((node.lineno, ".".join([*base, *tail])))
        elif isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
    return found


def _concrete_vertical_imports(paths: list[Path]) -> list[str]:
    """Imports that reach into one named domain rather than the shared bridge.

    ``argus_skill/verticals/*.py`` is the framework-owned bridge (the loader,
    the plugin registry, the data-domain shim, the shared evidence helpers).
    Every *subdirectory* of ``verticals/`` is domain-owned. The rule needs no
    allowlist: a new bridge module or a new vertical classifies itself.
    """
    offenders: list[str] = []
    for path in paths:
        for lineno, module in _imported_modules(path):
            parts = module.split(".")
            if parts[:2] != ["argus_skill", "verticals"] or len(parts) < 3:
                continue
            if (ARGUS / "verticals" / f"{parts[2]}.py").is_file():
                continue  # a bridge module, not a domain
            offenders.append(f"{path.relative_to(ARGUS).as_posix()}:{lineno} -> {module}")
    return offenders


# ``replace`` is deliberately absent: ``str.replace`` is far too common to
# distinguish from ``Path.replace`` by name alone. Nothing is lost -- an atomic
# rewrite still has to ``open``/``write_text`` its temp file first.
_WRITE_VERBS = frozenset({
    "chmod", "dump", "makedirs", "mkdir", "open", "remove", "rename",
    "rmdir", "rmtree", "symlink_to", "touch", "unlink", "write_bytes",
    "write_text", "Popen", "spawnv",
})


def _write_calls(path: Path) -> list[str]:
    """Every call in ``path`` that can create, mutate, or remove a filesystem entry."""
    calls: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name)
            else ""
        )
        if name in _WRITE_VERBS:
            calls.append(f"{path.name}:{node.lineno}: {ast.unparse(node)[:80]}")
    return calls


def _provider(**attrs: object) -> SimpleNamespace:
    """A minimal duck-typed vertical provider that passes contract validation."""
    base: dict[str, object] = {
        "CHECKLIST_STAGE_ORDER": ("work",),
        "CHECKLIST_ITEMS": {
            "work": (ChecklistItem("work.done", "The work is finished", "artifact"),),
        },
        "completion_gate": "none",
    }
    base.update(attrs)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# 1. Four persistent roles, and a vertical is not one of them
# ---------------------------------------------------------------------------

def test_every_persistent_role_owns_exactly_one_prompt_catalog() -> None:
    """The set of roles the runtime can drive is the set that has prompts.

    Manager -> Planner -> Engineer <-> Reviewer is the whole authority chain.
    A role added to the enum without a prompt catalog cannot be driven and
    raises deep inside prompt resolution; a catalog with no enum member is a
    persona nothing can dispatch to. Either way the mismatch is invisible until
    a live mission hits it, so the two tables are pinned to each other here.
    """
    from argus_skill.roles.prompts.registry import _OPERATIONS

    assert {role.value for role in RoleName} == {
        "manager", "planner", "engineer", "reviewer",
    }
    assert set(_OPERATIONS) == set(RoleName)
    assert all(PROMPT_CATALOG.operations_for(role) for role in RoleName)


def test_a_role_the_catalog_does_not_know_is_refused_rather_than_improvised() -> None:
    """An unknown role must not silently fall back to some other role's prompts.

    ``RolePromptRequest`` is a plain dataclass, so nothing stops a caller from
    passing a bare string. If the catalog answered such a request with, say,
    the Engineer's operations, a fifth persona would execute wearing the
    Engineer's authority and nobody would see a mismatch in any log.
    """
    with pytest.raises(ValueError, match="unsupported prompt role"):
        PROMPT_CATALOG.operations_for("auditor")


def test_a_vertical_banner_overlay_does_not_become_a_persistent_role(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``banner_role`` fetches prompt text; it must never select the operation.

    The field is free-form so the Engineer-owned Skill Scientist can pull its
    own overlay without pretending to be a fifth role. If that string ever
    started choosing which operations catalog runs, a vertical could ship an
    "auditor" banner and have the runtime execute it as a role in its own
    right: no Manager routing, no place in the Engineer/Reviewer round, and no
    Reviewer adjudicating its output.
    """
    monkeypatch.delenv("ARGUS_SKILL_EXTERNAL_COMPLETION_GATE", raising=False)
    plugin = ModuleType("plugin.stages")
    plugin.ARGUS_VERTICAL_API_VERSION = vertical_registry.VERTICAL_API_VERSION
    plugin.VERTICAL_PURPOSE = "Invariant probe"
    plugin.CHECKLIST_STAGE_ORDER = ("work",)
    plugin.CHECKLIST_ITEMS = {
        "work": (ChecklistItem("work.done", "The work is finished", "artifact"),),
    }
    plugin.completion_gate = "none"
    plugin.VERTICAL_SKILLS = tmp_path
    plugin.role_banner = lambda role: f"OVERLAY FOR {role}"
    entry = SimpleNamespace(name="probe_lab", value="plugin.stages", load=lambda: plugin)
    monkeypatch.setattr(vertical_registry, "entry_points", lambda group: [entry])
    vertical_registry.refresh_vertical_plugins()
    try:
        resolved = resolve_role_prompt(
            RolePromptRequest(
                role=RoleName.ENGINEER,
                operation="mission",
                vertical="probe_lab",
                banner_role="auditor",
            )
        )
    finally:
        vertical_registry.refresh_vertical_plugins()

    # The overlay is delivered...
    assert resolved.role_banner == "OVERLAY FOR auditor"
    assert resolved.banner_role == "auditor"
    # ...but the acting role, and therefore the authority, is unchanged.
    assert resolved.role is RoleName.ENGINEER
    assert resolved.operation in PROMPT_CATALOG.operations_for(RoleName.ENGINEER)


def test_a_vertical_can_only_state_advice_never_a_verdict() -> None:
    """Everything a provider returns is narrowed to text or to blocking issues.

    This is what keeps a vertical from adjudicating. Its banner is prompt text,
    and its completion validator has exactly one expressive power: *objecting*.
    There is no channel through which it can return "approved" — an empty tuple
    means "no objection from me", and the Reviewer still decides. If a
    structured object survived this narrowing, a vertical could hand the
    runtime a verdict-shaped payload and the round would start honouring it.
    """
    verdict_shaped = vertical_contract("probe", _provider(
        role_banner=lambda role: {"status": "done", "role": role},
        stage_completion_issues=lambda stage, root: [],
    ))

    assert verdict_shaped.banner("engineer") == ""
    # The only two things a validator can say, and neither is an approval.
    assert verdict_shaped.completion_issues("work", Path(".")) == ()
    objecting = vertical_contract("probe", _provider(
        stage_completion_issues=lambda stage, root: ["  the proof is unchecked  "],
    ))
    assert objecting.completion_issues("work", Path(".")) == ("the proof is unchecked",)


def test_a_single_string_of_issues_is_refused_instead_of_split_into_letters() -> None:
    """``"failed"`` iterates into six one-character blockers, all of them lies.

    A validator that returns a bare string instead of a sequence is a plausible
    mistake, and the silent reading of it is catastrophic in the wrong
    direction: the stage looks like it has six unrelated defects. Refusing the
    shape turns a typo into a loud contract error at load time.
    """
    contract = vertical_contract("probe", _provider(
        stage_completion_issues=lambda stage, root: "the proof is unchecked",
    ))

    with pytest.raises(VerticalContractError, match="returned a string"):
        contract.completion_issues("work", Path("."))


# ---------------------------------------------------------------------------
# 2. The framework does not depend on any named domain
# ---------------------------------------------------------------------------

def test_no_framework_package_imports_a_named_vertical() -> None:
    """Core is not the only layer that has to stay domain-blind.

    ``tests/core/test_vertical_contract.py`` guards ``core/``. But the runtime
    that *drives* a mission -- the supervisor, the Engineer/Reviewer round, the
    Planner, the Manager, the prompt resolver, the stage machine -- has the
    same obligation, and nothing checked it. One ``from ..verticals.math import
    ...`` in the supervisor is enough to make every non-math mission import
    math's dependencies, and to make math's stage names special-cased in code
    that is supposed to read them off a contract.
    """
    offenders = _concrete_vertical_imports(_python_files(
        "core", "life", "engineer", "reviewer", "planner", "manager",
        "roles", "skills", "daemon", "team", "webapi", "wiki",
    ))

    assert offenders == []


def test_the_adjudication_round_does_not_load_a_vertical_at_all() -> None:
    """The Engineer and the Reviewer reach the domain only through prompts.

    Both packages today import nothing from ``verticals`` -- not even the
    shared loader. That is stronger than "no named domain" and it is worth
    keeping: whatever the vertical wants said to the Engineer or checked by the
    Reviewer arrives as resolved prompt text and contract values that the loop
    computed, so a vertical cannot reach inside the round that judges its work.
    A loader call here would open exactly that door.
    """
    offenders = [
        f"{path.relative_to(ARGUS).as_posix()}:{lineno} -> {module}"
        for path in _python_files("engineer", "reviewer")
        for lineno, module in _imported_modules(path)
        if module.startswith("argus_skill.verticals")
    ]

    assert offenders == []


def test_project_services_do_not_resolve_dependencies_through_the_web_server() -> None:
    """Per-app dependencies flow from create_app into the extracted services.

    Calling back into the server module would make two app instances share
    whichever dependencies were last patched globally. Keep this boundary
    explicit while the remaining daemon lifecycle services are migrated.
    """
    forbidden = {"argus_skill.webapi.server", "argus_skill.webapi._server_module"}
    offenders = []
    for name in ("project_crud.py", "mission_items.py", "daemon_services.py"):
        path = ARGUS / "webapi" / name
        imports = _imported_modules(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            if (node.level == 1 and node.module is None) or (
                node.level == 0 and node.module == "argus_skill.webapi"
            ):
                imports.extend(
                    (node.lineno, f"argus_skill.webapi.{alias.name}")
                    for alias in node.names
                )
        offenders.extend(
            f"webapi/{name}:{line} -> {module}"
            for line, module in imports if module in forbidden
        )
    assert offenders == []


def test_the_operator_cli_is_the_only_admitted_domain_dependency() -> None:
    """One documented exception exists; it must not quietly become a habit.

    ``argus-skill learn`` calls into ``verticals.learning.ingest`` directly. It
    is an operator-facing command naming the vertical the operator asked for,
    not runtime code branching on a domain, so it is legitimate. It is also the
    entire list. A second entry means someone taught a code path to know a
    domain by name, and this test is where that gets noticed instead of
    discovered later as an import cycle or a mission that only works for math.
    """
    everything = [
        path for path in sorted(ARGUS.rglob("*.py"))
        if path.relative_to(ARGUS).parts[0] != "verticals"
    ]

    importers = sorted({line.split(":")[0] for line in _concrete_vertical_imports(everything)})

    assert importers == ["apps/cli/_core.py"]


# ---------------------------------------------------------------------------
# 3. completion_gate is a three-value vocabulary
# ---------------------------------------------------------------------------

def test_the_gate_vocabulary_and_the_evidence_ranking_stay_in_lockstep() -> None:
    """A gate the contract accepts but the ranking has never heard of fails shut.

    ``vertical_contract`` decides which gate strings may be *declared*;
    ``project_api`` decides what each one *demands*. If a value enters one
    table and not the other, the contract loads the vertical happily and then
    completion scores it against ``_UNKNOWN_GATE_RANK`` -- the strictest
    setting. The symptom is a vertical that asked for a metric and is refused
    completion until someone produces an independent certification, with
    nothing in the error naming the missing table entry.
    """
    assert set(_COMPLETION_GATES) == {"none", "metric", "certified"}
    assert set(project_api._GATE_REQUIRED_RANK) == set(_COMPLETION_GATES)


def test_an_invented_completion_gate_is_refused_when_the_vertical_loads() -> None:
    """Gate names are a closed vocabulary, not free text for a plan document.

    ``full_paper`` has been written down as a gate more than once. It is not
    one. Rejecting it at load time makes the mistake a contract error naming
    the vertical; accepting it would make the vertical load and then behave as
    if it had demanded the strongest possible evidence, which reads as an
    unexplained refusal to ever finish.
    """
    with pytest.raises(VerticalContractError, match="unsupported completion gate"):
        vertical_contract("probe", _provider(completion_gate="full_paper"))


def test_an_unreadable_gate_demands_the_strongest_evidence() -> None:
    """The fallback for a requirement we cannot parse is the strict reading.

    This is the safety net behind the vocabulary check: if a gate string ever
    does reach the ranking without a table entry, the only safe interpretation
    of an unreadable requirement is the strictest one. Ranking it as ``none``
    instead would let the weakest source -- a Planner asserting it is finished
    -- close out work whose actual evidence bar nobody could determine.
    """
    source = project_api.CompletionSource(
        kind=project_api.SOURCE_PLANNER_VERDICT,
        evidence_refs=("notes.md",),
    )

    weak = project_api.evaluate_completion(
        vertical="probe", required_gate="full_paper", source=source
    )
    strong = project_api.evaluate_completion(
        vertical="probe",
        required_gate="full_paper",
        source=project_api.CompletionSource(
            kind=project_api.SOURCE_INDEPENDENT_CERTIFICATION,
            evidence_refs=("certificate.json",),
        ),
    )

    assert weak.accepted is False
    assert strong.accepted is True


# ---------------------------------------------------------------------------
# 4. The per-mission prelude hook
# ---------------------------------------------------------------------------

def _mission(**attrs: object) -> BacklogItem:
    """A claimed backlog item, as the supervisor hands one to the hook."""
    return BacklogItem.new(
        title=str(attrs.pop("title", "Bound the unit-distance count")),
        objective=str(attrs.pop("objective", "Push the exponent below 4/3")),
        **attrs,  # type: ignore[arg-type]
    )


def test_the_prelude_hook_receives_every_argument_by_keyword_and_by_name() -> None:
    """The provider side of this hook is keyword, so the *names* are the contract.

    This test used to pin the opposite -- positional forwarding, where the
    argument *order* was the contract. The protection is the same and the
    reason it exists is the same; only the mechanism moved, because the hook
    grew a fourth argument. Positional forwarding makes adding one silent: the
    mission slides into whatever slot happens to be fourth in a stale
    out-of-tree provider, and every earlier argument is a plausible-looking
    path or stage string, so nothing raises and the vertical writes its scratch
    state into the wrong tree. By keyword, the same stale provider fails with
    ``TypeError: ... unexpected keyword argument 'mission'``, which names the
    problem.

    The probe below declares its parameters in a deliberately scrambled order.
    If anything in the chain reverts to positional forwarding, it gets the
    state root where it asked for the stage and this test goes red.
    """
    seen: list[dict[str, object]] = []
    item = _mission()

    def probe(*, state_root: Path, mission: object, stage: str, project_root: Path) -> str:
        seen.append({
            "stage": stage,
            "project_root": project_root,
            "state_root": state_root,
            "mission": mission,
        })
        return "PRELUDE"

    contract = vertical_contract("probe", _provider(prepare_mission=probe))

    block = contract.prepare_mission(
        stage="solve",
        project_root=Path("/tmp/mission"),
        state_root=Path("/tmp/state"),
        mission=item,
    )

    assert block == "PRELUDE"
    assert seen == [{
        "stage": "solve",
        "project_root": Path("/tmp/mission"),
        "state_root": Path("/tmp/state"),
        "mission": item,
    }]
    # Identity, not equality: the vertical must be able to read the real
    # item's fields. A copy, a dict, or a reconstructed summary would silently
    # lose whichever field the projection targets on.
    assert seen[0]["mission"] is item


def test_a_prelude_written_before_the_mission_argument_fails_by_name() -> None:
    """A stale out-of-tree provider must break loudly, not be quietly demoted.

    The tempting alternative is to inspect the provider's signature and only
    pass ``mission`` when it is accepted. That would keep old providers
    loading, at the price of leaving a vertical permanently and invisibly
    mission-blind: it would return the same block for every task in a stage and
    nobody would ever see an error saying why. The error below is the whole
    point -- it names the argument that has to be added.

    Both providers are exercised through the same call, because "it raised
    ``TypeError``" on its own proves nothing: a runtime that had never heard of
    ``mission`` would raise that too, at the framework end, for every provider
    alike. What is pinned is the *difference* -- current provider served, stale
    provider refused by name.
    """
    call = {
        "stage": "solve",
        "project_root": Path("/tmp/a"),
        "state_root": Path("/tmp/b"),
        "mission": _mission(),
    }
    current = vertical_contract("probe", _provider(
        prepare_mission=lambda *, stage, project_root, state_root, mission: "PRELUDE",
    ))
    stale = vertical_contract("probe", _provider(
        prepare_mission=lambda stage, project_root, state_root: "PRELUDE",
    ))

    assert current.prepare_mission(**call) == "PRELUDE"

    with pytest.raises(TypeError, match="mission"):
        stale.prepare_mission(**call)


def test_a_prelude_may_ignore_the_mission_and_stay_correct() -> None:
    """Per-mission is an *option*, not an obligation.

    ``kernel_engineering`` accepts the item and never reads it, on purpose: its
    baseline workspace is one shared tree per stage, so varying it per claimed
    item would hand two concurrent missions two baselines. A vertical with
    nothing item-specific to say must not be forced to invent something.
    """
    contract = vertical_contract("probe", _provider(
        prepare_mission=lambda **_kwargs: "STAGE BLOCK",
    ))

    assert contract.prepare_mission(
        stage="solve",
        project_root=Path("/tmp/a"),
        state_root=Path("/tmp/b"),
        mission=_mission(),
    ) == "STAGE BLOCK"


def test_a_vertical_without_a_prelude_is_indistinguishable_from_an_empty_one() -> None:
    """The hook is optional, and optional has to mean "contributes nothing".

    Two in-tree verticals implement it. If an absent hook raised, or returned
    ``None``, every call site would need its own guard and every vertical would
    be pushed into implementing a no-op just to stay loadable.
    """
    contract = vertical_contract("probe", _provider())

    assert contract.mission_prelude is None
    assert contract.prepare_mission(
        stage="solve",
        project_root=Path("/tmp/a"),
        state_root=Path("/tmp/b"),
        mission=_mission(),
    ) == ""


def test_a_non_text_prelude_never_reaches_the_mission_prompt() -> None:
    """Whatever comes back is concatenated into the Engineer's prompt.

    A dict, a Path, or a list would either raise inside the string join or --
    worse -- stringify its repr into the prompt the Engineer then works from.
    Dropping a non-string keeps a malformed provider from corrupting the
    mission instruction.
    """
    contract = vertical_contract("probe", _provider(
        prepare_mission=lambda **_kwargs: {"prelude": "hi"},
    ))

    assert contract.prepare_mission(
        stage="solve",
        project_root=Path("/tmp/a"),
        state_root=Path("/tmp/b"),
        mission=_mission(),
    ) == ""


def test_every_prelude_call_site_passes_the_roots_and_the_mission_by_keyword() -> None:
    """This enumerates the blast radius of changing the hook's signature.

    ``prepare_mission`` is called from more than one layer, and the signature
    is expected to grow. Keyword-only calls mean a new parameter is a clean
    ``TypeError`` at every stale call site instead of an argument sliding into
    the wrong slot; requiring the full set here means a call site that quietly
    stopped passing one of them -- so the vertical starts guessing, or goes
    back to answering per stage instead of per mission -- fails in this file
    rather than in a mission.
    """
    call_sites: list[tuple[str, list[str], list[str]]] = []
    for path in sorted(ARGUS.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "prepare_mission"):
                continue
            call_sites.append((
                f"{path.relative_to(ARGUS).as_posix()}:{node.lineno}",
                [ast.unparse(arg) for arg in node.args],
                sorted(str(kw.arg) for kw in node.keywords),
            ))

    assert call_sites, "the prelude hook has no callers; it is dead, not protected"
    assert all(positional == [] for _, positional, _ in call_sites), call_sites
    assert all(
        keywords == ["mission", "project_root", "stage", "state_root"]
        for _, _, keywords in call_sites
    ), call_sites


def test_every_in_tree_prelude_provider_declares_the_four_names_keyword_only() -> None:
    """The other half of the same invariant: definitions, not just call sites.

    Forwarding by keyword makes the provider's parameter *names* contractual.
    The call-site sweep above cannot see that -- it pins what the framework
    sends, and every one of those calls stays valid while a vertical quietly
    renames ``project_root`` to ``root``. Nothing else pins it either: a
    provider is duck-typed, so a rename type-checks, imports, loads, passes
    contract validation, and fails for the first time at mission setup, on the
    path that ends the run (see ``VerticalContract.prepare_mission``).

    So the names are asserted where they are written. Keyword-only as well as
    correctly named, because a positional-or-keyword parameter accepts the call
    today and lets the next reader reorder the signature harmlessly-looking
    tomorrow.
    """
    providers: list[tuple[str, list[str], list[str]]] = []
    for path in sorted((ARGUS / "verticals").rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.FunctionDef) or node.name != "prepare_mission":
                continue
            providers.append((
                f"{path.relative_to(ARGUS).as_posix()}:{node.lineno}",
                [arg.arg for arg in node.args.kwonlyargs],
                [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)],
            ))

    assert providers, "no vertical implements the hook; it is dead, not protected"
    assert all(not positional for _, _, positional in providers), providers
    assert all(
        sorted(kwonly) == ["mission", "project_root", "stage", "state_root"]
        for _, kwonly, _ in providers
    ), providers


# ---------------------------------------------------------------------------
# 5. The Reviewer's structured payload channel
# ---------------------------------------------------------------------------

def _verdict(research_result: str = "") -> str:
    body = (
        "STATUS=done\n"
        "REASON=The synthesis is supported by the cited sources.\n"
        "NEXT_ACTION=\n"
        "FORWARD_PROGRESS=true\n"
    )
    return body + (f"RESEARCH_RESULT={research_result}\n" if research_result else "")


_WELL_FORMED = json.dumps({
    "result_class": "literature_review",
    "correctness_status": "verified",
    "novelty_status": "known",
    "significance_status": "publishable",
    "statement_fidelity_status": "verified",
    "evidence": ["source audit"],
    "limitations": [],
})


def test_a_structured_result_reaches_the_event_payload_as_its_own_copy() -> None:
    """Parsing the field is only half the channel; consumers read the event.

    ``research_result`` is the one structured thing the Reviewer can say beyond
    a status line -- what kind of result this is, and how correct, novel, and
    significant it is. A field that parses but is dropped during serialization
    is invisible to every ledger and digest downstream, and the loss looks
    exactly like a Reviewer that never filled it in. The copy matters too: a
    shared dict lets a downstream consumer edit the Reviewer's finding in place.
    """
    decision = parse_decision_text(_verdict(_WELL_FORMED))
    assert decision is not None
    assert decision.research_result is not None

    payload = decision.to_event_payload()

    assert payload["research_result"] == decision.research_result
    payload["research_result"]["correctness_status"] = "refuted"
    assert decision.research_result["correctness_status"] == "verified"


def test_an_invented_result_vocabulary_is_dropped_without_voiding_the_verdict() -> None:
    """The status line is the verdict; the payload is a claim about its shape.

    If unknown vocabulary passed through, a vertical could mint its own result
    classes and correctness levels, and everything downstream would key off
    strings the framework assigns no meaning to -- a "proved" that no part of
    the runtime knows how to weigh. Dropping the payload is the fail-closed
    choice. Invalidating the whole verdict is not: the Reviewer's decision
    about whether the work is done stands on its own.
    """
    invented = json.dumps({
        # Every other field below is drawn from the framework's vocabulary, so
        # this payload is rejected for the result class alone.
        "result_class": "proved_the_riemann_hypothesis",
        "correctness_status": "verified",
        "novelty_status": "verified_new",
        "significance_status": "publishable",
        "statement_fidelity_status": "verified",
    })

    decision = parse_decision_text(_verdict(invented))

    assert decision is not None
    assert decision.status == "done"
    assert decision.research_result is None


def test_a_half_filled_result_is_discarded_rather_than_completed_with_defaults() -> None:
    """Five judgments, and none of them may be inferred on the Reviewer's behalf.

    Correctness, novelty, significance, statement fidelity, and result class
    are separate findings. Defaulting a missing one -- to "unknown", or to the
    most common value -- would put a judgment in the Reviewer's mouth that the
    Reviewer never made, and downstream nothing can tell an inferred field from
    a stated one. Refusing the partial payload keeps the omission visible.
    """
    partial = json.dumps({
        "result_class": "literature_review",
        "correctness_status": "verified",
        "novelty_status": "known",
        "statement_fidelity_status": "verified",
    })

    assert normalize_research_result(json.loads(partial)) is None
    decision = parse_decision_text(_verdict(partial))
    assert decision is not None and decision.research_result is None


# ---------------------------------------------------------------------------
# 6. Backlog dependencies are AND-only
# ---------------------------------------------------------------------------

def test_the_backlog_offers_exactly_one_dependency_operator() -> None:
    """There is one dependency field and its semantics are conjunctive.

    ``_is_ready`` is ``all(d in done for d in item.deps)``. A second field --
    ``deps_any``, ``requires_one_of``, an operator string -- would change what
    an existing row means without changing the row, because every scheduler
    path (claim, cascade, cycle detection) reads ``deps`` and only ``deps``.
    Anything added here has to be wired through all of them deliberately.
    """
    dependency_fields = {
        name for name in BacklogItem.__dataclass_fields__
        if "dep" in name or "requires" in name or "any_of" in name
    }

    assert dependency_fields == {"deps"}


def test_alternative_routes_to_the_same_goal_are_inexpressible_as_dependencies(
    tmp_path: Path,
) -> None:
    """"Prove it by A *or* by B" has no encoding in the backlog DAG.

    A research plan reaches for this constantly -- a Lean formalization or a
    referee-checked argument, either one settling the claim. The DAG has one
    operator and it is AND, so listing both routes as deps means the consumer
    waits for *both* to succeed. A planner that emits alternatives has to
    collapse them into a single route itself, upstream, or the plan will not
    run. Nobody should be looking for an OR in here later.
    """
    backlog = Backlog(tmp_path / "backlog.jsonl")
    lean = backlog.add(BacklogItem.new(title="lean route", objective="formalize"))
    human = backlog.add(BacklogItem.new(title="human route", objective="write the argument"))
    writeup = backlog.add(BacklogItem.new(
        title="write up the settled claim",
        objective="write up",
        deps=[lean.id, human.id],
    ))

    assert {backlog.claim_next().id, backlog.claim_next().id} == {lean.id, human.id}
    backlog.mark_done(lean.id)

    # One complete route is not enough: the consumer is still not schedulable.
    assert backlog.claim_next() is None
    assert backlog.next_pending() is None
    backlog.mark_done(human.id)
    claimed = backlog.claim_next()
    assert claimed is not None and claimed.id == writeup.id


def test_one_dead_route_permanently_disqualifies_a_consumer_of_both(
    tmp_path: Path,
) -> None:
    """A failed alternative does not degrade to the surviving one -- it kills the node.

    This is the sharp end of AND-only deps. Encode two proof routes as deps of
    the write-up, watch the Lean route fail while the human route succeeds, and
    the write-up is cascade-skipped anyway: its dependency set can never be
    satisfied. The work is not blocked pending a decision, it is terminal, and
    the surviving route's result is stranded with nothing scheduled to consume
    it.
    """
    backlog = Backlog(tmp_path / "backlog.jsonl")
    lean = backlog.add(BacklogItem.new(title="lean route", objective="formalize"))
    human = backlog.add(BacklogItem.new(title="human route", objective="write the argument"))
    writeup = backlog.add(BacklogItem.new(
        title="write up the settled claim",
        objective="write up",
        deps=[lean.id, human.id],
    ))

    backlog.mark_failed(lean.id, error="mathlib is unavailable")
    backlog.mark_done(human.id)

    assert backlog.claim_next() is None
    rows = {item.id: item for item in backlog.all()}
    assert rows[human.id].status == "done"
    assert rows[writeup.id].status == "skipped"
    assert lean.id in rows[writeup.id].last_error


# ---------------------------------------------------------------------------
# 7. .argus_external_work is an observation protocol
# ---------------------------------------------------------------------------

def test_the_external_work_protocol_has_no_writer_while_its_supervisor_does() -> None:
    """Observing external work and launching it are two different mechanisms.

    ``.argus_external_work`` describes work some *other* process already
    started: the runtime reads liveness records it did not author. Adding a
    submit path here would make the runtime both producer and consumer of its
    own heartbeats, and a job that died would be indistinguishable from one the
    runtime simply forgot to update -- the exact failure the stale-heartbeat
    check exists to catch. Launching, PIDs, and reconciliation already live in
    the subagent registry, which is asserted here as the positive control: the
    probe below really does detect writers.
    """
    reader = Path(external_work.__file__).resolve()
    supervisor = Path(subagent_registry.__file__).resolve()

    assert _write_calls(reader) == []
    assert _write_calls(supervisor), "the write probe no longer detects a known writer"
    assert external_work.EXTERNAL_WORK_REGISTRY != str(subagent_registry.REGISTRY_DIR)


def test_reading_external_work_leaves_the_project_tree_byte_identical(
    tmp_path: Path,
) -> None:
    """The observer must not touch what it observes, including by accident.

    An external process owns these records. If a read path created the registry
    directory, rewrote a record to normalize it, or dropped a lock file, it
    would race the owner and could resurrect a record the owner had just
    removed. Comparing the whole tree before and after covers the paths a
    verb-level source scan cannot see -- a write inside a helper, a library
    call, a tempfile left behind.
    """
    registry = tmp_path / external_work.EXTERNAL_WORK_REGISTRY
    registry.mkdir()
    (registry / "job-1.json").write_text(json.dumps({
        "version": external_work.EXTERNAL_WORK_PROTOCOL_VERSION,
        "work_id": "job-1",
        "state": "running_healthy",
        "heartbeat_at": 100.0,
        "stale_after_seconds": 60.0,
        "poll_after_seconds": 30.0,
        "description": "an experiment this runtime did not start",
        "evidence_paths": ["experiments/result.json"],
    }), encoding="utf-8")

    def snapshot() -> dict[str, bytes]:
        return {
            path.relative_to(tmp_path).as_posix(): path.read_bytes()
            for path in sorted(tmp_path.rglob("*")) if path.is_file()
        }

    before = snapshot()
    assert list(external_work.scan_external_work(tmp_path, now=110))
    assert external_work.inspect_external_work(tmp_path, "job-1", now=110) is not None
    assert external_work.render_external_work_advisory(tmp_path, now=110)
    external_work.wait_for_external_work_cadence(
        tmp_path, "job-1", sleep=lambda seconds: None, poll_interval=1, now=lambda: 200.0
    )

    assert snapshot() == before


def test_an_absent_registry_is_read_as_no_work_rather_than_created(
    tmp_path: Path,
) -> None:
    """Most projects never have this directory, and observing must not mint one.

    A reader that calls ``mkdir(exist_ok=True)`` to simplify its glob leaves an
    empty ``.argus_external_work`` in every project it ever looked at. That
    directory is a protocol marker: its presence tells a reader that someone
    intends to report external work here, so creating it on read makes the
    signal meaningless.
    """
    assert list(external_work.scan_external_work(tmp_path, now=110)) == []
    assert external_work.inspect_external_work(tmp_path, "job-1", now=110) is None
    assert external_work.render_external_work_advisory(tmp_path, now=110) == ""

    assert list(tmp_path.iterdir()) == []
