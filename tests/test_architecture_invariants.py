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
import functools
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import NamedTuple

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


class _ImportRecord(NamedTuple):
    lineno: int
    module: str
    names: tuple[str, ...]
    deferred: bool


def _is_type_checking_guard(test: ast.expr) -> bool:
    """``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:`` -- false at runtime."""
    return (
        (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
        or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
    )


def _split_submodules(module: str, names: tuple[str, ...]) -> list[tuple[str, tuple[str, ...]]]:
    """``from pkg import a, b`` -> ``pkg.a`` for each name that is a submodule on disk.

    A name that is a directory or a ``<name>.py`` under ``argus_skill/pkg/``
    is the same import as ``from pkg.a import ...`` and is recorded as
    ``pkg.a`` with no names (one record per submodule). Plain attributes --
    ``__version__``, ``builtin_verticals`` -- stay with ``pkg``. Imports
    outside ``argus_skill`` are passed through untouched.
    """
    parts = module.split(".")
    if parts[0] != "argus_skill":
        return [(module, names)]
    package_dir = ARGUS.joinpath(*parts[1:])
    if not package_dir.is_dir():
        return [(module, names)]
    submodules = tuple(
        name for name in names
        if (package_dir / name).is_dir() or (package_dir / f"{name}.py").is_file()
    )
    attributes = tuple(name for name in names if name not in submodules)
    split: list[tuple[str, tuple[str, ...]]] = [(f"{module}.{name}", ()) for name in submodules]
    if attributes or not names:
        split.append((module, attributes))
    return split


@functools.lru_cache(maxsize=None)
def _import_records(path: Path) -> tuple[_ImportRecord, ...]:
    """Every import in ``path``: absolute dotted module, imported names, deferred flag.

    Relative imports are resolved against the file's own package. ``deferred``
    is true when the statement does not run when the module is loaded: it
    sits inside a function body (runs at call time) or under ``if
    TYPE_CHECKING:`` (never runs; ``TYPE_CHECKING`` is false at runtime).
    Class bodies execute at import time and count as module level. ``from
    pkg import submodule`` is normalised to ``pkg.submodule`` (see
    ``_split_submodules``) so a target never depends on which of two
    equivalent spellings was used. Cached: every scan in this file reads the
    tree's imports from one parse per session.
    """
    own_package = ["argus_skill", *path.relative_to(ARGUS).parts[:-1]]
    found: list[_ImportRecord] = []

    def visit(nodes: Iterable[ast.AST], deferred: bool) -> None:
        for node in nodes:
            if isinstance(node, ast.ImportFrom):
                tail = node.module.split(".") if node.module else []
                if node.level:
                    # level 1 == the module's own package; each extra level pops one.
                    base = own_package[: len(own_package) - (node.level - 1)]
                else:
                    base = []
                names = tuple(alias.name for alias in node.names)
                found.extend(
                    _ImportRecord(node.lineno, module, kept, deferred)
                    for module, kept in _split_submodules(".".join([*base, *tail]), names)
                )
            elif isinstance(node, ast.Import):
                found.extend(
                    _ImportRecord(node.lineno, alias.name, (), deferred) for alias in node.names
                )
            elif isinstance(node, ast.If) and _is_type_checking_guard(node.test):
                visit(node.body, True)
                visit(node.orelse, deferred)
            else:
                visit(
                    ast.iter_child_nodes(node),
                    deferred or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
                )

    visit(ast.iter_child_nodes(ast.parse(path.read_text(encoding="utf-8"))), False)
    return tuple(found)


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    """Absolute dotted names imported by ``path``, relative imports resolved."""
    return [(record.lineno, record.module) for record in _import_records(path)]


def _concrete_vertical_imports(paths: list[Path]) -> list[str]:
    """Imports that reach into one named domain rather than the shared bridge.

    ``argus_skill/verticals/*.py`` is the framework-owned bridge (the loader,
    the plugin registry, the data-domain shim, the shared evidence helpers).
    Every *subdirectory* of ``verticals/`` is domain-owned. The rule needs no
    allowlist: a new bridge module or a new vertical classifies itself. All
    three spellings are caught -- ``from ..verticals.math import stages``,
    ``from ..verticals import math`` and ``from argus_skill.verticals import
    quant as q`` -- because ``_import_records`` resolves an imported name that
    is a directory under ``verticals/`` to that vertical's module path, while
    a bridge module or a plain attribute (``builtin_verticals``) stays put.
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
    that is supposed to read them off a contract. The provider, capability and
    delivery packages are held to the same rule; ``apps`` is the one package
    with an admitted exception, pinned by
    ``test_the_operator_cli_is_the_only_admitted_domain_dependency`` below.
    """
    offenders = _concrete_vertical_imports(_python_files(
        "core", "life", "engineer", "reviewer", "planner", "manager",
        "roles", "skills", "daemon", "team", "webapi", "wiki",
        "tools", "adapters", "agent_cli", "provider_integrations", "plugin",
        "maintenance", "trial", "release_tools", "cli", "integrations", "proof_ledger",
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


# ---------------------------------------------------------------------------
# 8. Declared layering
# ---------------------------------------------------------------------------
#
# Eight layers, low to high. A module-level import may point at its own layer
# or a lower one; every upward edge that exists today is pinned by name, so
# repairing an edge without deleting its allowlist line is red too -- the
# lists can only shrink. This table is the single source that the package
# docstrings (test_every_package_docstring_names_its_layer) and
# docs/LAYOUT.md (test_layout_map_lists_every_directory) are checked against.
# The four package-root modules (``__init__``, ``__main__``, ``loop``,
# ``desktop_backend_entry``) form one pseudo-package ``<root>`` in the
# delivery layer.

LAYERS: dict[str, tuple[str, ...]] = {
    "kernel": ("core", "proof_ledger"),
    "providers": ("agent_cli", "provider_integrations", "adapters", "advisor"),
    "capabilities": ("tools", "wiki", "cli", "skills"),
    "domain": ("verticals", "domains", "builtin_skills"),
    "roles": ("roles", "planner", "engineer", "reviewer"),
    "runtime": ("life", "manager", "messaging"),
    "process": ("daemon", "team"),
    "delivery": (
        "apps", "webapi", "plugin", "maintenance", "trial", "integrations", "release_tools",
    ),
}
LAYER_ORDER = tuple(LAYERS)
REPO_ROOT = ARGUS.parent
_ROOT = "<root>"
_PACKAGE_LAYER = {
    package: layer for layer, packages in LAYERS.items() for package in packages
}


def _layer_of(package: str) -> str:
    if package == _ROOT:
        return "delivery"
    layer = _PACKAGE_LAYER.get(package)
    assert layer is not None, f"argus_skill/{package} is not assigned to any layer in LAYERS"
    return layer


def _layer_rank(package: str) -> int:
    return LAYER_ORDER.index(_layer_of(package))


def _source_package(path: Path) -> str:
    parts = path.relative_to(ARGUS).parts
    return parts[0] if len(parts) > 1 else _ROOT


def _target_package(module: str) -> str:
    """The package a dotted ``argus_skill...`` name lives in; root modules are ``<root>``.

    ``from .. import core`` arrives here as ``argus_skill.core`` (a package),
    ``from .. import __version__`` and ``import argus_skill`` as bare
    ``argus_skill`` and ``argus_skill.loop`` as a root module file; the last
    two are ``<root>``. ``_import_records`` does the per-name resolution.
    """
    parts = module.split(".")
    if len(parts) < 2 or (ARGUS / f"{parts[1]}.py").is_file():
        return _ROOT
    return parts[1]


def _every_python_file() -> list[Path]:
    # Every ``.py`` under the package on purpose, including the two
    # directories with no ``__init__.py`` (research's ``figure_spec_scripts``
    # and ``research_visual_scripts``): they ship in the wheel and run as
    # scripts, so what they import is a coupling the tree still pays for.
    return sorted(ARGUS.rglob("*.py"))


def _cross_package_imports(path: Path) -> list[_ImportRecord]:
    """The ``_import_records`` of ``path`` whose target is another ``argus_skill`` package."""
    source = _source_package(path)
    return [
        record for record in _import_records(path)
        if (record.module == "argus_skill" or record.module.startswith("argus_skill."))
        and _target_package(record.module) != source
    ]


def _allowlist_report(noun: str, measured: frozenset[str], allowlist: frozenset[str]) -> str:
    """Two set differences, each phrased as the action the reader has to take."""
    return "\n".join([
        *(f"remove {entry} from the allowlist (the edge is gone; keep the win)"
          for entry in sorted(allowlist - measured)),
        *(f"new {noun} {entry}" for entry in sorted(measured - allowlist)),
    ])


def _upward_imports(deferred: bool) -> frozenset[str]:
    """``file -> target package`` for imports whose target layer is strictly above the source's."""
    keys: set[str] = set()
    for path in _every_python_file():
        source = _source_package(path)
        relpath = path.relative_to(ARGUS).as_posix()
        for record in _cross_package_imports(path):
            if record.deferred != deferred:
                continue
            target = _target_package(record.module)
            if _layer_rank(target) > _layer_rank(source):
                keys.add(f"{relpath} -> {target}")
    return frozenset(keys)


# Module-level imports that point at a higher layer, keyed by (file, target
# package) exactly like the deferred list below, so re-spelling an import
# inside the target package (``from ..life import event_log`` vs ``from
# ..life.event_log import X``) or renaming a module there does not churn a
# line. Each is a real coupling that fires when the lower package is merely
# imported; phases 1-5 of the architecture plan remove them one at a time,
# deleting the line here as they go. ``core/usage.py ->
# provider_integrations`` (``copilot_usage``) is the one the plan expects to
# survive longest (until the accounting tier leaves core). A target of
# ``<root>`` is ``from .. import __version__``; it goes when
# ``core/version.py`` exists (phase 1).
MODULE_LEVEL_UPWARD_ALLOWLIST: frozenset[str] = frozenset({
    "cli/event_format.py -> life",
    "core/backend_readiness.py -> agent_cli",
    "core/knobs.py -> agent_cli",
    "core/mission_view/_reduce_mission.py -> life",
    "core/operator_messages.py -> life",
    "core/runtime_identity.py -> <root>",
    "core/usage.py -> provider_integrations",
    "engineer/round_prompt.py -> life",
    "engineer/round_reviewer.py -> life",
    "life/chat/router.py -> apps",
    "life/telegram_bot.py -> apps",
    "manager/config_intent.py -> apps",
    "manager/dispatch.py -> apps",
    "manager/observation.py -> daemon",
    "manager/supervision.py -> daemon",
    "provider_integrations/authorization_retry.py -> tools",
    "tools/event_log_query.py -> life",
    "tools/experience.py -> life",
    "tools/manager_live_view.py -> life",
    "tools/manager_live_view.py -> manager",
    "tools/peer.py -> messaging",
    "tools/subagent/_direct_run.py -> daemon",
    "tools/team.py -> team",
    "verticals/research/idea_portfolio.py -> team",
})

# Deferred imports that point at a higher layer, keyed by (file, target
# package): statements inside a function body (run at call time) or under
# ``if TYPE_CHECKING:`` (never run). The constant keeps its original name;
# typing-only imports are counted here too because, like a function-body
# import, they do not fire when the lower package is loaded. They are
# tolerated for that reason, but each one is still a place where a low layer
# knows a high layer's name. ``<root>`` is the package root (``import
# argus_skill`` for its path, or ``from .. import __version__``).
FUNCTION_BODY_UPWARD_ALLOWLIST: frozenset[str] = frozenset({
    "adapters/agent_cli_backend/_core.py -> tools",
    "adapters/agent_cli_backend/_exec.py -> life",
    "adapters/agent_cli_backend/_exec.py -> messaging",
    "adapters/agent_cli_backend/_exec.py -> skills",
    "adapters/agent_cli_backend/_exec.py -> trial",
    "adapters/agent_cli_backend/_exec_finalize.py -> trial",
    "advisor/service.py -> life",
    "advisor/service.py -> trial",
    "agent_cli/_sandbox_commands.py -> reviewer",
    "agent_cli/_sandbox_commands.py -> trial",
    "agent_cli/copilot_acp.py -> daemon",
    "agent_cli/copilot_acp.py -> trial",
    "agent_cli/copilot_home.py -> trial",
    "core/agent_probe.py -> adapters",
    "core/backend_readiness.py -> agent_cli",
    "core/backend_readiness.py -> tools",
    "core/jsonl_reader.py -> life",
    "core/knob_store.py -> agent_cli",
    "core/knobs.py -> agent_cli",
    "core/knobs.py -> tools",
    "core/knobs.py -> trial",
    "core/mission_view/_replay.py -> life",
    "core/operator_context.py -> life",
    "core/operator_context.py -> manager",
    "core/operator_context.py -> tools",
    "core/operator_presence.py -> apps",
    "core/plugin_manager.py -> agent_cli",
    "core/project_api.py -> life",
    "core/provider_quota.py -> provider_integrations",
    "core/role_config.py -> agent_cli",
    "core/sandbox.py -> <root>",
    "core/stage_certificate.py -> skills",
    "core/vault_preflight.py -> tools",
    "daemon/_life_worker_admission.py -> trial",
    "daemon/_life_worker_boot.py -> apps",
    "daemon/_life_worker_run.py -> apps",
    "daemon/_life_worker_runtime_context.py -> apps",
    "daemon/process.py -> trial",
    "daemon/spawn_helper.py -> trial",
    "engineer/round_execution.py -> life",
    "engineer/round_manager_wait.py -> manager",
    "engineer/round_settlement.py -> life",
    "engineer/round_settlement.py -> manager",
    "life/chat/router.py -> apps",
    "life/chat/router.py -> daemon",
    "life/chat/router.py -> webapi",
    "life/recall_embedding.py -> daemon",
    "life/runtime_failure_circuit.py -> <root>",
    "life/supervisor/_core.py -> daemon",
    "life/supervisor/_idle_cycle.py -> apps",
    "life/supervisor/_idle_cycle.py -> daemon",
    "life/supervisor/_planning_context.py -> <root>",
    "life/supervisor/_planning_context.py -> apps",
    "life/supervisor/_planning_cycle_intake.py -> apps",
    "life/supervisor/_planning_cycle_verdict.py -> daemon",
    "manager/_session_ops.py -> daemon",
    "manager/directive.py -> daemon",
    "manager/dispatch.py -> apps",
    "manager/dispatch.py -> daemon",
    "manager/front_door.py -> apps",
    "manager/front_door.py -> daemon",
    "manager/supervision.py -> daemon",
    "provider_integrations/copilot_usage.py -> trial",
    "reviewer/_core.py -> manager",
    "reviewer/review_file.py -> manager",
    "roles/prompts/manager.py -> manager",
    "skills/builtins.py -> domains",
    "skills/builtins.py -> verticals",
    "skills/checklist_store.py -> verticals",
    "skills/loop_prompt.py -> life",
    "skills/loop_prompt.py -> roles",
    "skills/loop_skill_library.py -> verticals",
    "skills/stage_machine.py -> domains",
    "skills/stage_machine.py -> verticals",
    "skills/vertical_select.py -> domains",
    "skills/vertical_select.py -> verticals",
    "team/teammate_entry.py -> apps",
    "tools/setup.py -> trial",
    "tools/subagent/_reporting.py -> apps",
    "verticals/research/idea_portfolio.py -> manager",
    "verticals/research/second_reading.py -> manager",
})

# Cross-package imports of an underscore-private module or name. Every entry
# is a public surface that was never declared; phase 7 (public loader modules)
# is expected to delete the ``verticals._base`` / ``_registry`` /
# ``_data_domain`` rows wholesale.
PRIVATE_IMPORT_ALLOWLIST: frozenset[str] = frozenset({
    "<root> -> argus_skill.apps.tui_launcher._configure_windows_console_encoding",
    "<root> -> argus_skill.verticals._base",
    "adapters -> argus_skill.agent_cli._env",
    "adapters -> argus_skill.agent_cli._structured_output",
    "adapters -> argus_skill.core.cost_control._local_day_start",
    "agent_cli -> argus_skill.daemon.state._terminate_windows_process_tree",
    "apps -> argus_skill.adapters.agent_cli_backend._strip_legacy_codex_profile_args",
    "apps -> argus_skill.manager._session_ops",
    "apps -> argus_skill.manager.config_intent._front_door_classify",
    "apps -> argus_skill.manager.front_door._ensure_manager_runner",
    "apps -> argus_skill.skills.vertical_select._persisted_vertical",
    "apps -> argus_skill.verticals._base",
    "apps -> argus_skill.webapi.manager_state._chat_state_for",
    "core -> argus_skill.agent_cli._process_control",
    "core -> argus_skill.apps._inbox",
    "core -> argus_skill.manager.directive._active_steering_records",
    "core -> argus_skill.manager.directive._read_steering_records",
    "daemon -> argus_skill.agent_cli._process_control",
    "daemon -> argus_skill.apps._inbox",
    "daemon -> argus_skill.apps._runtime",
    "daemon -> argus_skill.life.supervisor._config",
    "daemon -> argus_skill.manager._session_ops",
    "daemon -> argus_skill.skills.vertical_select._persisted_domain",
    "daemon -> argus_skill.skills.vertical_select._persisted_vertical",
    "daemon -> argus_skill.verticals._base",
    "engineer -> argus_skill.reviewer._core",
    "life -> argus_skill.agent_cli._process_control",
    "life -> argus_skill.apps._inbox",
    "life -> argus_skill.apps._inbox_delivery",
    "life -> argus_skill.apps._life_actions",
    "life -> argus_skill.core.mission_view._replay",
    "life -> argus_skill.core.operator_context._current_mission_id",
    "life -> argus_skill.daemon.state._fsync_directory",
    "life -> argus_skill.manager.config_intent._apply_config_intent",
    "life -> argus_skill.manager.config_intent._front_door_classify",
    "life -> argus_skill.manager.front_door._accepts_parameter",
    "life -> argus_skill.planner.planner._GLOBAL_KEY_VALUE_KEYS",
    "life -> argus_skill.roles.prompts.manager._IDENTITY_GUARD",
    "life -> argus_skill.tools.subagent._registry",
    "life -> argus_skill.verticals._base",
    "life -> argus_skill.verticals._data_domain",
    "life -> argus_skill.webapi.manager_bridge._answer_inline",
    "maintenance -> argus_skill.agent_cli._process_control",
    "manager -> argus_skill.apps._inbox",
    "manager -> argus_skill.apps._life_actions",
    "manager -> argus_skill.apps._runtime",
    "manager -> argus_skill.apps._runtime_construction",
    "manager -> argus_skill.apps.cli._follow",
    "manager -> argus_skill.daemon.state._fsync_directory",
    "manager -> argus_skill.life.memory._TERMINAL_STATUSES",
    "manager -> argus_skill.life.memory._read_jsonl_tail_history",
    "manager -> argus_skill.skills.stage_machine._active_vertical_checklist_defs",
    "manager -> argus_skill.skills.stage_machine._ensure_stage_completion",
    "manager -> argus_skill.verticals._base",
    "manager -> argus_skill.verticals._data_domain",
    "messaging -> argus_skill.manager._session_ops",
    "reviewer -> argus_skill.core.role_reply._line_pattern",
    "reviewer -> argus_skill.roles.prompts.reviewer._REEVALUATE_HEADER",
    "reviewer -> argus_skill.roles.prompts.reviewer._engineer_log_audit_block",
    "reviewer -> argus_skill.roles.prompts.reviewer._load_wiki_curator_skill_if_present",
    "reviewer -> argus_skill.roles.prompts.reviewer._verification_directive",
    "roles -> argus_skill.skills.vertical_select._persisted_vertical",
    "roles -> argus_skill.verticals._base",
    "skills -> argus_skill.verticals._base",
    "skills -> argus_skill.verticals._data_domain",
    "skills -> argus_skill.verticals._registry",
    "skills -> argus_skill.wiki.store._atomic_write_text",
    "team -> argus_skill.apps._runtime",
    "team -> argus_skill.apps._runtime_supervisor",
    "team -> argus_skill.daemon.state._terminate_windows_process_tree",
    "team -> argus_skill.verticals._base",
    "tools -> argus_skill.agent_cli._process_control",
    "tools -> argus_skill.apps._inbox",
    "tools -> argus_skill.daemon.state._terminate_windows_process_tree",
    "trial -> argus_skill.agent_cli.copilot_home._read_managed_config",
    "trial -> argus_skill.tools.setup._verify_setup_smoke",
    "verticals -> argus_skill.adapters.agent_cli_backend._strip_legacy_codex_profile_args",
    "verticals -> argus_skill.skills.rl_training_plots._is_probe",
    "verticals -> argus_skill.skills.rl_training_plots._read_optimizer_steps",
    "verticals -> argus_skill.tools.image_api._DEFAULT_MAX_RETRIES",
    "verticals -> argus_skill.tools.image_api._DEFAULT_TIMEOUT_SECONDS",
    "verticals -> argus_skill.tools.image_api._atomic_write_json",
    "verticals -> argus_skill.tools.image_api._data_url",
    "verticals -> argus_skill.tools.image_api._json_request",
    "verticals -> argus_skill.tools.image_api._load_sidecar_prompt",
    "verticals -> argus_skill.tools.image_api._parse_chat_text",
    "verticals -> argus_skill.tools.image_api._parse_responses_text",
    "verticals -> argus_skill.tools.image_api._read_prompt",
    "verticals -> argus_skill.tools.image_api._redact",
    "verticals -> argus_skill.tools.image_api._require_route",
    "verticals -> argus_skill.tools.lean_check._artifact_directory_lock",
    "verticals -> argus_skill.tools.lean_check._atomic_artifact_write",
    "verticals -> argus_skill.tools.lean_check._resolve_lake_workspace",
    "webapi -> argus_skill.agent_cli._env",
    "webapi -> argus_skill.apps._inbox",
    "webapi -> argus_skill.apps._life_actions",
    "webapi -> argus_skill.apps.cli._follow",
    "webapi -> argus_skill.daemon.life_worker._acquire_daemon_spawn_lock",
    "webapi -> argus_skill.daemon.life_worker._active_daemon_count",
    "webapi -> argus_skill.daemon.life_worker._active_workspace_owner",
    "webapi -> argus_skill.daemon.life_worker._launcher_failure_message",
    "webapi -> argus_skill.daemon.life_worker._max_active_daemons",
    "webapi -> argus_skill.daemon.life_worker._release_daemon_spawn_lock",
    "webapi -> argus_skill.daemon.life_worker._workspace_start_error",
    "webapi -> argus_skill.life.memory._TERMINAL_STATUSES",
    "webapi -> argus_skill.life.memory._append_jsonl",
    "webapi -> argus_skill.life.memory._jsonl_history_paths",
    "webapi -> argus_skill.life.memory._read_jsonl_tail",
    "webapi -> argus_skill.life.memory._read_jsonl_tail_history",
    "webapi -> argus_skill.life.supervisor._mission_execution_runtime",
    "webapi -> argus_skill.manager._session_ops",
    "webapi -> argus_skill.manager.config_intent._apply_config_intent",
    "webapi -> argus_skill.manager.config_intent._front_door_classify",
    "webapi -> argus_skill.manager.dispatch._daemon_status",
    "webapi -> argus_skill.manager.front_door._accepts_parameter",
    "webapi -> argus_skill.manager.front_door._derive_session_name",
    "webapi -> argus_skill.manager.front_door._ensure_manager_runner",
    "webapi -> argus_skill.manager.front_door._operator_workspace",
})


def test_every_package_is_assigned_to_exactly_one_layer() -> None:
    """A package with no layer has no import rule, so nothing below can judge it.

    The three allowlist tests derive "upward" from ``LAYERS``. A new
    ``argus_skill/<pkg>/`` that is not in the table would make ``_layer_of``
    fail on the first file that imports it -- or, worse, never be scanned at
    all if it only *imports* others. A package listed twice would have two
    ranks and the rule would depend on dict iteration order.
    """
    on_disk = {
        path.name for path in ARGUS.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    declared = [package for packages in LAYERS.values() for package in packages]

    unassigned = sorted(on_disk - set(declared))
    assert unassigned == [], (
        f"add the package to LAYERS in tests/test_architecture_invariants.py: {unassigned}"
    )
    vanished = sorted(set(declared) - on_disk)
    assert vanished == [], (
        f"no longer on disk; remove from LAYERS in tests/test_architecture_invariants.py: {vanished}"
    )
    assert len(declared) == len(set(declared)), "a package is listed in two layers"


def test_module_level_imports_never_point_to_a_higher_layer() -> None:
    """Loading a low layer must not drag a high one into the process.

    A module-level import runs when the importer is imported, so ``core/`` --
    which every entry point and every test loads first -- pulling in
    ``agent_cli`` or ``life`` means there is no such thing as a cheap or
    isolated import anywhere in the tree, and any cycle through the high layer
    surfaces as an ``ImportError`` in whichever module happens to be loaded
    first that day. The set above is today's exact list of such edges, keyed
    ``file -> target package``. Strict equality means both directions are red:
    a new edge, and an edge that was repaired without deleting its line (the
    ratchet only turns one way).
    """
    measured = _upward_imports(deferred=False)

    assert measured == MODULE_LEVEL_UPWARD_ALLOWLIST, _allowlist_report(
        "upward import", measured, MODULE_LEVEL_UPWARD_ALLOWLIST
    )


def test_function_body_imports_to_higher_layers_are_pinned() -> None:
    """Deferred upward imports are tolerated, counted, and not allowed to multiply.

    An import inside a function is how a low layer reaches a high one without
    coupling at load time, and the tree relies on it in sixty-odd places. It
    is still a place where ``core`` knows the name of ``trial`` or ``skills``
    knows ``verticals``; each one is a monkeypatch target, a hidden cycle
    waiting for the function to be called at import time, and a line that has
    to move when the target package does. An import under ``if
    TYPE_CHECKING:`` is counted here for the same reason: it never runs, so
    it is not a load-time edge, but the name is still known. Coarse keys
    (file -> package) so that reorganising *inside* the target package stays
    green.
    """
    measured = _upward_imports(deferred=True)

    assert measured == FUNCTION_BODY_UPWARD_ALLOWLIST, _allowlist_report(
        "upward import", measured, FUNCTION_BODY_UPWARD_ALLOWLIST
    )


def test_private_modules_are_not_imported_across_packages() -> None:
    """A leading underscore is a promise that only the owning package reads it.

    Every cross-package import of ``_base``, ``_process_control`` or a
    ``_helper`` name silently converts a private into a public surface that
    its owner does not know it is maintaining: a rename that looks local
    breaks another package at import time, and the private's caller has no
    contract to point at. Pinning today's set means a new one has to be
    argued for, and the phase that publishes the loader modules has to delete
    its rows here to prove the leak is closed.
    """
    measured: set[str] = set()
    for path in _every_python_file():
        source = _source_package(path)
        for record in _cross_package_imports(path):
            components = record.module.split(".")[1:]
            if any(_is_private(component) for component in components):
                measured.add(f"{source} -> {record.module}")
                continue
            measured.update(
                f"{source} -> {record.module}.{name}" for name in record.names if _is_private(name)
            )

    assert frozenset(measured) == PRIVATE_IMPORT_ALLOWLIST, _allowlist_report(
        "private import", frozenset(measured), PRIVATE_IMPORT_ALLOWLIST
    )


def _is_private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


_LAYER_LINE = re.compile(r"^Layer: (\w+)$")


def test_every_package_docstring_names_its_layer() -> None:
    """The layer a package sits in is written where its reader will look first.

    ``LAYERS`` is the enforced truth, but nobody opens a test file to learn
    where a package belongs; they open its ``__init__``. A docstring that is
    empty, or that names a different layer than the table, sends the next
    contributor's import to the wrong place with the test as their only
    warning. Exactly one ``Layer:`` line, matching the table, per package.
    """
    problems: list[str] = []
    for package, layer in sorted(_PACKAGE_LAYER.items()):
        init = ARGUS / package / "__init__.py"
        docstring = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8"))) or ""
        declared = [
            match.group(1) for line in docstring.splitlines()
            if (match := _LAYER_LINE.match(line.strip()))
        ]
        if not docstring.strip():
            problems.append(f"argus_skill/{package}/__init__.py has no docstring")
        elif declared != [layer]:
            problems.append(
                f"argus_skill/{package}/__init__.py declares Layer: {declared or 'nothing'}, "
                f"LAYERS says {layer}"
            )

    assert problems == []


LAYOUT_MAP = REPO_ROOT / "docs" / "LAYOUT.md"


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _layer_table(text: str) -> dict[str, frozenset[str]]:
    """layer -> packages, from the first ``| Layer | Packages | May import |`` table."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _table_cells(line)[:3] == ["Layer", "Packages", "May import"]:
            break
    else:
        pytest.fail("docs/LAYOUT.md has no table headed | Layer | Packages | May import |")
    mapping: dict[str, frozenset[str]] = {}
    for line in lines[index + 1:]:
        cells = _table_cells(line)
        if not cells:
            break
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue  # the header/body separator row
        if len(cells) < 2:
            pytest.fail(f"docs/LAYOUT.md layer table row has no Packages cell: {line!r}")
        mapping[cells[0]] = frozenset(
            entry.strip().strip("`") for entry in cells[1].split(",") if entry.strip()
        )
    return mapping


def _tracked_top_level_directories() -> list[str]:
    try:
        listing = subprocess.run(
            ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True,
            check=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"git ls-files is unavailable here: {exc}")
    return sorted({line.split("/", 1)[0] for line in listing.stdout.splitlines() if "/" in line})


def _has_bullet(text: str, entry: str) -> bool:
    """A line of the map's own form ``- `entry` ...``, not a mention in passing.

    ``integrations/`` occurs inside ``argus_skill/integrations/`` and
    ``docs/`` inside ``docs/audits/``; a substring test would let the bullet
    for either disappear unnoticed.
    """
    return re.search(rf"^- `{re.escape(entry)}`", text, re.M) is not None


def test_layout_map_lists_every_directory() -> None:
    """The written map and the enforced table are the same table.

    ``docs/LAYOUT.md`` is what a reader is pointed at; ``LAYERS`` is what the
    tests enforce. If a package moves layers in one and not the other, the
    document teaches an import the tests then reject -- or accepts one they
    would have caught. The second half pins coverage: every package and every
    tracked top-level directory has its own bullet, so the next ``research/``
    or ``companions/`` cannot appear without saying whether it is built,
    tested and shipped.
    """
    tracked_directories = _tracked_top_level_directories()  # skips before any assert without git
    assert LAYOUT_MAP.is_file(), "docs/LAYOUT.md is missing; the layering has no written map"
    text = LAYOUT_MAP.read_text(encoding="utf-8")

    assert _layer_table(text) == {
        layer: frozenset(packages) for layer, packages in LAYERS.items()
    }
    unmentioned_packages = [
        f"argus_skill/{package}/" for package in sorted(_PACKAGE_LAYER)
        if not _has_bullet(text, f"argus_skill/{package}/")
    ]
    assert unmentioned_packages == [], (
        "each needs its own docs/LAYOUT.md line starting with '- `argus_skill/<pkg>/`'"
    )
    unmentioned_directories = [
        f"{directory}/" for directory in tracked_directories
        if not _has_bullet(text, f"{directory}/")
    ]
    assert unmentioned_directories == [], (
        "each needs its own docs/LAYOUT.md line starting with '- `<dir>/`'"
    )


# After the import, every ``argus_skill.*`` module in ``sys.modules`` must be
# the package root itself or live in a kernel package (``LAYERS["kernel"]``).
_KERNEL_PROBE = "; ".join([
    "import argus_skill.core.paths, sys",
    f"kernel = tuple('argus_skill.' + package for package in {LAYERS['kernel']!r})",
    "bad = sorted(m for m in sys.modules if m.startswith('argus_skill.')"
    " and m not in kernel and not m.startswith(tuple(k + '.' for k in kernel)))",
    "print(chr(10).join(bad))",
])


def test_importing_the_kernel_does_not_load_the_engine() -> None:
    """``import argus_skill.core.paths`` must cost the kernel, not the whole runtime.

    Python imports ``argus_skill/__init__`` before any submodule, so an eager
    ``from .loop import SkillLoop`` there means every subprocess that wants a
    path helper -- the daemon spawn helper, the desktop entry, a vertical's
    evaluation script -- pays for the Engineer, the Reviewer, the skill store
    and every role prompt, and inherits every import-time side effect they
    carry. The assertion is the invariant itself, not a list of suspects:
    anything outside ``core`` and ``proof_ledger`` -- ``tools``, ``manager``,
    ``verticals``, ``daemon`` included -- is a failure. Measured in a fresh
    interpreter because this process already has all of it loaded.
    """
    probe = subprocess.run(
        [sys.executable, "-c", _KERNEL_PROBE], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=120,
    )

    assert probe.returncode == 0, probe.stderr
    loaded = probe.stdout.split()
    assert loaded == [], (
        f"importing argus_skill.core.paths loaded {len(loaded)} modules outside the kernel:\n"
        f"{probe.stdout}"
    )


_DOTTED_PATH = re.compile(r"\bargus_skill(\.[A-Za-z_][A-Za-z0-9_]*)+")

# Cited paths that are illustrative rather than real modules. Empty today:
# every ``argus_skill.x.y`` in a Skill, plugin document or role prompt resolves.
CITED_PATH_EXCEPTIONS: frozenset[str] = frozenset()


def _cited_module_paths() -> dict[str, str]:
    """dotted path -> where it was first seen, from Skill/plugin markdown and role prompts."""
    cited: dict[str, str] = {}

    def note(text: str, where: str) -> None:
        for match in _DOTTED_PATH.finditer(text):
            cited.setdefault(match.group(0), where)

    for base in ("argus_skill", "plugins", "integrations"):
        for path in sorted((REPO_ROOT / base).rglob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                note(line, f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}")
    for path in sorted((ARGUS / "roles" / "prompts").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                note(node.value, f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}")
    return cited


def _resolves_on_disk(dotted: str) -> bool:
    """Walk the dotted path against the tree under ``argus_skill/``; nothing is imported.

    Each segment must be a directory (a package, with or without
    ``__init__.py``) or a ``<name>.py`` module file. Once a module *file* is
    reached, up to two further segments are accepted as an attribute
    (``module.func``, ``module.Class.method``). A directory ends the path, so
    ``argus_skill.bogus`` does not pass merely because ``argus_skill`` exists.
    ``find_spec`` would execute every parent package (and, for ``module.attr``
    citations, the module itself); this walk executes none of them.
    """
    parts = dotted.split(".")
    if parts[0] != "argus_skill":
        return False
    current = ARGUS
    for index, part in enumerate(parts[1:], start=1):
        if (current / part).is_dir():
            current = current / part
        elif (current / f"{part}.py").is_file():
            return len(parts) - index - 1 <= 2
        else:
            return False
    return True


def test_module_paths_cited_by_prompts_and_skills_resolve() -> None:
    """A path the model is told to run must be a path that exists.

    Skills and role prompts say ``python -m argus_skill.tools.subagent`` and
    ``argus_skill.verticals.math.citation_check`` in prose the runtime never
    parses. When a module moves, nothing fails at import time -- the Engineer
    fails at mission time, after burning a round on ``No module named``, and
    the Reviewer may never see why. Paths are resolved against the filesystem,
    so the check is cheap and imports nothing -- a vertical whose
    ``__init__`` raises in a trimmed environment still gets a readable
    "unresolved" list rather than a traceback from here.
    """
    cited = _cited_module_paths()
    assert cited, "no module paths are cited anywhere; the scan is broken, not the prose"

    unresolved = [
        f"{dotted} (cited at {where})" for dotted, where in sorted(cited.items())
        if dotted not in CITED_PATH_EXCEPTIONS and not _resolves_on_disk(dotted)
    ]
    stale_exceptions = sorted(CITED_PATH_EXCEPTIONS - set(cited))

    assert unresolved == []
    assert stale_exceptions == [], "no longer cited; remove from CITED_PATH_EXCEPTIONS"


# Module paths that appear on a ``python -m`` / argv line somewhere in the tree
# and are therefore matched back by string in a *different* process.
SUBPROCESS_REENTRY_MODULES = (
    "argus_skill.team.teammate_entry",
    "argus_skill.tools.subagent",
    "argus_skill.daemon.spawn_helper",
    "argus_skill.reviewer.review_file",
    "argus_skill.tools.manager_live_view",
    "argus_skill.desktop_backend_entry",
    "argus_skill.plugin.mcp_server",
    "argus_skill.__main__",
)


def test_subprocess_reentry_module_paths_stay_importable() -> None:
    """These names cross a process boundary as strings, so a move is invisible to Python.

    The daemon spawns ``python -m argus_skill.daemon.spawn_helper``; teammates
    re-enter through ``team.teammate_entry``; liveness checks match those same
    strings against ``argv`` of running processes. Rename one and the
    importer-side tests stay green while the live system either fails to
    spawn or -- worse -- stops recognising its own workers as its own.
    """
    missing = [
        dotted for dotted in SUBPROCESS_REENTRY_MODULES
        if importlib.util.find_spec(dotted) is None
    ]

    assert missing == []


_STAGE_WRITERS = frozenset({"advance_stage", "rollback_stage", "complete_final_stage"})

# References to the stage writers from outside argus_skill/manager/ (and
# outside skills/stage_machine.py, which defines them), per file. Counted:
# every ``from ... import advance_stage [as alias]`` binding, plus every load
# of a bound alias or of the literal name -- a call, ``stage_machine.
# rollback_stage(...)`` through a module attribute, or the function passed on
# as a callback. One statement that imports and calls once is therefore 2.
STAGE_WRITER_REFERENCES_OUTSIDE_MANAGER: dict[str, int] = {
    "life/supervisor/_mission_execution_settlement.py": 2,
    "life/supervisor/_planning_cycle_enqueue.py": 6,
    "skills/vertical_select.py": 2,
}


def _stage_writer_references(path: Path) -> int:
    """Bindings of a stage writer plus loads of any name bound to one (see the dict above)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = set(_STAGE_WRITERS)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _STAGE_WRITERS:
                    bound.add(alias.asname or alias.name)
                    count += 1
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(node.ctx, ast.Load):
            continue
        if isinstance(node, ast.Name) and node.id in bound:
            count += 1
        elif isinstance(node, ast.Attribute) and node.attr in _STAGE_WRITERS:
            count += 1
    return count


def _baseline_report(measured: dict[str, int], baseline: dict[str, int], constant: str) -> str:
    """Per-key differences, then the one edit that resolves each direction."""
    moved = [
        f"{key}: baseline {baseline.get(key, 0)}, now {measured.get(key, 0)}"
        for key in sorted(set(measured) | set(baseline))
        if measured.get(key, 0) != baseline.get(key, 0)
    ]
    return "\n".join([
        *moved,
        f"lower {constant} in tests/test_architecture_invariants.py if the reduction is intended; "
        "a new or larger entry is a second authority and has to be argued for, not pinned",
    ])


def test_only_the_manager_advances_the_pipeline_stage() -> None:
    """"Manager is the sole writer of the pipeline stage" as a number that can only fall.

    The prose rule has five exceptions today, all in the supervisor and the
    vertical selector, each of them acting on the Manager's behalf. A sixth
    caller of ``advance_stage`` / ``rollback_stage`` / ``complete_final_stage``
    -- in a vertical, a tool, a web route -- is a second authority over which
    stage the project is in, and stage-scoped state (checklists, certificates,
    skill selection) would start disagreeing with itself. The count follows
    the *binding*, so ``import advance_stage as _adv; _adv(...)`` -- the house
    idiom inside ``manager/_stage_ops.py`` -- is not a way around it.
    """
    counts: dict[str, int] = {}
    for path in _every_python_file():
        relpath = path.relative_to(ARGUS).as_posix()
        if relpath.startswith("manager/") or relpath == "skills/stage_machine.py":
            continue
        if references := _stage_writer_references(path):
            counts[relpath] = references

    assert counts == STAGE_WRITER_REFERENCES_OUTSIDE_MANAGER, _baseline_report(
        counts, STAGE_WRITER_REFERENCES_OUTSIDE_MANAGER, "STAGE_WRITER_REFERENCES_OUTSIDE_MANAGER"
    )


@functools.lru_cache(maxsize=None)
def _package_sources() -> dict[str, str]:
    return {
        path.relative_to(ARGUS).as_posix(): path.read_text(encoding="utf-8")
        for path in _every_python_file()
    }


def _ratchet_report(name: str, baseline: int, per_file: dict[str, int], constant: str) -> str:
    """A pinned count moved: which way, where most of the occurrences are, what to edit."""
    actual = sum(per_file.values())
    top = sorted(
        ((relpath, count) for relpath, count in per_file.items() if count),
        key=lambda item: (-item[1], item[0]),
    )[:3]
    where = ", ".join(f"{relpath} ({count})" for relpath, count in top) or "nowhere"
    action = (
        f"lower {constant} in tests/test_architecture_invariants.py if the reduction is intended"
        if actual < baseline else
        f"remove the new occurrences (or raise {constant} in tests/test_architecture_invariants.py "
        "with a reason)"
    )
    return f"{name}: baseline {baseline}, now {actual} (most in {where}); {action}"


# Retired spellings of "the project state directory" (canonical: ``life_dir``
# and ``core.paths.project_state_root``). Whole-word occurrence counts, today.
RETIRED_NAME_OCCURRENCES: dict[str, int] = {
    "life_root": 13,
    "memory_root": 42,
    "session_root": 40,
    "project_dir": 33,
    "manager_session_root": 24,
    "session_states_root": 22,
    "session_state_root": 31,
}

# Prose that describes a runtime this tree no longer contains.
STALE_PROSE = (
    "MissionExecutor",
    "JsonlCommandBus",
    "_VENDORED",
    "matcher → distiller",
    "vendored from ArgusBot",
)


def test_retired_names_do_not_spread() -> None:
    """Six names for one directory is how a path ends up computed six ways.

    ``life_root``, ``memory_root``, ``session_root`` and the rest all mean
    ``~/.argus-skill/projects/<id>/``; each spelling is a place where the next
    reader guesses whether it is the host root or the project root. Their
    counts are pinned so that a new occurrence is a conscious choice, and a
    removal is banked. The stale prose is pinned at zero: a docstring that
    names ``MissionExecutor`` or a vendored ``ArgusBot`` reviewer sends a
    reader looking for code that does not exist.
    """
    sources = _package_sources()
    problems: list[str] = []
    for name, expected in RETIRED_NAME_OCCURRENCES.items():
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        per_file = {relpath: len(pattern.findall(text)) for relpath, text in sources.items()}
        if sum(per_file.values()) != expected:
            problems.append(_ratchet_report(
                name, expected, per_file, f"RETIRED_NAME_OCCURRENCES[{name!r}]"
            ))
    for phrase in STALE_PROSE:
        hits = {relpath: text.count(phrase) for relpath, text in sources.items() if phrase in text}
        if hits:
            problems.append(
                f"{phrase!r}: baseline 0, now {sum(hits.values())} in {sorted(hits)}; "
                "the prose describes code this tree no longer has -- rewrite it"
            )

    assert problems == []


MEMORY_ROOT_READS = 79


def test_memory_root_reads_do_not_grow() -> None:
    """``MemoryBundle.root`` returns the *host* root; every reader of it is a trap.

    ``life/memory.py`` defines ``root`` as ``~/.argus-skill``, not the
    project state directory a supervisor author expects when the bundle they
    hold is per-project. Today ``memory.root`` is read 79 times, all in
    ``life/supervisor``, and each read is a candidate for writing project
    state into the host root. Phase 8 renames them to ``global_root`` /
    ``project_root`` one by one; the count is pinned so it can only fall.
    """
    pattern = re.compile(r"\bmemory\.root\b")
    per_file = {relpath: len(pattern.findall(text)) for relpath, text in _package_sources().items()}

    assert sum(per_file.values()) == MEMORY_ROOT_READS, _ratchet_report(
        "memory.root", MEMORY_ROOT_READS, per_file, "MEMORY_ROOT_READS"
    )
