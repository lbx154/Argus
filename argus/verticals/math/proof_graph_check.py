"""Check the proof-gap graph, and report what still stands in the way.

Two subcommands:

``check``
    Structural validation — unknown dependencies, cycles, nodes marked proved
    without reviewer confirmation, routes retired without evidence. Exits
    non-zero on any of them, so "the graph says we are nearly there" cannot
    rest on a node nobody confirmed.

``gap``
    The question the graph exists to answer: which unproved propositions the
    goal currently rests on. Prints them, so a round can be judged on whether
    that list got shorter rather than on whether something happened.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .proof_graph import GRAPH_RELPATH, load_graph, template

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "gap"):
        node = sub.add_parser(name)
        node.add_argument("--project-root", type=Path, default=Path.cwd())
    init = sub.add_parser("template")
    init.add_argument("--goal", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "template":
        print(json.dumps(template(args.goal), indent=2, sort_keys=True))
        return 0

    root = Path(args.project_root)
    graph = load_graph(root)
    if graph is None:
        where = root.joinpath(*GRAPH_RELPATH)
        print(f"no proof graph at {where}", file=sys.stderr)
        return 2

    report = graph.gap()
    if args.command == "check":
        for issue in report.issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        if report.issues:
            return 2
        print(f"proof graph valid: {len(graph.nodes)} node(s)")
        return 0

    # gap
    for issue in report.issues:
        print(f"warning: {issue}", file=sys.stderr)
    print(f"goal: {report.goal}")
    print(f"gap: {report.gap_size} unproved proposition(s) the goal rests on")
    for key in report.blocking_nodes:
        statement = str(graph.nodes.get(key, {}).get("statement") or key)
        print(f"  - {key}: {statement}")
    if not report.reachable:
        print(
            "  (no goal node found; the graph cannot say what the gap is)",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
