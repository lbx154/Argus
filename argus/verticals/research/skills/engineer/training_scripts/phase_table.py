#!/usr/bin/env python3
"""Where a training step spends its time: each phase's share and generation tokens/s.

Reads the phase log of a few real steps: one JSON object per line,
``{"phase": "rollout", "kind": "generation", "seconds": 54.5, "generated_tokens": 3003}``
(``phase()`` below writes them, with a ``start``), or a framework log whose lines
carry ``<prefix><phase>:<seconds>`` (verl prints ``timing_s/gen:12.5``). A row
without a kind is given one from its name, and the table says so. Each second is
summed once: a row whose time lies inside another row's (by ``start`` or
``start_monotonic``) is left out, and so are the rows ``--skip`` names, which logs
without start times need: by default totals, steps, and verl's generate_sequences
and reshard, which run inside gen. Shares are ratios inside one run, so they hold
on a shared device too. ``--json`` prints the largest kind as one object. It only
reports; the exit code is 0 whatever it reads.

    python phase_table.py LOG [--engine native|<engine>] [--skip REGEX] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import time
from collections import defaultdict
from contextlib import contextmanager

# A name is read word by word (split at punctuation and camelCase); the first
# kind with a word starting with one of its prefixes is the kind.
KINDS = (
    ("checkpoint and logging", ("save", "ckpt", "checkpoint", "dump")),
    ("weight sync", ("sync", "reshard", "broadcast")),
    ("startup", ("startup", "compile", "init")),
    ("generation", ("gen", "rollout", "sampl", "decode", "prefill")),
    ("environment", ("env", "tool", "exec", "reward", "verif", "sandbox")),
    ("training compute", ("backward", "update", "optim", "actor", "forward", "logprob", "prob", "ref", "score", "adv")),
)
SKIP = r"total|including|^step$|^generate_sequences$|^reshard$"  # rows other rows already count
_NUMBER = r"-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?"


@contextmanager
def phase(name, kind, log_path, sync=None, **counts):
    """Time one phase of ``kind`` (one of KINDS) into ``log_path``; set counts (``generated_tokens``) on the yielded dict."""
    if sync:
        sync()
    start = time.time()
    try:
        yield counts
    finally:
        if sync:
            sync()
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"phase": name, "kind": kind, "start": start, "seconds": time.time() - start, **counts}) + "\n")


def kind_of(name):
    """The kind ``name`` reads as, or the name itself."""
    words = [word.lower() for word in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)]
    return next((kind for kind, prefixes in KINDS if any(word.startswith(prefixes) for word in words)), name)


def read(path, prefix):
    pair = re.compile(re.escape(prefix) + r'([\w.-]+)"?\s*[:=]\s*(' + _NUMBER + ")")
    records = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line) if line.lstrip().startswith("{") else None
            except ValueError:
                row = None
            if isinstance(row, dict) and "phase" in row and isinstance(row.get("seconds"), (int, float)):
                row.setdefault("start", row.get("start_monotonic"))
                records.append(row)
            else:
                records += [{"phase": name, "seconds": float(value)} for name, value in pair.findall(line)]
    return records


def profile(path, *, prefix, skip):
    pattern = re.compile(skip) if skip else None
    rows = read(path, prefix)
    left_out = {i for i, row in enumerate(rows) if pattern and pattern.search(str(row["phase"]))}
    timed = sorted((row["start"], -row["seconds"], i) for i, row in enumerate(rows)
                   if i not in left_out and isinstance(row.get("start"), (int, float)))
    reach = float("-inf")
    for start, negative_seconds, i in timed:  # of two rows starting together, the longer comes first
        if start - negative_seconds <= reach:
            left_out.add(i)  # inside an earlier row
        reach = max(reach, start - negative_seconds)
    seconds, kinds, skipped, spans = defaultdict(float), {}, set(), []
    tokens = gen_seconds = 0.0
    for i, row in enumerate(rows):
        name = str(row["phase"])
        kind = (str(row["kind"]), False) if row.get("kind") else (kind_of(name), True)
        if isinstance(row.get("start"), (int, float)) and kind[0] != "startup":
            spans.append((row["start"], row["start"] + row["seconds"]))
        if i in left_out:
            skipped.add(name)
            continue
        seconds[name] += float(row["seconds"])
        kinds.setdefault(name, kind)
        if row.get("generated_tokens"):
            tokens += float(row["generated_tokens"])
            gen_seconds += float(row["seconds"])
    by_kind = defaultdict(float)
    for name, value in seconds.items():
        if kinds[name][0] != "startup":  # paid once, not per step
            by_kind[kinds[name][0]] += value
    total = sum(by_kind.values())
    return {
        "rows": sorted(seconds.items(), key=lambda item: -item[1]),
        "kinds": kinds,
        "total": total,
        "shares": sorted(((kind, value / total) for kind, value in by_kind.items()), key=lambda item: -item[1]) if total else [],
        "tokens_per_second": round(tokens / gen_seconds, 1) if gen_seconds else None,
        "skipped": sorted(skipped),
        "wall": max(end for _, end in spans) - min(start for start, _ in spans) if spans else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("log")
    parser.add_argument("--engine", default="unstated", help="what generated the rollouts: native, or the engine's name")
    parser.add_argument("--prefix", default="timing_s/", help="framework log key prefix (verl: timing_s/)")
    parser.add_argument("--skip", default=SKIP, help="regex of phases other phases already count; '' for none")
    parser.add_argument("--json", action="store_true", help="print the largest kind as one object")
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv)
    try:
        result = profile(args.log, prefix=args.prefix, skip=args.skip)
    except OSError as error:
        print(f"{args.log}: {error.strerror or error}")
        return 0
    if not result["shares"]:
        print(f"{args.log}: no timed phases beyond startup (JSON rows with phase and seconds, "
              f"or '{args.prefix}<phase>:<seconds>' pairs); log a few real steps")
        return 0
    tokens_per_second = result["tokens_per_second"]
    if args.json:
        kind, share = result["shares"][0]
        print(json.dumps({
            "command": " ".join(map(shlex.quote, [sys.executable, __file__, *argv])),
            "source": args.log,
            "engine": args.engine,
            "dominant": kind,
            "share": round(share, 3),
            **({"tokens_per_second": tokens_per_second} if tokens_per_second else {}),
            **({"kind_from_name": True} if any(k == kind and named for k, named in result["kinds"].values()) else {}),
        }, ensure_ascii=False))
        return 0
    for name, value in result["rows"]:
        kind, named = result["kinds"][name]
        share = "once" if kind == "startup" else f"{value / result['total']:.1%}"
        print(f"{name:<40} {kind + ('*' if named else ''):<24} {value:9.1f}s {share:>6}")
    if any(named for _, named in result["kinds"].values()):
        print("* kind read from the phase name; phase() records it")
    if result["skipped"]:
        print(f"(not summed, their time is in other rows: {', '.join(result['skipped'])}; --skip sets the names)")
    if result["wall"]:
        print(f"phases cover {result['total'] / result['wall']:.0%} of {result['wall']:.0f}s wall")
    if tokens_per_second:
        print(f"generation: {tokens_per_second} tokens/s (engine: {args.engine})")
    for kind, share in result["shares"]:
        bound = f"the step is at most {1 / (1 - share):.1f}x faster if it took no time" if share < 1 else "the whole step"
        print(f"{kind}: {share:.1%} of the step; {bound}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
