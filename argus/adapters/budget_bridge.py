"""One local budget session for a TypeScript Pi invocation over bounded JSONL.

Only this Python process writes usage/reservations. No provider commands, prompts,
arbitrary paths, budget overrides or dynamic function names cross this protocol.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..core.contract_resources import contract_schema_path

CONTRACT = json.loads(contract_schema_path("budget_bridge_protocol.json").read_text())
MAX_COUNT = 2**53 - 1
FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_output_tokens")


def text(value: Any, *, maximum: int = 255, optional: bool = False) -> str:
    if optional and value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum or (not optional and not value.strip()):
        raise ValueError("invalid text field")
    if any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("invalid text field")
    return value


def number(value: Any, *, integer: bool = False) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("usage must be finite and nonnegative")
    if integer and (type(value) is not int or value > MAX_COUNT):
        raise ValueError("usage count must be a safe integer")
    return value


def accounting(value: Any) -> tuple[Any, dict]:
    from ..core.token_usage import TokenUsage

    if not isinstance(value, dict) or not isinstance(value.get("pricing"), dict):
        raise ValueError("invalid accounting receipt")
    price = value["pricing"]
    if price.get("status") not in {"priced", "partial", "unpriced"}:
        raise ValueError("invalid pricing status")
    cost = price.get("cost_usd")
    if cost is not None:
        number(cost)
    elif price["status"] == "priced":
        raise ValueError("priced usage requires a cost")
    text(price.get("tier"), maximum=100)
    raw = value.get("usage")
    if raw is None:
        if price["status"] == "priced":
            raise ValueError("missing accounting cannot be fully priced")
        return TokenUsage(), price
    if not isinstance(raw, dict):
        raise ValueError("invalid token usage")
    fields = {}
    for field in FIELDS:
        fields[field] = number(raw.get(field), integer=True)
        flag = field + "_present"
        if type(raw.get(flag)) is not bool:
            raise ValueError("token presence must be boolean")
        if not raw[flag] and fields[field] != 0:
            raise ValueError("absent token counts must be zero")
        fields[flag] = raw[flag]
    if sum(fields[key] for key in ("input_tokens", "output_tokens", "reasoning_output_tokens")) > MAX_COUNT:
        raise ValueError("token total exceeds safe integer range")
    return TokenUsage(**fields, source="typescript_pi"), price


class BudgetSession:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.phase = "new"
        self.reservation = None
        self.pending = None
        self.project: Path | None = None
        self.call_id = ""
        self.started_at = time.time()
        self.last_cost = 0.0
        self.last_tokens = 0
        self.day = time.strftime("%Y-%m-%d")
        self.day_cost = 0.0
        self.day_tokens = 0

    def dispatch(self, method: str, params: dict) -> dict:
        from ..core.cost_control import PendingBudgetCall, cached_token_weight, reserve_call_budget
        from ..core.usage import UsageLedger, build_usage_record

        if method == "reserve":
            if self.phase != "new" or set(params) != {"sid", "model", "run_label", "mission_id"}:
                raise ValueError("invalid reserve operation")
            sid = text(params["sid"])
            if sid in {".", ".."} or "/" in sid or "\\" in sid:
                raise ValueError("invalid project identifier")
            projects = (self.root / "projects").resolve()
            project = (projects / sid).resolve()
            if project.parent != projects or not project.is_dir():
                raise ValueError("project must be an existing direct child")
            self.project = project
            self.call_id = uuid.uuid4().hex
            model = text(params["model"])
            label = text(params["run_label"])
            mission = text(params["mission_id"], optional=True) or None
            self.reservation, reason = reserve_call_budget(
                call_id=self.call_id, project_root=project, mission_id=mission,
                provider="pi", model=model, run_label=label, global_root=self.root,
            )
            self.phase = "reserved" if self.reservation else "closed"
            return {"admitted": self.reservation is not None, "call_id": self.call_id,
                    "reason": reason, "global_root": str(self.root),
                    "source_root": str(Path(__file__).resolve().parents[2])}
        if method == "release":
            if params or self.phase != "reserved":
                raise ValueError("only an unstarted reservation can be released")
            self.reservation.release(reason="typescript runner did not start")
            self.phase = "closed"
            return {"released": True}
        if method == "start":
            if params or self.phase != "reserved":
                raise ValueError("invalid start operation")
            self.pending = PendingBudgetCall(self.reservation)
            self.phase = "running"
            return {"started": True}
        if method not in {"observe", "settle"} or self.phase != "running":
            raise ValueError("invalid budget lifecycle operation")
        allowed = {"accounting"} if method == "observe" else {"accounting", "completed", "thread_id"}
        if set(params) != allowed:
            raise ValueError("unexpected budget fields")
        usage, price = accounting(params["accounting"])
        cost = max(self.last_cost, price["cost_usd"] or 0)
        tokens = usage.input_tokens - round(min(usage.cached_input_tokens, usage.input_tokens)
            * (1.0 - cached_token_weight())) + usage.output_tokens + usage.reasoning_output_tokens
        tokens = max(self.last_tokens, tokens)
        self.pending.observe(cost, tokens)
        day = time.strftime("%Y-%m-%d")
        if day != self.day:
            self.day, self.day_cost, self.day_tokens = day, 0.0, 0
        self.day_cost += cost - self.last_cost
        self.day_tokens += tokens - self.last_tokens
        self.last_cost, self.last_tokens = cost, tokens
        if method == "observe":
            reason = self.reservation.observe_cost(self.day_cost, tokens=self.day_tokens)
            return {"stop_reason": reason}
        if type(params["completed"]) is not bool:
            raise ValueError("completed must be boolean")
        thread = text(params["thread_id"], maximum=1024, optional=True) or None
        complete = params["completed"] and price["status"] == "priced"
        status = "priced" if complete else "partial"
        # The TS quote prices each provider turn. Repricing the whole token
        # total would select the wrong long-context tier, so keep its own basis.
        record = build_usage_record(
            call_id=self.call_id, project_root=self.project,
            mission_id=self.reservation.mission_id, provider="pi", model=self.reservation.model,
            run_label=self.reservation.run_label, started_at=self.started_at, completed_at=time.time(),
            status="completed" if params["completed"] else "error", token_usage=usage,
            thread_id=thread, provider_cost_usd=cost,
        )
        record = replace(record, cost_usd=cost if cost > 0 or complete else None,
                         pricing_status=status, pricing_tier=price["tier"], cost_basis="typescript_pi",
                         error="" if complete else "TypeScript Pi usage is incomplete; reconciliation required")
        ledger = UsageLedger(self.project, migrate_legacy=False)
        if not ledger.append(record):
            raise RuntimeError("duplicate settlement call id")
        self.reservation.settle(record)
        self.pending.close(settled=complete)
        self.phase = "closed"
        return {"settlement": "settled" if complete else "unresolved", "call_id": self.call_id}

    def close(self) -> None:
        try:
            if self.reservation and self.phase == "running":
                self.reservation.settle_unknown(reason="TypeScript budget session closed without settlement")
            elif self.reservation and self.phase == "reserved":
                self.reservation.release(reason="TypeScript budget session closed before start")
        finally:
            if self.pending:
                self.pending.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--global-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.global_root.expanduser().resolve()
    os.environ["ARGUS_SKILL_HOME"] = str(root)
    os.environ.pop("ARGUS_DESKTOP_LAUNCH_NONCE", None)
    session = BudgetSession(root)
    output = sys.stdout
    sequence = 0
    try:
        while raw := sys.stdin.buffer.readline(CONTRACT["max_line_bytes"] + 1):
            response = {"protocol": CONTRACT["protocol"], "version": CONTRACT["version"], "id": None}
            try:
                if len(raw) > CONTRACT["max_line_bytes"] or not raw.endswith(b"\n"):
                    raise ValueError("invalid budget frame length")
                query = json.loads(raw)
                if not isinstance(query, dict) or set(query) != {"protocol", "version", "id", "method", "params"}:
                    raise ValueError("invalid budget envelope")
                if (query["protocol"] != CONTRACT["protocol"] or type(query["version"]) is not int
                        or query["version"] != CONTRACT["version"] or type(query["id"]) is not int
                        or query["id"] != sequence + 1
                        or not isinstance(query["params"], dict) or query["method"] not in CONTRACT["methods"]):
                    raise ValueError("incompatible budget envelope")
                sequence = query["id"]
                response["id"] = query["id"]
                with contextlib.redirect_stdout(sys.stderr):
                    result = session.dispatch(query["method"], query["params"])
                response.update(ok=True, result=result)
            except Exception as exc:  # one invalid mutation ends the session conservatively
                print(f"budget bridge failed: {type(exc).__name__}", file=sys.stderr)
                response.update(ok=False, error="budget operation failed")
            output.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
            output.flush()
            if not response["ok"]:
                break
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
