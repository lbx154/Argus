"""Compare mypy diagnostics against a Git revision using the same environment."""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

_ERROR = re.compile(r"^(.+?):\d+(?::\d+)?: error: (.+)$")


def diagnostic_counts(output: str) -> Counter[str]:
    """Keep file/message/code and multiplicity; moving lines is not new debt."""
    counts: Counter[str] = Counter()
    for line in output.splitlines():
        if match := _ERROR.match(line):
            message = match[2]
            if message.endswith("[no-redef]"):
                message = re.sub(r"\bon line \d+\b", "on line <location>", message)
            counts[f"{match[1]}: {message}"] += 1
    return counts


def _run_mypy(root: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-pretty", "--no-color-output",
         "--show-error-codes", "--no-error-summary"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    output = result.stdout + result.stderr
    if result.returncode not in {0, 1} or (result.returncode == 1 and not diagnostic_counts(output)):
        raise RuntimeError(f"mypy could not complete in {root}:\n{output}")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="HEAD^", help="Git revision to compare against")
    parser.add_argument("--output-dir", type=Path, help="Keep baseline/current mypy output")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    base_ref = args.base if args.base and set(args.base) != {"0"} else "HEAD^"
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{base_ref}^{{commit}}"], cwd=root, text=True,
        ).strip()
        archive = subprocess.check_output(
            ["git", "archive", revision, "--", "argus_skill", "pyproject.toml"], cwd=root,
        )
        with tempfile.TemporaryDirectory(prefix="argus-typecheck-base-") as directory:
            baseline = Path(directory)
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                bundle.extractall(baseline, filter="data")
            before = _run_mypy(baseline)
        after = _run_mypy(root)
    except (OSError, subprocess.CalledProcessError, tarfile.TarError, RuntimeError) as exc:
        print(f"Typecheck comparison failed: {exc}", file=sys.stderr)
        return 2

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "baseline.log").write_text(before, encoding="utf-8")
        (args.output_dir / "current.log").write_text(after, encoding="utf-8")
        (args.output_dir / "base-revision.txt").write_text(revision + "\n", encoding="utf-8")
    old, new = diagnostic_counts(before), diagnostic_counts(after)
    added = new - old
    print(f"mypy baseline {revision[:12]}: {old.total()} diagnostics; current: {new.total()}; "
          f"introduced: {added.total()}; removed: {(old - new).total()}")
    for diagnostic, count in sorted(added.items()):
        print(f"NEW ({count}): {diagnostic}")
    return 1 if added else 0


if __name__ == "__main__":
    raise SystemExit(main())
