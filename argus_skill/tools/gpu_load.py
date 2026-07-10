#!/usr/bin/env python
"""GPU keep-alive loader (standalone, torch-only).

On managed/cloud boxes the scheduler reclaims GPUs that sit idle. While the
Argus agent is only calling the model API, drafting LaTeX, or otherwise doing
no real GPU work, the cards can go idle and be reclaimed -- losing in-progress
training/inference state. This loader holds the requested GPUs at a low,
best-effort duty cycle so each card keeps a live process plus a small VRAM
footprint and periodic utilization, and is therefore not considered idle.

It is the operator "keep-alive" coordinated by
``argus_skill.tools.gpu_lease``: real GPU work runs via
``python -m argus_skill.tools.gpu_lease run -- <cmd>``, which stops this loader
(freeing the cards), runs the job, then restarts this loader afterwards.

Deliberately standalone: it imports ONLY the standard library and ``torch`` (no
``argus_skill`` imports) so it can run under whichever interpreter actually has
torch + CUDA, independent of where the Argus framework itself is installed. It
is kept Python 3.10 compatible for the same reason.

``--util`` is a best-effort activity target, not a guaranteed utilization
percentage. If a scheduler still reclaims the cards, raise ``--util``, lower
``--interval``, or raise ``--mem``.

Usage::

    python gpu_load.py                 # all visible GPUs, low duty cycle
    python gpu_load.py --gpus 0,1,2,3 --mem 10 --util 20
    python gpu_load.py --duration 600  # auto-exit after 600s (default: forever)
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time

_STOP = False


def _handle_stop(_signum, _frame):  # noqa: ANN001 - signal handler signature
    global _STOP
    _STOP = True


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in small slices so a SIGTERM stop flag is observed promptly."""
    deadline = time.time() + seconds
    while not _STOP and time.time() < deadline:
        time.sleep(min(0.1, max(0.0, deadline - time.time())))


def _parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="gpu_load.py", description="GPU keep-alive loader (anti-reclaim)")
    ap.add_argument("--util", type=float, default=20.0,
                    help="best-effort GPU utilization target, percent (0-100)")
    ap.add_argument("--mem", type=float, default=10.0,
                    help="VRAM to hold per GPU, percent of total (0-90)")
    ap.add_argument("--gpus", type=str, default="",
                    help="comma-separated PHYSICAL GPU ids to hold; default all")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="seconds per busy/idle cycle")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="run for N seconds then exit; 0 = run forever")
    # Inert marker so gpu_lease can match THIS loader precisely via its config
    # `match` token without relying on the (broad) basename. Accepted and
    # ignored.
    ap.add_argument("--keepalive-token", type=str, default="",
                    help="opaque marker used by gpu_lease to find this process")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    # Pin the requested physical devices BEFORE importing torch, so torch's
    # visible ordinals 0..N-1 map exactly to the requested physical ids
    # regardless of any inherited CUDA_VISIBLE_DEVICES.
    gpus = args.gpus.strip()
    if gpus:
        os.environ["CUDA_VISIBLE_DEVICES"] = gpus

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    try:
        import torch
    except Exception as exc:  # pragma: no cover - depends on host env
        print("[gpu_load] torch is required but unavailable: %s" % exc,
              file=sys.stderr, flush=True)
        return 1

    if not torch.cuda.is_available():
        print("[gpu_load] CUDA is not available; nothing to hold",
              file=sys.stderr, flush=True)
        return 1

    n = torch.cuda.device_count()
    devices = list(range(n))
    if not devices:
        print("[gpu_load] no visible CUDA devices", file=sys.stderr, flush=True)
        return 1

    util = max(0.0, min(100.0, args.util)) / 100.0
    mem_frac = max(0.0, min(0.90, args.mem / 100.0))
    interval = max(0.25, args.interval)

    holds = []  # persistent VRAM footprint, kept referenced so it is not freed
    work = []   # small operands for periodic matmul
    for d in devices:
        torch.cuda.set_device(d)
        total = torch.cuda.get_device_properties(d).total_memory
        hold_bytes = int(total * mem_frac)
        if hold_bytes > 0:
            numel = max(1, hold_bytes // 4)  # float32 = 4 bytes
            try:
                holds.append(
                    torch.empty(numel, dtype=torch.float32, device="cuda:%d" % d))
            except Exception as exc:  # pragma: no cover - OOM/host specific
                print("[gpu_load] cuda:%d hold alloc failed: %s" % (d, exc),
                      file=sys.stderr, flush=True)
        # Small operands keep work bounded so the SIGTERM stop flag is seen
        # promptly between iterations.
        a = torch.randn(1024, 1024, device="cuda:%d" % d)
        b = torch.randn(1024, 1024, device="cuda:%d" % d)
        work.append((d, a, b))

    print("[gpu_load] holding physical GPUs=%s mem=%.0f%% util=%.0f%% "
          "interval=%.2fs pid=%d" % (
              gpus or ",".join(str(d) for d in devices),
              mem_frac * 100.0, util * 100.0, interval, os.getpid()),
          flush=True)

    busy = interval * util
    idle = interval - busy
    start = time.time()
    while not _STOP:
        if args.duration > 0 and (time.time() - start) >= args.duration:
            break
        t0 = time.time()
        while busy > 0 and not _STOP and (time.time() - t0) < busy:
            for d, a, b in work:
                if _STOP:
                    break
                torch.cuda.set_device(d)
                c = a @ b
                float(c[0, 0].item())  # tiny sync to realize the kernel
        if idle > 0 and not _STOP:
            _interruptible_sleep(idle)

    print("[gpu_load] stopping; releasing GPUs", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
