"""Round-loop phase: agent-driven background/external-work cadence waits.

The Engineer requests a wait through its mission-scoped control file. The
harness validates the exact registry id, then sleeps on that owner's cadence.
Legacy response sentinels remain a read-only adapter for already-running
older agents.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .background_subagents import find_waitable_subagent, parse_wait_sentinel
from .external_work import inspect_external_work, parse_external_wait_sentinel
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
        if supervised_config.background_subagent_advisory:
            wait_task_id = parse_wait_sentinel(raw_engineer_message)
            if wait_task_id and find_waitable_subagent(workdir, wait_task_id) is not None:
                # Call through the ``runner`` module attribute (not a static
                # imported name) so tests that monkeypatch
                # ``runner._run_background_wait`` keep observing it, exactly
                # as when this call lived directly inside ``runner.py``.
                from . import runner as _runner_module

                _, waited_s = _runner_module._run_background_wait(
                    workdir=workdir,
                    task_id=wait_task_id,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    on_event=on_event,
                )
                state.last_decision_progress_at = _pause_decision_clock(
                    state.last_decision_progress_at,
                    waited_s,
                )
                # A deliberate yield is neither progress nor a stall. Preserve
                # the pre-wait streak and re-assess fresh next round.
                return control_continue_loop()

        external_work_id = parse_external_wait_sentinel(raw_engineer_message)
        external_work = (
            inspect_external_work(workdir, external_work_id) if external_work_id else None
        )
        if external_work is not None and external_work.waitable:
            # See the background-wait call above: route through the
            # ``runner`` module attribute so monkeypatching
            # ``runner._run_external_work_wait`` keeps taking effect.
            from . import runner as _runner_module

            _, waited_s = _runner_module._run_external_work_wait(
                workdir=workdir,
                work_id=external_work.work_id,
                round_index=round_index,
                round_max=supervised_config.max_rounds,
                on_event=on_event,
            )
            state.last_decision_progress_at = _pause_decision_clock(
                state.last_decision_progress_at,
                waited_s,
            )
            return control_continue_loop()
        return control_proceed()
