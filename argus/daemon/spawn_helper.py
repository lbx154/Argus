"""Single-threaded exec boundary for detached daemon spawning."""

from __future__ import annotations

import json
import sys

from .config import config_from_payload
from .life_worker import spawn_detached_daemon


def main() -> int:
    payload = json.load(sys.stdin)
    training_launch = payload.pop("_training_launch", None)
    config = config_from_payload(payload)
    from ..trial.training_runtime import arm_daemon_launch

    arm_daemon_launch(training_launch, config)
    training_launch = None
    # NOT quiet, despite this being a non-interactive helper. ``quiet`` means
    # "there is no operator reading this stream" — but the caller runs us with
    # ``capture_output=True`` and relays our stderr, so our stream IS how the
    # operator hears about an admission failure. With ``quiet=True`` every
    # refusal in ``spawn_detached_process`` (workspace busy, workspace lease
    # held, active-daemon cap, spawn lock) took its ``if not quiet`` branch to
    # nowhere and exited 2/3 having written nothing at all; the caller then
    # found empty stderr and reported the bare fallback string, "clean daemon
    # launcher exited with code 3".
    #
    # Observed cost: a testbed re-run in a directory whose previous daemon was
    # still alive silently failed to start any executor. The lease record named
    # the owning pid and session and the message body listed how to stop it —
    # all of it was discarded here, and the mission sat queued with nothing to
    # run it. Success chatter goes to stdout, which the caller ignores on
    # return code 0, so unmuting costs nothing on the happy path.
    return spawn_detached_daemon(config, quiet=False)


if __name__ == "__main__":
    raise SystemExit(main())
