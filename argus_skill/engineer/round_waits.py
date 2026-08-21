"""Round-loop phase: agent-driven background/external-work cadence waits.

The Engineer requests a wait through an exact JSON object on the final non-empty
response line. The harness validates the registry id, then monitors that owner
until its state changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core import process_stop
from .external_work import inspect_external_work, parse_external_wait_request
from .round_signals import _pause_decision_clock
from .round_state import RoundControl, RoundLoopState, control_continue_loop, control_proceed

if TYPE_CHECKING:
    from .runner import SupervisedConfig


class RoundWaitsMixin:
    """Mixin providing ``SupervisedEngineer``'s agent-driven wait phase."""

    def _handle_agent_driven_wait(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        raw_engineer_message: str,
        workdir: Path,
        state: RoundLoopState,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        wait_request = parse_external_wait_request(raw_engineer_message)
        wait_kind, external_work_id = wait_request or ("", "")
        external_work = (
            inspect_external_work(workdir, external_work_id) if external_work_id else None
        )
        source_matches = (
            external_work is not None
            and (
                (wait_kind == "external_work" and external_work.source != "subagent")
                or (
                    wait_kind == "subagent"
                    and supervised_config.background_subagent_advisory
                    and external_work.source == "subagent"
                )
            )
        )
        if source_matches and external_work is not None and external_work.waitable:
            from . import runner as _runner_module

            waited_s = 0.0
            while True:
                wait_reason, cadence_waited_s = _runner_module._run_external_work_wait(
                    workdir=workdir,
                    work_id=external_work.work_id,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    on_event=on_event,
                )
                waited_s += cadence_waited_s
                if wait_reason != "cadence_elapsed":
                    break
                if process_stop.stop_requested():
                    break
            state.last_decision_progress_at = _pause_decision_clock(
                state.last_decision_progress_at,
                waited_s,
            )
            return control_continue_loop()
        return control_proceed()
