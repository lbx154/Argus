"""Opt-in, tool-free live comparison of integrated versus specialist-only prompts.

Run from the checkout with ARGUS_AB_API_KEY set. This sends only the four public
fixtures below; it does not read manuscripts, change runtime settings, or start
an Argus mission. The Responses-compatible endpoint is supplied explicitly.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import urllib.request
from pathlib import Path

from argus.roles.prompts import ChecklistMode, resolve_role_prompt
from argus.roles.prompts.reviewer import (
    COLD_READ,
    SCIENCE_LOSS_CHECK,
    evaluate_request,
)
from argus.verticals._base import load_vertical_contract

CASES = (
    (
        "cold-clear", COLD_READ, "pass",
        "PDF page 1: A controlled microbenchmark measures the same CPU operation. "
        "Baseline median latency is 20 ms; candidate median latency is 10 ms, "
        "so the candidate takes half as long (2x speedup). Page 2: Both methods "
        "use the same inputs, CPU, thread count, and 100 trials. The conclusion "
        "is limited to this microbenchmark, not end-to-end application speed.",
    ),
    (
        "cold-contradiction", COLD_READ, "fail",
        "PDF page 1: Our candidate is 2x faster than the baseline. Page 2, Table 1: "
        "Baseline latency 20 ms; candidate latency 20 ms. These are the exact "
        "same measurement, workload, and units; no alternative timing is reported.",
    ),
    (
        "loss-relocation", SCIENCE_LOSS_CHECK, "pass",
        "Before: The candidate reduces latency from 20 ms to 10 ms on workload A; "
        "on workload B it increases latency from 20 ms to 24 ms. "
        "After: Main text reports the workload A improvement and explicitly refers "
        "to Appendix Table 2 for workload B. Appendix Table 2 states baseline "
        "20 ms, candidate 24 ms, and explicitly describes the slowdown on B.",
    ),
    (
        "loss-omission", SCIENCE_LOSS_CHECK, "fail",
        "Before: The candidate reduces latency from 20 ms to 10 ms on workload A; "
        "on workload B it increases latency from 20 ms to 24 ms. "
        "After: The candidate reduces latency from 20 ms to 10 ms on workload A. "
        "This is the entire after snapshot: workload B and its slowdown are absent.",
    ),
)

FIXTURE_RULE = (
    "\n\nControlled acceptance fixture: the complete relevant PDF text or before/after "
    "extract follows. Judge only whether it contains a substantive contradiction "
    "or an edit-induced loss; do not demand the rest of a full paper for this "
    "miniature fixture. No tools or other files are available. Do not edit files. "
    'Return only JSON: {"verdict":"pass" or "fail","reason":"concrete explanation"}.'
    "\n\n"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="Full /v1/responses URL")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ["ARGUS_AB_API_KEY"]
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="argus-specialist-ab-") as root:
        contract = load_vertical_contract("research", project_root=Path(root))
        for index, (name, operation, expected, fixture) in enumerate(CASES):
            fragment = contract.prompt_fragment(
                role="reviewer", operation=operation, stage="review",
                scope="", project_root=Path(root),
            )
            prompts = {
                "baseline": contract.banner("reviewer").strip() + "\n\n" + fragment.strip(),
                "candidate": resolve_role_prompt(evaluate_request(
                    root, vertical="research", stage="review",
                    checklist_mode=ChecklistMode.NONE, operation=operation,
                )).role_banner,
            }
            for arm in (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")):
                prompt = prompts[arm] + FIXTURE_RULE + fixture
                request = urllib.request.Request(
                    args.endpoint,
                    data=json.dumps({
                        "model": args.model, "input": prompt, "stream": False,
                        "reasoning": {"effort": "low"}, "max_output_tokens": 2048,
                    }).encode(),
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                )
                started = time.monotonic()
                with urllib.request.urlopen(request, timeout=120) as response:
                    body = json.load(response)
                if body.get("status") != "completed":
                    raise RuntimeError(f"{name}/{arm}: response did not complete")
                text = "".join(
                    item["text"]
                    for message in body["output"]
                    for item in message.get("content", [])
                    if item.get("type") == "output_text"
                )
                assessment = json.loads(text)
                results.append({
                    "case": name, "operation": operation, "arm": arm,
                    "expected": expected, "assessment": assessment,
                    "passed": assessment.get("verdict") == expected,
                    "model": body.get("model"), "reasoning_effort": "low",
                    "prompt_chars": len(prompt), "usage": body["usage"],
                    "seconds": round(time.monotonic() - started, 3),
                })
                args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
                print(json.dumps({key: results[-1][key] for key in ("case", "arm", "passed", "usage")}), flush=True)
    if not all(row["passed"] for row in results):
        raise SystemExit("At least one acceptance fixture failed; see the saved results.")


if __name__ == "__main__":
    main()
