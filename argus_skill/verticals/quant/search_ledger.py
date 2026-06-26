"""Append-oriented search ledger for factor-mining trials.

Every backtest the agent runs — survivor *or* discard, success *or* failure —
is recorded here at the execution boundary (see :mod:`.backtest`). The ledger is
the raw evidence the L2 reviewer reads to judge **search breadth** and
**multiple-testing / cherry-picking risk**: a factor with IC 0.05 found after
one hypothesis is very different from the same IC found after 3,000 silent
trials.

Per the project's trust model this is **evidence, not a gate**. The ledger does
not decide pass/fail; the reviewer interprets it. It is therefore deliberately
dumb: it appends rows and never edits or judges them.

Tamper-evidence: rows are written as JSONL and chained by hash — each row
carries the hash of the previous row plus a hash of its own payload, so a
deleted or edited middle row breaks the chain and is detectable on
:func:`verify_chain`. This makes "I logged every trial" auditable without
needing immutable storage. It is lightweight tamper-*evidence*, not
tamper-*proofing*.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_GENESIS = "0" * 64


def _canonical(payload: Mapping[str, Any]) -> str:
    """Deterministic JSON for hashing (sorted keys, no whitespace drift)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


@dataclass(frozen=True)
class LedgerRow:
    """One recorded trial. ``payload`` is the trial record (a backtest result or
    a failure); the rest is chain bookkeeping.

    ``index`` is the 0-based position; ``prev_hash`` chains to the prior row's
    ``row_hash``; ``payload_hash`` fixes the content; ``row_hash`` =
    H(index, prev_hash, payload_hash, recorded_at).
    """

    index: int
    recorded_at: float
    payload: Mapping[str, Any]
    payload_hash: str
    prev_hash: str
    row_hash: str

    def to_json(self) -> str:
        return _canonical(asdict(self))


@dataclass
class SearchLedger:
    """An append-oriented, hash-chained JSONL ledger of factor-mining trials.

    Not thread-safe; one writer (the run-stage executor) is assumed. Reads are
    fail-open: a missing ledger is an empty ledger.
    """

    path: Path
    _last_hash: str = field(default=_GENESIS, init=False)
    _count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        # Recover chain state from an existing ledger so re-opening appends
        # correctly rather than restarting the chain.
        last = None
        for last in self._iter_rows():
            pass
        if last is not None:
            self._last_hash = last.row_hash
            self._count = last.index + 1

    # -- writing ----------------------------------------------------------

    def append(self, payload: Mapping[str, Any]) -> LedgerRow:
        """Append one trial record and return the written row.

        The payload is recorded verbatim (plus chain metadata). Callers should
        include enough to reproduce/audit the trial: factor id(s), combination
        spec, weighting, window, params, metrics, oos/is flag, run_id, status.
        """
        index = self._count
        recorded_at = time.time()
        payload_hash = _hash(_canonical(payload))
        row_hash = _hash(str(index), self._last_hash, payload_hash, repr(recorded_at))
        row = LedgerRow(
            index=index,
            recorded_at=recorded_at,
            payload=dict(payload),
            payload_hash=payload_hash,
            prev_hash=self._last_hash,
            row_hash=row_hash,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(row.to_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._last_hash = row_hash
        self._count = index + 1
        return row

    # -- reading ----------------------------------------------------------

    def _iter_rows(self) -> Iterator[LedgerRow]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                yield LedgerRow(
                    index=int(data["index"]),
                    recorded_at=float(data["recorded_at"]),
                    payload=data["payload"],
                    payload_hash=str(data["payload_hash"]),
                    prev_hash=str(data["prev_hash"]),
                    row_hash=str(data["row_hash"]),
                )

    def rows(self) -> list[LedgerRow]:
        return list(self._iter_rows())

    def __len__(self) -> int:
        return self._count

    # -- audit ------------------------------------------------------------

    def verify_chain(self) -> bool:
        """Recompute the hash chain end-to-end.

        Returns ``True`` iff every row's index is sequential, its payload hash
        matches its content, and its ``prev_hash`` links to the previous row's
        ``row_hash`` (genesis for the first). A deleted/edited/reordered row
        breaks this and returns ``False``.
        """
        expected_prev = _GENESIS
        expected_index = 0
        for row in self._iter_rows():
            if row.index != expected_index:
                return False
            if row.prev_hash != expected_prev:
                return False
            if _hash(_canonical(row.payload)) != row.payload_hash:
                return False
            recomputed = _hash(
                str(row.index), row.prev_hash, row.payload_hash, repr(row.recorded_at)
            )
            if recomputed != row.row_hash:
                return False
            expected_prev = row.row_hash
            expected_index += 1
        return True
