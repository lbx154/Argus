"""The proof-gap graph: what still stands between here and the goal.

Without it, "how hard was this step" quietly replaces "how much closer did
this get us", because only the first is visible round to round. The system
accumulates correct local lemmas, keeps extending finite verification from
n=3000 to n=5000, and never has to answer: if this lemma were proved
tomorrow, what would still be missing?

Two structures, in order:

**Strategy graph (AND/OR).** Which routes exist, which are ruled out and by
what, which is current, and what would close the goal. Cheap to maintain and
revise, because early on the routes are still moving.

**Proof DAG.** Once a route is chosen: the goal, its lemmas, their
dependencies, and each node's status. Only Reviewer-confirmed propositions
belong here — a DAG full of hopeful nodes measures nothing.

Requiring the DAG too early is its own failure mode: decomposing before the
route is settled makes the system fill in lemmas for a structure it will
throw away. So this is gated on the verification profile — ``explore`` asks
for neither, ``develop`` and ``certify`` require the graph and then the DAG.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "GRAPH_RELPATH",
    "NODE_STATUSES",
    "ROUTE_STATUSES",
    "GapReport",
    "ProofGraph",
    "graph_required_for",
    "load_graph",
]

GRAPH_RELPATH = ("research", "PROOF_GRAPH.json")

#: A route in the strategy graph.
ROUTE_STATUSES = frozenset({"untried", "current", "ruled_out", "parked"})

#: A node in the proof DAG. ``proved`` requires Reviewer confirmation; the
#: others are honest about what is not yet established.
NODE_STATUSES = frozenset({"open", "in_progress", "proved", "refuted"})

#: Profiles that require the structure. `explore` deliberately does not: the
#: route is not settled, and forcing a decomposition there produces lemmas for
#: a structure that gets discarded.
_PROFILES_REQUIRING_GRAPH = frozenset({"develop", "certify"})


def graph_required_for(profile: str, mode: str | None) -> bool:
    """Whether this round must maintain the proof-gap structure.

    Exploratory projects have no single goal to close, so a goal-rooted DAG
    would be a fiction; they still benefit from the strategy graph, but it is
    not gated here.
    """
    return str(profile or "").strip().lower() in _PROFILES_REQUIRING_GRAPH and (
        mode == "targeted"
    )


@dataclass
class GapReport:
    """What stands between the current state and the goal."""

    goal: str
    reachable: bool
    open_nodes: list[str] = field(default_factory=list)
    blocking_nodes: list[str] = field(default_factory=list)
    proved_nodes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def gap_size(self) -> int:
        """How many unproved propositions the goal still rests on."""
        return len(self.blocking_nodes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "reachable": self.reachable,
            "gap_size": self.gap_size,
            "blocking_nodes": list(self.blocking_nodes),
            "open_nodes": list(self.open_nodes),
            "proved_nodes": list(self.proved_nodes),
            "issues": list(self.issues),
        }


class ProofGraph:
    """Strategy graph plus proof DAG, with the checks that make it honest."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        self.goal: str = str(payload.get("goal") or "").strip()
        self.routes: list[dict[str, Any]] = [
            item for item in (payload.get("routes") or []) if isinstance(item, dict)
        ]
        # ``nodes`` is an object keyed by node id. An agent authoring this file
        # by hand writes a list about as often as it writes a mapping, and
        # ``[].items()`` is an AttributeError that escapes ``validate`` — the
        # one method whose whole job is to turn a malformed graph into a
        # sentence the author can act on. Every other field here already
        # tolerates the wrong shape; this one is recorded so ``validate`` can
        # say what was wrong instead of the caller seeing a traceback.
        raw_nodes = payload.get("nodes") or {}
        self.nodes_wrong_shape: str = (
            "" if isinstance(raw_nodes, dict) else type(raw_nodes).__name__
        )
        self.nodes: dict[str, dict[str, Any]] = {
            str(key): value
            for key, value in (raw_nodes.items() if isinstance(raw_nodes, dict) else ())
            if isinstance(value, dict)
        }

    # -- validation --------------------------------------------------------

    def validate(self) -> list[str]:
        """Structural problems that make the graph unable to measure anything."""
        issues: list[str] = []
        if not self.goal:
            issues.append("goal is empty; there is nothing to measure the gap against")
        if self.nodes_wrong_shape:
            issues.append(
                f"nodes is a {self.nodes_wrong_shape}, not an object keyed by node "
                "id; write {\"<node-id>\": {...}} rather than a list of nodes"
            )

        for index, route in enumerate(self.routes):
            name = str(route.get("name") or "").strip()
            status = str(route.get("status") or "").strip()
            if not name:
                issues.append(f"routes[{index}] has no name")
            if status not in ROUTE_STATUSES:
                issues.append(
                    f"routes[{index}] status {status!r} is not one of "
                    f"{', '.join(sorted(ROUTE_STATUSES))}"
                )
            if status == "ruled_out" and not str(route.get("evidence") or "").strip():
                issues.append(
                    f"route {name!r} is ruled_out without evidence; a route retired "
                    "without a reason gets retried"
                )

        for key, node in sorted(self.nodes.items()):
            status = str(node.get("status") or "").strip()
            if status not in NODE_STATUSES:
                issues.append(
                    f"node {key!r} status {status!r} is not one of "
                    f"{', '.join(sorted(NODE_STATUSES))}"
                )
            if status == "proved" and not node.get("reviewer_confirmed"):
                issues.append(
                    f"node {key!r} is marked proved without reviewer confirmation; "
                    "an unconfirmed node makes the gap look smaller than it is"
                )
            for dependency in self._dependencies(node):
                if dependency not in self.nodes:
                    issues.append(f"node {key!r} depends on unknown node {dependency!r}")

        issues.extend(self._cycles())
        return issues

    @staticmethod
    def _dependencies(node: dict[str, Any]) -> list[str]:
        return [str(item) for item in (node.get("depends_on") or [])]

    def _cycles(self) -> list[str]:
        """Report dependency cycles — a proof that rests on itself proves nothing."""
        issues: list[str] = []
        visiting: set[str] = set()
        done: set[str] = set()

        def walk(key: str, trail: list[str]) -> None:
            if key in done:
                return
            if key in visiting:
                cycle = " -> ".join([*trail, key])
                issues.append(f"dependency cycle: {cycle}")
                return
            visiting.add(key)
            for dependency in self._dependencies(self.nodes.get(key, {})):
                if dependency in self.nodes:
                    walk(dependency, [*trail, key])
            visiting.discard(key)
            done.add(key)

        for key in sorted(self.nodes):
            walk(key, [])
        return list(dict.fromkeys(issues))

    # -- the question the graph exists to answer --------------------------

    def gap(self) -> GapReport:
        """What the goal still rests on that is not proved."""
        issues = self.validate()
        proved = sorted(
            key for key, node in self.nodes.items() if node.get("status") == "proved"
        )
        open_nodes = sorted(
            key
            for key, node in self.nodes.items()
            if node.get("status") in {"open", "in_progress"}
        )

        blocking: list[str] = []
        seen: set[str] = set()

        def walk(key: str) -> None:
            if key in seen or key not in self.nodes:
                return
            seen.add(key)
            node = self.nodes[key]
            if node.get("status") == "proved":
                return
            dependencies = [d for d in self._dependencies(node) if d in self.nodes]
            unproved = [
                d for d in dependencies if self.nodes[d].get("status") != "proved"
            ]
            if not unproved:
                # Nothing below it is missing, so this node is the frontier.
                blocking.append(key)
            for dependency in unproved:
                walk(dependency)

        root = self._goal_node()
        if root is not None:
            walk(root)
        else:
            blocking = [key for key in open_nodes]

        return GapReport(
            goal=self.goal,
            reachable=root is not None,
            open_nodes=open_nodes,
            blocking_nodes=sorted(dict.fromkeys(blocking)),
            proved_nodes=proved,
            issues=issues,
        )

    def _goal_node(self) -> str | None:
        for key, node in self.nodes.items():
            if node.get("is_goal"):
                return key
        return self.goal if self.goal in self.nodes else None

    # -- routes ------------------------------------------------------------

    def ruled_out_routes(self) -> list[str]:
        return sorted(
            str(route.get("name") or "")
            for route in self.routes
            if route.get("status") == "ruled_out"
        )

    def current_route(self) -> str:
        for route in self.routes:
            if route.get("status") == "current":
                return str(route.get("name") or "")
        return ""


def load_graph(project_root: object) -> ProofGraph | None:
    """Read the project's proof graph, or ``None`` when it does not exist."""
    path = Path(str(project_root)).joinpath(*GRAPH_RELPATH)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return ProofGraph(payload if isinstance(payload, dict) else {})


def template(goal: str) -> dict[str, Any]:
    """A starting graph with one goal node and no routes decided yet."""
    return {
        "goal": goal,
        "routes": [
            {"name": "REPLACE with a candidate route", "status": "untried", "evidence": ""}
        ],
        "nodes": {
            goal: {
                "statement": goal,
                "status": "open",
                "is_goal": True,
                "depends_on": [],
                "reviewer_confirmed": False,
            }
        },
    }
