"""Execute-lifecycle mixin: ``SkillLoopExecuteMixin`` — the
``_SkillLoopRunner.execute()`` orchestrator and its lifecycle-phase helper
methods (config build, mission-context prep, loop
invocation, outcome-field extraction, stage-transition decision, outcome
assembly).
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..core.knobs import learning_enabled
from ..core.ports import EventSink
from ..core.runner_errors import is_execution_host_startup_error
from ..engineer.round_config import HOST_PLACEHOLDER_REVIEW_SOURCES
from ..engineer.runner import should_clear_thread_id_after_outcome
from ._env import env_flag as _env_flag
from ._runtime_backends import _Outcome
from ._runtime_helpers import (
    _checkpoint_path_for,
    _ExecuteState,
    _project_state_dir_for,
    _should_run_stage_transition,
)

log = logging.getLogger(__name__)


def _execution_host_blocked_outcome(outcome: object) -> bool:
    return (
        getattr(outcome, "park_reason", "") == "infra"
        and is_execution_host_startup_error(getattr(outcome, "reason", ""))
    )


def _final_round_unjudged(outcome: object) -> bool:
    """Whether the last round record is a host placeholder, not a judgment.

    The Reviewer leg rendered no verdict (it raised or failed until the
    mission stopped), or the Engineer turn ended in a way nobody judged.
    Neither is Reviewer evidence: the Manager's stage writer must not decide
    on a ``blocked`` the Reviewer never said, and the journal must not credit
    it to the Reviewer.
    """
    rounds = getattr(outcome, "rounds", None) or []
    review = getattr(rounds[-1], "review", None) if rounds else None
    return getattr(review, "review_source", "") in HOST_PLACEHOLDER_REVIEW_SOURCES


def _engineer_guidance(
    state_root: Path | None,
    workdir: Path,
    manager: object | None = None,
    *,
    receiver=None,
    delivery_messages: list[str] | None = None,
    mission_id: str = "",
) -> list[str]:
    """Project the typed operator context after persisting fresh inbox input."""
    if state_root is None:
        return []
    from ..core.file_lock import FileLockCancelled, bounded_file_lock_wait
    from ..core.operator_context import (
        OperatorContextUnavailable,
        build_operator_context_block,
    )
    from ..core.run_gateway import current_run_interrupt_reason
    from ._inbox_delivery import DurableInboxReceiver, operator_live_turn

    owned_receiver = receiver is None
    receiver = receiver or DurableInboxReceiver(state_root, consumer="engineer", project_root=workdir)

    def cancelled() -> bool:
        return bool(current_run_interrupt_reason())

    try:
        with bounded_file_lock_wait(timeout_seconds=float("inf"), cancelled=cancelled):
            transient = receiver.receive(mission_id=mission_id, cancelled=cancelled)
            if delivery_messages is not None:
                delivery_messages.extend(transient)
            block, _revision = build_operator_context_block(
                "engineer", state_root, mission_id=mission_id, live_turn=operator_live_turn(transient),
            )
    except FileLockCancelled as exc:
        if current_run_interrupt_reason():
            return []
        raise OperatorContextUnavailable("Current Engineer OperatorContext read was cancelled") from exc
    except Exception as exc:
        raise OperatorContextUnavailable("Current Engineer OperatorContext is unavailable") from exc
    finally:
        if owned_receiver:
            # Standalone callers have no complete-prompt settlement hook.
            receiver.release_pending()
    return [block] if block else []


class SkillLoopExecuteMixin:
    """Mission-execution half of ``_SkillLoopRunner``."""

    @staticmethod
    def _is_link_or_reparse_point(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            if callable(is_junction) and is_junction():
                return True
            attributes = getattr(os.lstat(path), "st_file_attributes", 0)
            return bool(
                attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        except OSError:
            return False

    @classmethod
    def _has_linked_ancestor(cls, path: Path) -> bool:
        for parent in path.parents:
            if parent == parent.parent:
                break
            if os.path.lexists(parent) and cls._is_link_or_reparse_point(parent):
                return True
        return False

    @classmethod
    def _is_unaliased_regular_file(cls, path: Path) -> bool:
        try:
            return (
                not cls._is_link_or_reparse_point(path)
                and not cls._has_linked_ancestor(path)
                and path.is_file()
                and os.stat(path).st_nlink == 1
            )
        except OSError:
            return False

    @classmethod
    def _detach_packaged_skill_hardlink(cls, path: Path) -> None:
        """Give an installed Skill its own inode before establishing the guard.

        uv legitimately hardlinks wheel resources from its cache. Replacing our
        directory entry preserves those cached bytes and other environments;
        in-place writes or weakening the execution-time alias check would not.
        Symlinks, junctions and redirected ancestors remain disallowed.
        """
        if cls._is_link_or_reparse_point(path) or cls._has_linked_ancestor(path):
            return
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink <= 1:
            return
        content = path.read_bytes()
        fd, filename = tempfile.mkstemp(prefix=".argus-skill-", dir=path.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(stat.S_IMODE(before.st_mode))
            if cls._is_link_or_reparse_point(path) or cls._has_linked_ancestor(path):
                raise OSError(f"protected Skill path changed while preparing: {path}")
            current = path.stat()
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            if identity(current) != identity(before) or path.read_bytes() != content:
                # Another startup may already have detached exactly these bytes.
                if cls._is_unaliased_regular_file(path) and path.read_bytes() == content:
                    return
                raise OSError(f"protected Skill changed while preparing: {path}")
            os.replace(temporary, path)
            if not cls._is_unaliased_regular_file(path) or path.read_bytes() != content:
                raise OSError(f"protected Skill private copy did not verify: {path}")
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def _remove_pipeline_state_replacement(cls, path: Path) -> None:
        if path.is_symlink():
            path.unlink()
            return
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            path.rmdir()
            return
        if cls._is_link_or_reparse_point(path):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    @classmethod
    def _snapshot_pipeline_state(
        cls,
        workdir: Path,
    ) -> tuple[Path, bool, bytes | None, str]:
        from ..core.pipeline_state import pipeline_state_path

        path = pipeline_state_path(workdir.expanduser().resolve(strict=False))
        try:
            if os.path.lexists(path.parent) and (
                cls._is_link_or_reparse_point(path.parent)
                or not path.parent.is_dir()
            ):
                return path, True, None, "formal pipeline state parent is not a real directory"
            if not os.path.lexists(path):
                return path, False, None, ""
            if not cls._is_unaliased_regular_file(path):
                return path, True, None, "formal pipeline state is not a regular file"
            return path, True, path.read_bytes(), ""
        except OSError as exc:
            return path, True, None, f"cannot snapshot formal pipeline state: {exc}"

    @classmethod
    def _restore_pipeline_state(
        cls,
        snapshot: tuple[Path, bool, bytes | None, str],
    ) -> tuple[bool, str, bool]:
        path, existed, content, snapshot_error = snapshot
        if snapshot_error:
            return True, snapshot_error, False
        try:
            # A fresh mission may have neither a pipeline file nor its parent.
            # Restoration must not create that parent and then accuse the
            # mission of creating formal state that never existed.
            if not existed and not os.path.lexists(path.parent):
                return False, "", True
            if cls._has_linked_ancestor(path.parent):
                raise OSError(
                    f"formal pipeline state ancestor was replaced: {path.parent}"
                )
            parent_changed = False
            if os.path.lexists(path.parent) and (
                cls._is_link_or_reparse_point(path.parent)
                or not path.parent.is_dir()
            ):
                cls._remove_pipeline_state_replacement(path.parent)
                path.parent.mkdir(parents=True, exist_ok=True)
                parent_changed = True
            elif not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                parent_changed = True
            current_exists = os.path.lexists(path)
            if not existed:
                if not current_exists and not parent_changed:
                    return False, "", True
                if current_exists:
                    cls._remove_pipeline_state_replacement(path)
                return (
                    True,
                    "Playground execution created formal pipeline state; removed it",
                    True,
                )

            if (
                not parent_changed
                and current_exists
                and cls._is_unaliased_regular_file(path)
                and path.read_bytes() == content
            ):
                return False, "", True
            if current_exists:
                cls._remove_pipeline_state_replacement(path)
            if cls._has_linked_ancestor(path.parent):
                raise OSError(
                    f"formal pipeline state ancestor was replaced: {path.parent}"
                )
            path.write_bytes(content or b"")
            if (
                cls._is_link_or_reparse_point(path.parent)
                or not path.parent.is_dir()
                or not cls._is_unaliased_regular_file(path)
                or path.read_bytes() != (content or b"")
            ):
                raise OSError("restored formal pipeline state did not verify")
            return (
                True,
                "Playground execution modified formal pipeline state; restored it",
                True,
            )
        except OSError as exc:
            return True, f"formal pipeline state isolation failed: {exc}", False

    @staticmethod
    def _canonical_playground_skill_paths() -> tuple[Path, Path]:
        root = Path(__file__).resolve().parents[1]
        return (
            root
            / "domains"
            / "chemistry"
            / "skills"
            / "engineer"
            / "workflows"
            / "chemistry-playground.md",
            root
            / "domains"
            / "chemistry"
            / "skills"
            / "reviewer"
            / "chemistry-playground-review.md",
        )

    @classmethod
    def _snapshot_playground_skill_files(
        cls,
    ) -> tuple[tuple[tuple[Path, bytes], ...], str]:
        snapshots: list[tuple[Path, bytes]] = []
        try:
            canonical_paths = cls._canonical_playground_skill_paths()
            protected_paths = list(canonical_paths)
            for parent in dict.fromkeys(path.parent for path in canonical_paths):
                for sibling in sorted(parent.iterdir()):
                    # Another mission may be detaching a cache hardlink now;
                    # its short-lived private copy is not a packaged Skill.
                    if sibling.name.startswith(".argus-skill-"):
                        continue
                    if sibling not in protected_paths and sibling.is_file():
                        protected_paths.append(sibling)
            for path in protected_paths:
                cls._detach_packaged_skill_hardlink(path)
                if (
                    cls._is_link_or_reparse_point(path.parent)
                    or not path.parent.is_dir()
                    or not cls._is_unaliased_regular_file(path)
                ):
                    return (), f"protected Playground Skill is not a regular file: {path}"
                snapshots.append((path, path.read_bytes()))
        except OSError as exc:
            return (), f"cannot snapshot protected Playground Skills: {exc}"
        return tuple(snapshots), ""

    @classmethod
    def _restore_playground_skill_files(
        cls,
        snapshots: tuple[tuple[Path, bytes], ...],
        snapshot_error: str,
    ) -> tuple[bool, str, bool]:
        if snapshot_error:
            return True, snapshot_error, False
        changed_paths: list[str] = []
        try:
            canonical_paths = set(cls._canonical_playground_skill_paths())
            recovery_parents = {
                path.parent
                for path in canonical_paths
                if (
                    not path.parent.is_dir()
                    or cls._is_link_or_reparse_point(path.parent)
                    or not cls._is_unaliased_regular_file(path)
                )
            }
            for path, content in snapshots:
                if path not in canonical_paths and path.parent not in recovery_parents:
                    continue
                if cls._has_linked_ancestor(path.parent):
                    raise OSError(
                        f"protected Skill ancestor was replaced: {path.parent}"
                    )
                if os.path.lexists(path.parent) and (
                    cls._is_link_or_reparse_point(path.parent)
                    or not path.parent.is_dir()
                ):
                    cls._remove_pipeline_state_replacement(path.parent)
                if not path.parent.is_dir():
                    if not path.parent.parent.is_dir():
                        raise OSError(
                            f"protected Skill ancestor is missing: {path.parent.parent}"
                        )
                    path.parent.mkdir(exist_ok=False)
                    changed_paths.append(str(path.parent))
                if (
                    os.path.lexists(path)
                    and cls._is_unaliased_regular_file(path)
                    and path.read_bytes() == content
                ):
                    continue
                if os.path.lexists(path):
                    cls._remove_pipeline_state_replacement(path)
                path.write_bytes(content)
                if (
                    not cls._is_unaliased_regular_file(path)
                    or path.read_bytes() != content
                ):
                    raise OSError(f"protected Skill restoration did not verify: {path}")
                changed_paths.append(str(path))
        except OSError as exc:
            return True, f"protected Playground Skill isolation failed: {exc}", False
        if not changed_paths:
            return False, "", True
        return (
            True,
            "Playground execution modified protected Skill files; restored: "
            + ", ".join(changed_paths),
            True,
        )

    @classmethod
    def _restore_playground_boundaries(
        cls,
        pipeline_snapshot: tuple[Path, bool, bytes | None, str],
        skill_snapshots: tuple[tuple[Path, bytes], ...],
        skill_snapshot_error: str,
    ) -> tuple[bool, str, bool]:
        pipeline_changed, pipeline_reason, pipeline_ok = cls._restore_pipeline_state(
            pipeline_snapshot
        )
        skills_changed, skills_reason, skills_ok = cls._restore_playground_skill_files(
            skill_snapshots,
            skill_snapshot_error,
        )
        reasons = [reason for reason in (pipeline_reason, skills_reason) if reason]
        return (
            pipeline_changed or skills_changed,
            "; ".join(reasons),
            pipeline_ok and skills_ok,
        )

    @staticmethod
    def _playground_skills_from_snapshots(
        snapshots: tuple[tuple[Path, bytes], ...],
    ) -> tuple[object | None, object | None, str]:
        """Return trusted source paths without parsing Skill Markdown."""
        if len(snapshots) < 2:
            return None, None, "protected Playground Skill snapshot is incomplete"
        try:
            snapshots[0][1].decode("utf-8")
            snapshots[1][1].decode("utf-8")
        except UnicodeError as exc:
            return None, None, f"protected Playground Skill is not UTF-8: {exc}"
        return snapshots[0][0], snapshots[1][0], ""

    def execute(
        self,
        *,
        objective: str,
        original_objective: str = "",
        review_objective: str = "",
        sink: EventSink,
        preload_injects: list[str] | None = None,  # noqa: ARG002 — protocol parity
        prelude_context: str = "",
        prelude_context_provider: Callable[[], str] | None = None,
        seed_thread_id: str | None = None,
        scope: str = "",
        preplanned: bool = False,
        mission_id: str | None = None,
        usage_mission_id: str | None = None,
        context_packet_path: str = "",
        max_rounds_override: int | None = None,
        workflow_mode_override: str = "",
        require_independent_review: bool = True,
        skip_stage_transition: bool = False,
        stage_closing: bool = False,
        holds_stage_authority: bool = True,
        working_dir_override: str = "",
        maintenance_mission: bool = False,
        allow_skill_changes: bool = False,
        vertical_override: str = "",
    ) -> _Outcome:
        from ._runtime_interrupt import execution_interrupt_scope

        with execution_interrupt_scope(
            stop_event=getattr(self, "_execution_stop_event", None),
            state_root=getattr(self, "_manager_session_root", None),
            mission_id=str(mission_id or getattr(self, "_active_mission_id", "") or ""),
            enable_abort=bool(getattr(self, "_enable_mission_abort_signal", False)),
        ):
            ex_state = _ExecuteState()
            ex_state.prelude_context_provider = prelude_context_provider
            self._build_execute_config(
                ex_state,
                working_dir_override=working_dir_override,
                maintenance_mission=maintenance_mission,
                vertical_override=vertical_override,
                require_independent_review=require_independent_review,
                max_rounds_override=max_rounds_override,
                context_packet_path=context_packet_path,
                mission_id=mission_id,
                objective=objective,
                workflow_mode_override=workflow_mode_override,
            )
            self._build_execute_skill_store_and_loop(ex_state, sink=sink)
            self._prepare_execute_mission_context(
                ex_state,
                objective=objective,
                review_objective=review_objective,
                prelude_context=prelude_context,
                seed_thread_id=seed_thread_id,
                scope=scope,
            )
            self._invoke_execute_loop(
                ex_state,
                sink=sink,
                objective=objective,
                original_objective=original_objective,
                mission_id=mission_id,
                usage_mission_id=usage_mission_id,
            )
            self._extract_execute_outcome_fields(ex_state)
            self._maybe_decide_stage_transition(
                ex_state,
                sink=sink,
                mission_id=mission_id,
                usage_mission_id=usage_mission_id,
                maintenance_mission=maintenance_mission,
                skip_stage_transition=skip_stage_transition,
                preplanned=preplanned,
                stage_closing=stage_closing,
                holds_stage_authority=holds_stage_authority,
            )
            return self._build_execute_outcome(ex_state)

    def _build_execute_config(
        self,
        ex_state: "_ExecuteState",
        *,
        working_dir_override: str,
        maintenance_mission: bool,
        vertical_override: str,
        require_independent_review: bool,
        max_rounds_override: int | None,
        context_packet_path: str,
        mission_id: str | None,
        workflow_mode_override: str,
        objective: str = "",
    ) -> None:
        """Resolve the workdir/vertical-derived flags and build the
        ``SkillLoopConfig`` for this mission.
        """
        args = self._args
        from ._runtime_supervisor import (
            _independent_review_required_for_project_root,
            _paper_mission_for_project_root,
            _workflow_mode_for_project_root,
        )

        workdir = (
            Path(working_dir_override).expanduser().resolve()
            if working_dir_override
            else Path(args.workdir).expanduser()
            if args.workdir
            else Path.cwd()
        )
        # Execution happens in the operator workspace, but vertical contracts
        # live in session state. A working-dir override must not make a freshly
        # authored project-local vertical disappear before Engineer starts.
        _proot = (
            workdir
            if maintenance_mission
            else Path(getattr(self, "_artifact_root", None) or workdir)
        )
        active_vertical = str(vertical_override or "").strip()
        active_contract = None
        if active_vertical:
            from ..skills.vertical_select import require_vertical
            from ..verticals._base import load_vertical_contract

            active_vertical = require_vertical(active_vertical, _proot)
            active_contract = load_vertical_contract(
                active_vertical,
                project_root=_proot,
            )
        effective_require_independent_review = bool(
            require_independent_review
            or _env_flag("ARGUS_SKILL_REQUIRE_INDEPENDENT_REVIEW", False)
            or (
                active_contract.requires_independent_review
                if active_contract is not None
                else _independent_review_required_for_project_root(_proot)
            )
        )
        if not effective_require_independent_review:
            # Bug #42: 14 consecutive missions closed on the Engineer's own
            # say-so and the only trace of it was a reason string inside each
            # review record. Dropping the Reviewer is a policy decision; say so
            # once, out loud, with the inputs that produced it. The framework
            # path is the one that mattered — the daemon had rolled back to a
            # source root whose math vertical predated the review requirement.
            from ..skills.stage_machine import framework_source_root

            waiver_reason = (
                "framework maintenance mission"
                if maintenance_mission
                else "explicit mission configuration with no stricter vertical policy"
            )
            log.warning(
                "independent review waived: %s; "
                "project_root=%s vertical=%s framework=%s",
                waiver_reason,
                _proot,
                active_vertical or "<persisted>",
                framework_source_root(),
            )
        # 7×24 product: the agent CLIs ask no permission (dangerous_yolo);
        # the agent sandbox (core.sandbox) bounds what they can see.
        # ARGUS_SKILL_SAFE_MODE=1 turns their own permission modes back on.
        safe_mode = _env_flag("ARGUS_SKILL_SAFE_MODE", False)
        config_kwargs = {
            "engineer_model": args.engineer_model,
            "reviewer_model": args.reviewer_model,
            "require_independent_review": effective_require_independent_review,
            "max_rounds": (
                max(1, int(max_rounds_override))
                if max_rounds_override is not None
                else args.max_rounds
            ),
            "require_post_task_learning": bool(
                getattr(self, "_role_memory_maintenance_enabled", True)
            ),
            "wiki_enabled": learning_enabled(),
            "auto_init_wiki": learning_enabled(),
            "dangerous_yolo": not safe_mode,
            "full_auto": safe_mode,
            "sandbox_mode": (
                "workspace-write" if maintenance_mission and safe_mode else None
            ),
            "isolate_workdir": bool(maintenance_mission and safe_mode),
            "skip_git_repo_check": True,
            # Filled from the resolved vertical below.  Fail-safe default: an
            # undecided task is bounded/non-paper.
            "paper_mission": False,
            "active_vertical": active_vertical,
            "vertical_state_root": _proot,
            # Shared Markdown checkpoint in internal project state. Engineer
            # and Reviewer receive its absolute path and edit it in sequence;
            # output workdirs contain deliverables only.
            "checkpoint_path": _checkpoint_path_for(
                args,
                Path(args.workdir).expanduser() if args.workdir else Path.cwd(),
            ),
            "context_packet_path": str(context_packet_path or ""),
            "session_id": mission_id,
            # Process-correctness audit: the reviewer runs in the project
            # work-tree and only sees the engineer's final summary. Give it the
            # ABSOLUTE path to this project's engineer execution log
            # (``<life_dir>/events.jsonl``) so it can grep HOW the result was
            # produced. This runtime log remains outside the worktree.
        }
        maintenance_checkpoint_dir: Path | None = None
        if context_packet_path:
            config_kwargs["checkpoint_path"] = (
                Path(context_packet_path).expanduser().resolve().parent / "CHECKPOINT.md"
            )
        if maintenance_mission:
            maintenance_checkpoint_dir = workdir / ".argus-self-maintenance-runtime"
            maintenance_checkpoint_dir.mkdir(parents=True, exist_ok=True)
            config_kwargs["checkpoint_path"] = maintenance_checkpoint_dir / "CHECKPOINT.md"
        _project_state_dir = _project_state_dir_for(
            args, Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        config_kwargs["engineer_log_path"] = (
            str(_project_state_dir / "events.jsonl") if _project_state_dir is not None else ""
        )
        from ..life.knowledge_recall import knowledge_read_dirs

        # The Engineer's recalled knowledge, and this task's handoff directory
        # its prompt names (mission, latest handoff, checkpoint), lie outside
        # its workdir; a sandboxed backend reads them only when they are given.
        config_kwargs["add_dirs"] = [
            *knowledge_read_dirs(_project_state_dir, getattr(args, "global_root", None), workdir),
            *([str(Path(context_packet_path).expanduser().resolve().parent)] if context_packet_path else []),
        ] or None
        from ..core.autonomy import operator_questions_can_be_asked

        explicit_operator_root = str(getattr(args, "operator_context_dir", "") or "").strip()
        config_kwargs["operator_questions_allowed"] = operator_questions_can_be_asked()
        config_kwargs["state_root"] = (
            Path(explicit_operator_root).expanduser() if explicit_operator_root else _project_state_dir
        )
        # Campaign lifetime metadata forwarded from the daemon namespace so the
        # Manager stage hook receives open_ended=True for daemon-created open-ended
        # campaigns, preventing final_stage_completion_decision from overwriting a
        # structured Manager rollback verdict with a bounded completion.
        config_kwargs["open_ended"] = bool(getattr(args, "open_ended", False))
        config_kwargs["continuous_objective"] = str(getattr(args, "continuous_objective", "") or "")
        # The Manager-persisted decision (fallback ``staged``) is authoritative;
        # the vertical's WORKFLOW_MODE only shapes the evidence policy of a
        # staged run, never direct vs staged orchestration.
        resolved_workflow_mode = (
            workflow_mode_override.strip().lower() or _workflow_mode_for_project_root(_proot)
        )
        config_kwargs["workflow_mode"] = resolved_workflow_mode
        # A paper contract is enabled only by a non-direct vertical that explicitly
        # declares PAPER_MISSION. Certification strength is a separate contract.
        # An explicit False may opt out; True cannot turn a non-paper vertical
        # into a paper.
        _paper_override = getattr(args, "paper_mission", None)
        _paper_allowed = True if _paper_override is None else bool(_paper_override)
        config_kwargs["paper_mission"] = bool(
            not maintenance_mission
            and resolved_workflow_mode != "direct"
            and _paper_allowed
            and (
                active_contract.paper_mission
                if active_contract is not None
                else _paper_mission_for_project_root(_proot)
            )
        )
        # Effort by need: bounded work runs at medium, deeper work at high, and
        # an operator's explicit effort wins (core.effort_policy).
        from ..core.effort_policy import is_bounded_mission, mission_efforts

        _vertical = active_vertical
        if not _vertical:
            try:
                from ..skills.vertical_select import resolve_skill_scope

                _vertical = resolve_skill_scope(_proot)
            except Exception:  # noqa: BLE001 — an unreadable vertical counts as deep work
                _vertical = "research"
        _efforts = mission_efforts(bounded=not maintenance_mission and is_bounded_mission(
            workflow_mode=resolved_workflow_mode,
            paper_mission=config_kwargs["paper_mission"],
            vertical=_vertical,
        ))
        config_kwargs["engineer_initial_reasoning_effort"] = _efforts.engineer_initial
        config_kwargs["engineer_reasoning_effort"] = _efforts.engineer
        config_kwargs["reviewer_reasoning_effort"] = _efforts.reviewer
        try:
            from inspect import signature

            sig = signature(self._SkillLoopConfig)
            if not any(param.kind == param.VAR_KEYWORD for param in sig.parameters.values()):
                config_kwargs = {
                    key: value for key, value in config_kwargs.items() if key in sig.parameters
                }
        except (TypeError, ValueError):
            pass
        ex_state.workdir = workdir
        ex_state.effective_require_independent_review = effective_require_independent_review
        ex_state.config = self._SkillLoopConfig(**config_kwargs)
        ex_state.maintenance_checkpoint_dir = maintenance_checkpoint_dir

    def _build_execute_skill_store_and_loop(
        self,
        ex_state: "_ExecuteState",
        *,
        sink: EventSink,
    ) -> None:
        """Refresh the Manager skill store, wire the per-round operator inbox
        drain, and construct this mission's ``SkillLoop``.
        """
        args = self._args
        workdir = ex_state.workdir
        config = ex_state.config
        self._refresh_manager_skill_store(args, workdir=workdir)
        # The per-project runtime state dir holds inbox.jsonl + events.jsonl.
        operator_state_dir = _project_state_dir_for(args, workdir)
        # Both built-in consumers share durable claims. Canonical acceptance is
        # idempotent; ephemeral messages settle only after full prompt assembly.
        inbox_life_dir = operator_state_dir
        from ._inbox_delivery import EngineerInboxGuidance

        extra_guidance_provider = (
            EngineerInboxGuidance(
                inbox_life_dir, workdir, lambda: getattr(self, "manager", None),
                mission_id=str(getattr(config, "session_id", "") or ""),
            ) if inbox_life_dir is not None else None
        )
        engineer_backend = getattr(self, "engineer_backend", None) or self._backend
        global_skills_dir = Path(args.skills_dir)
        skill_store = None
        project_state_dir = str(getattr(args, "project_state_dir", "") or "").strip()
        if project_state_dir:
            from ..skills.layered import (
                LayeredSkillStore,
                shared_skill_scope_dir,
            )
            from ..skills.vertical_select import resolve_skill_scope

            active_skill_scope = config.active_vertical or resolve_skill_scope(workdir)
            vertical_dir = shared_skill_scope_dir(
                global_skills_dir,
                active_skill_scope,
            )
            if vertical_dir is not None and active_skill_scope:
                from ..skills.builtins import seed_context_skills

                seed_context_skills(
                    vertical_dir,
                    active_skill_scope,
                    overwrite=False,
                )
            explicit_project_skills = str(
                os.environ.get("ARGUS_SKILL_PROJECT_SKILLS_DIR", "") or ""
            ).strip()
            project_skills_dir = (
                Path(explicit_project_skills)
                if explicit_project_skills
                else Path(project_state_dir) / "skills"
            )

            skill_store = LayeredSkillStore(
                project_dir=project_skills_dir,
                global_dir=global_skills_dir,
                vertical_dir=vertical_dir,
                execution_project_root=workdir,
            )
        ex_state.loop = self._SkillLoop(
            skills_dir=global_skills_dir,
            engineer_runner=engineer_backend,
            reviewer_runner=getattr(self, "reviewer_backend", None) or self._backend,
            config=config,
            skill_store=skill_store,
            on_event=sink.handle_event,
            extra_guidance_provider=extra_guidance_provider,
            prelude_context_provider=getattr(ex_state, "prelude_context_provider", None),
        )

    def _prepare_execute_mission_context(
        self,
        ex_state: "_ExecuteState",
        *,
        objective: str,
        review_objective: str,
        prelude_context: str,
        seed_thread_id: str | None,
        scope: str,
    ) -> None:
        """Build the full task text (objective + prelude), pick the seed
        thread id to chain off of, and normalize the structural scope tag.
        """
        full_task = objective
        if prelude_context and getattr(ex_state, "prelude_context_provider", None) is None:
            full_task = f"{prelude_context}\n---\n## Live objective\n{objective}"
        # Use the seed for the first execute() of this runner; subsequent
        # execute() calls (LifeSupervisor may run several missions in one
        # supervisor.run()) chain off the previous mission's last thread_id.
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id
        # Scope is threaded structurally from the planner via the backlog
        # item's tags (LifeSupervisor passes _planner_scope_from_item(item)).
        # We no longer re-parse it out of the objective prose — the harness
        # should consume the structured field, not sniff the rendered text.
        mission_scope = (scope or "").strip().lower()
        ex_state.full_task = full_task
        ex_state.review_objective = review_objective or objective
        ex_state.seed = seed
        ex_state.mission_scope = mission_scope

    def _invoke_execute_loop(
        self,
        ex_state: "_ExecuteState",
        *,
        sink: EventSink,
        objective: str,
        original_objective: str,
        mission_id: str | None,
        usage_mission_id: str | None,
    ) -> None:
        """Run the mission through ``SkillLoop.run`` and this call's
        sink/usage-context teardown.
        """
        self._current_sink = sink
        self._current_failure_ledger = None
        self._set_usage_context(usage_mission_id or mission_id)
        pipeline_state_snapshot = self._snapshot_pipeline_state(ex_state.workdir)
        skill_snapshots, skill_snapshot_error = self._snapshot_playground_skill_files()
        (
            canonical_playground_engineer,
            canonical_playground_reviewer,
            canonical_skill_error,
        ) = self._playground_skills_from_snapshots(skill_snapshots)
        expected_playground_path = self._canonical_playground_skill_path(
            skill_snapshots
        )
        execution_started = False
        try:
            if pipeline_state_snapshot[3]:
                raise RuntimeError(
                    "refused before execution: " + pipeline_state_snapshot[3]
                )
            if (
                skill_snapshot_error
                or canonical_skill_error
                or not expected_playground_path
            ):
                raise RuntimeError(
                    "refused before execution: "
                    + (
                        skill_snapshot_error
                        or canonical_skill_error
                        or "canonical Playground Engineer digest is unavailable"
                    )
                )
            execution_started = True

            def _pre_settlement_guard(
                _mission: object,
                state: object,
                status: str,
                _rounds: list,
                final_message: str,
                reason: str,
            ) -> tuple[str, str, str]:
                skills_changed, skills_reason, skills_ok = (
                    self._restore_playground_skill_files(
                        skill_snapshots,
                        skill_snapshot_error,
                    )
                )
                if not skills_ok:
                    raise RuntimeError(skills_reason)
                if skills_changed:
                    ex_state.protected_playground_source_violation = True
                    log.error("protected Skill isolation: %s", skills_reason)
                    return "blocked", skills_reason, skills_reason
                skill = getattr(state, "skill", None)
                playground_claimed = (
                    getattr(skill, "name", "")
                    == "Chemistry Playground Bounded Hypothesis Probe"
                    or getattr(skill, "category", "") == "chemistry-playground"
                )
                if not playground_claimed:
                    return status, final_message, reason
                ex_state.playground_workflow_guarded = True
                ex_state.trusted_playground_workflow = bool(
                    skill is not None
                    and expected_playground_path
                    and str(getattr(skill, "path", "")) == expected_playground_path
                )
                changed, isolation_reason, restoration_ok = (
                    self._restore_playground_boundaries(
                        pipeline_state_snapshot,
                        skill_snapshots,
                        skill_snapshot_error,
                    )
                )
                if not restoration_ok:
                    raise RuntimeError(isolation_reason)
                if changed or not ex_state.trusted_playground_workflow:
                    if not ex_state.trusted_playground_workflow:
                        isolation_reason = (
                            "Playground workflow trust validation failed; "
                            "formal stage transition was suppressed"
                        )
                    log.error("Chemistry Playground isolation: %s", isolation_reason)
                    return "blocked", isolation_reason, isolation_reason
                return status, final_message, reason

            ex_state.loop.canonical_playground_engineer_skill = (
                canonical_playground_engineer
            )
            ex_state.loop.canonical_playground_reviewer_skill = (
                canonical_playground_reviewer
            )
            ex_state.loop.pre_settlement_guard = _pre_settlement_guard
            ex_state.outcome = ex_state.loop.run(
                ex_state.full_task,
                workdir=ex_state.workdir,
                seed_thread_id=ex_state.seed,
                objective_for_skill=objective,
                review_objective=ex_state.review_objective,
                original_objective=original_objective or objective,
                scope=ex_state.mission_scope,
            )
            skills_changed, skills_reason, skills_ok = (
                self._restore_playground_skill_files(
                    skill_snapshots,
                    skill_snapshot_error,
                )
            )
            if not skills_ok:
                raise RuntimeError(skills_reason)
            if skills_changed:
                log.error("protected Skill isolation: %s", skills_reason)
                ex_state.protected_playground_source_violation = True
                ex_state.outcome.status = "blocked"
                ex_state.outcome.reason = skills_reason
                ex_state.outcome.recoverable = False
                if hasattr(ex_state.outcome, "final_message"):
                    ex_state.outcome.final_message = skills_reason
            playground_claimed = self._playground_workflow_claimed(
                ex_state.outcome
            )
            ex_state.trusted_playground_workflow = self._trusted_playground_workflow(
                ex_state.outcome,
                expected_path=expected_playground_path,
            )
            ex_state.playground_workflow_guarded = playground_claimed
            if playground_claimed:
                changed, isolation_reason, restoration_ok = (
                    self._restore_playground_boundaries(
                        pipeline_state_snapshot,
                        skill_snapshots,
                        skill_snapshot_error,
                    )
                )
                if changed or not ex_state.trusted_playground_workflow:
                    if not ex_state.trusted_playground_workflow:
                        isolation_reason = (
                            "Playground workflow trust validation failed; "
                            "formal stage transition was suppressed"
                        )
                    log.error("Chemistry Playground isolation: %s", isolation_reason)
                    if not restoration_ok:
                        raise RuntimeError(isolation_reason)
                    ex_state.outcome.status = "blocked"
                    ex_state.outcome.reason = isolation_reason
                    ex_state.outcome.recoverable = False
                    if hasattr(ex_state.outcome, "final_message"):
                        ex_state.outcome.final_message = isolation_reason
        except BaseException as execution_error:
            if not execution_started:
                # Preflight never ran the planner/engineer. Retain its actual
                # error; a failed snapshot is not an execution-time mutation.
                raise
            changed, isolation_reason, restoration_ok = self._restore_playground_boundaries(
                pipeline_state_snapshot,
                skill_snapshots,
                skill_snapshot_error,
            )
            if changed:
                log.error(
                    "mission exception required formal pipeline isolation recovery: %s",
                    isolation_reason,
                )
            if not restoration_ok:
                raise RuntimeError(isolation_reason) from execution_error
            raise
        finally:
            self._current_sink = None
            self._current_failure_ledger = None
            self._set_usage_context(None)
            if ex_state.maintenance_checkpoint_dir is not None:
                try:
                    shutil.rmtree(ex_state.maintenance_checkpoint_dir)
                except OSError:
                    pass

    def _extract_execute_outcome_fields(self, ex_state: "_ExecuteState") -> None:
        """Update thread-id/auth-failure bookkeeping and pull the final
        round's reviewer verdict fields (planner report, harness control,
        failure attribution, final-submission certification, ...) off
        ``ex_state.outcome`` for the journal and the returned ``_Outcome``.
        """
        outcome = ex_state.outcome
        new_tid = getattr(outcome, "last_thread_id", None)
        if should_clear_thread_id_after_outcome(
            fatal_error=str(getattr(outcome, "stop_reason", "") or ""),
            stop_kind=getattr(outcome, "stop_kind", None),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_tid = None
        elif new_tid:
            self.last_thread_id = new_tid
            self._next_seed_thread_id = new_tid
        auth_fail = self._consume_auth_failure()
        # Reviewer completion contract: certify whole-project completion only
        # from the final reviewer verdict (never raw success). Fail-closed:
        # absent rounds / review / non-final scope ⇒ not certified.
        final_submission_certified = False
        completion_evidence = ""
        operator_question = ""
        operator_options: list[dict] = []
        final_review_status = ""
        final_review_next_action = ""
        review_source = ""
        final_residual_risk = ""
        final_learning = ""
        plan_challenge: dict = {}
        rounds_list = getattr(outcome, "rounds", None) or []
        if rounds_list:
            _final_review = getattr(rounds_list[-1], "review", None)
            if _final_review is not None:
                binding = getattr(_final_review, "manuscript_snapshot", None)
                if isinstance(binding, dict):
                    try:
                        from ..core.manuscript_snapshot import (
                            manuscript_review_status,
                        )

                        # The Reviewer bound the manuscript it read in the
                        # execution workdir; compare against that same tree.
                        # ``_artifact_root`` is the session state root, which
                        # holds no manuscript and would grade every current
                        # review as stale.
                        review_freshness = manuscript_review_status(
                            {"manuscript_snapshot": binding},
                            Path(ex_state.workdir),
                        )
                    except Exception:  # noqa: BLE001 - certification fails closed
                        review_freshness = {
                            "status": "unbound",
                            "message": "unbound (reviewed manuscript cannot be read)",
                        }
                    if review_freshness.get("status") != "current":
                        setattr(_final_review, "status", "stale")
                        setattr(
                            _final_review,
                            "reason",
                            str(review_freshness.get("message") or "stale review"),
                        )
                final_review_status = (
                    str(getattr(_final_review, "status", "") or "").strip().lower()
                )
                review_source = str(getattr(_final_review, "review_source", "") or "").strip()
                final_residual_risk = str(getattr(_final_review, "residual_risk", "") or "").strip()
                operator_question = str(
                    getattr(_final_review, "operator_question", "") or ""
                ).strip()
                raw_operator_options = getattr(_final_review, "operator_options", []) or []
                if isinstance(raw_operator_options, list):
                    operator_options = [
                        dict(option)
                        for option in raw_operator_options
                        if isinstance(option, dict)
                    ]
                final_review_next_action = str(
                    getattr(_final_review, "next_action", "") or ""
                ).strip()
                final_learning = str(getattr(_final_review, "learning", "") or "")
                if final_review_status == "planner":
                    # The Reviewer's challenge goes straight to the Planner as
                    # new input: the Planner owns the plan, nobody adjudicates.
                    plan_challenge = {
                        "challenge": str(getattr(_final_review, "reason", "") or "").strip(),
                        "alternative": final_review_next_action,
                        "source": "reviewer",
                        "raised_at": time.time(),
                    }
                if _final_review.reviewer_authored or review_source.startswith("engineer_"):
                    # A role's note is for the next holder, not the operator.
                    final_review_next_action = ""
        if ex_state.mission_scope == "final_submission":
            final_review = None
            if rounds_list:
                final_review = getattr(rounds_list[-1], "review", None)
            if final_review is not None and getattr(
                final_review, "final_submission_certified", False
            ):
                final_submission_certified = True
                completion_evidence = getattr(final_review, "reason", "")
                from ..core.venue_review import (
                    current_venue_acceptance_issue,
                    has_submission_candidate,
                )
                from ..skills.vertical_select import resolve_vertical_if_decided

                state_root = Path(getattr(self, "_artifact_root", ex_state.workdir))
                if resolve_vertical_if_decided(state_root) == "research" and has_submission_candidate(
                    ex_state.workdir,
                ):
                    acceptance_issue = current_venue_acceptance_issue(
                        final_review, state_root=state_root, artifact_root=ex_state.workdir,
                    )
                    if acceptance_issue:
                        final_submission_certified = False
                        completion_evidence = acceptance_issue
        if _execution_host_blocked_outcome(outcome):
            # The round record describes an infrastructure interruption, not
            # an independent review or evidence of project completion.
            final_review_status = "not_assessed"
            review_source = ""
            final_residual_risk = ""
            final_submission_certified = False
            completion_evidence = ""
        elif _final_round_unjudged(outcome):
            # The status stays as recorded (resume and dedupe read it); only
            # the credit goes, since no Reviewer judged this round.
            review_source = ""
        ex_state.new_tid = new_tid
        ex_state.auth_fail = auth_fail
        ex_state.rounds_list = rounds_list
        ex_state.operator_question = operator_question
        ex_state.operator_options = operator_options
        ex_state.final_review_status = final_review_status
        ex_state.final_review_next_action = final_review_next_action
        ex_state.review_source = review_source
        ex_state.final_residual_risk = final_residual_risk if final_review_status == "done" else ""
        ex_state.final_learning = final_learning
        ex_state.plan_challenge = plan_challenge
        ex_state.final_submission_certified = final_submission_certified
        ex_state.completion_evidence = completion_evidence

    def _maybe_decide_stage_transition(
        self,
        ex_state: "_ExecuteState",
        *,
        sink: EventSink,
        mission_id: str | None,
        usage_mission_id: str | None,
        maintenance_mission: bool,
        skip_stage_transition: bool,
        preplanned: bool,
        stage_closing: bool,
        holds_stage_authority: bool = True,
    ) -> None:
        """Hand this round's structured completion verdict to the completion
        gate when this round is eligible to move the pipeline stage.

        STAGE AUTHORITY: the completion gate is the one writer of the pipeline
        stage. It reads the evidence (the accepted candidate, the vertical's
        stage checks, the person's required steps) and asks no model. See
        ``_decide_stage_transition``.
        """
        outcome = ex_state.outcome
        effective_status = str(outcome.status)
        effective_stop_kind = getattr(outcome, "stop_kind", None)
        effective_recoverable = bool(getattr(outcome, "recoverable", False))
        effective_reason = outcome.reason or ""
        stage_transition: dict = {}
        workflow_skips_stage_transition = (
            ex_state.playground_workflow_guarded
            or ex_state.protected_playground_source_violation
        )
        planned_node_holds_stage = (
            preplanned
            and ex_state.mission_scope.strip().lower().replace("-", "_")
            == "bounded"
            and not stage_closing
        )
        effective_skip_stage_transition = (
            workflow_skips_stage_transition
            or (skip_stage_transition and not stage_closing)
        )
        # Review-only tasks and ordinary non-stage-closing Planner nodes
        # suppress the stage writer; Reviewer-requested replans still reach
        # Manager through planning-cycle reconciliation.
        if (
            not maintenance_mission
            and not workflow_skips_stage_transition
            and not _final_round_unjudged(outcome)
            and _should_run_stage_transition(
                effective_status,
                mission_scope=ex_state.mission_scope,
                require_independent_review=(
                    ex_state.effective_require_independent_review
                ),
                review_source=ex_state.review_source,
                skip_stage_transition=effective_skip_stage_transition,
                preplanned=preplanned,
                stage_closing=stage_closing,
                holds_stage_authority=holds_stage_authority,
            )
        ):
            self._current_sink = sink
            self._set_usage_context(usage_mission_id or mission_id)
            try:
                stage_transition = self._decide_stage_transition(
                    rounds_list=ex_state.rounds_list,
                    workdir=ex_state.workdir,
                    sink=sink,
                    root_task_id=usage_mission_id or mission_id,
                    mission_scope=ex_state.mission_scope,
                    open_ended=bool(getattr(ex_state.config, "open_ended", False)),
                    continuous_objective=str(
                        getattr(ex_state.config, "continuous_objective", "") or ""
                    ),
                    stage_closing=stage_closing,
                )
            finally:
                self._current_sink = None
                self._set_usage_context(None)
        ex_state.effective_status = effective_status
        ex_state.effective_stop_kind = effective_stop_kind
        ex_state.effective_recoverable = effective_recoverable
        ex_state.effective_reason = effective_reason
        ex_state.stage_transition = stage_transition
        ex_state.stage_transition_skipped = bool(
            workflow_skips_stage_transition
            or not holds_stage_authority
            or (
                effective_skip_stage_transition
                and ex_state.effective_require_independent_review
                and ex_state.mission_scope.strip().lower().replace("-", "_")
                == "bounded"
            )
        )
        # Deliberately NOT folded into ``stage_transition_skipped`` above. A
        # planned node holding the stage is a deferral, not a suppression: its
        # Reviewer verdict is genuine evidence that the campaign-level stage
        # reconciliation is entitled to replay later. Collapsing the two made
        # every Planner node look review-suppressed, which left no vertical
        # whose completion gate is not ``certified`` any way to close a stage.
        ex_state.stage_transition_deferred = bool(
            planned_node_holds_stage
            and not _execution_host_blocked_outcome(outcome)
            and not _final_round_unjudged(outcome)
            and not ex_state.stage_transition_skipped
            and not stage_transition
        )

    def _build_execute_outcome(self, ex_state: "_ExecuteState") -> _Outcome:
        """Assemble the ``_Outcome`` returned to the caller from the fields
        gathered across the prior lifecycle phases.
        """
        outcome = ex_state.outcome
        rounds = getattr(outcome, "rounds", None) or ex_state.rounds_list
        engineer_message = str(getattr(outcome, "final_message", "") or "")
        summary_lines = []
        for line in engineer_message.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            summary_lines.append(cleaned.lstrip("#").strip())
        summary = " ".join(summary_lines)[:1200]
        final_output = engineer_message.strip()
        return _Outcome(
            success=bool(outcome.successful and ex_state.effective_status == "done"),
            status=ex_state.effective_status,
            stop_reason=ex_state.effective_reason,
            stop_kind=ex_state.effective_stop_kind,
            recoverable=ex_state.effective_recoverable,
            park_reason=str(getattr(outcome, "park_reason", "") or ""),
            rounds=outcome.round_count,
            last_thread_id=ex_state.new_tid,
            auth_failure=ex_state.auth_fail,
            final_submission_certified=ex_state.final_submission_certified,
            manuscript_snapshot=(
                dict(getattr(rounds[-1].review, "manuscript_snapshot", None))
                if rounds
                and isinstance(
                    getattr(rounds[-1].review, "manuscript_snapshot", None), dict
                )
                else None
            ),
            completion_evidence=ex_state.completion_evidence,
            stage_transition=ex_state.stage_transition,
            stage_transition_skipped=ex_state.stage_transition_skipped,
            stage_transition_deferred=ex_state.stage_transition_deferred,
            operator_question=ex_state.operator_question,
            operator_options=ex_state.operator_options,
            final_review_status=ex_state.final_review_status,
            final_review_source=ex_state.review_source,
            final_residual_risk=ex_state.final_residual_risk,
            final_review_reason=str(
                getattr(outcome, "final_review_reason", "") or ""
            ),
            final_review_next_action=ex_state.final_review_next_action,
            final_message=engineer_message,
            summary=summary,
            final_output=final_output,
            research_result=(
                getattr(
                    rounds[-1].review,
                    "research_result",
                    None,
                )
                if rounds
                else None
            ),
            venue_review=getattr(rounds[-1].review, "venue_review", None) if rounds else None,
            venue_review_snapshot=getattr(rounds[-1].review, "venue_review_snapshot", None) if rounds else None,
            final_learning=ex_state.final_learning,
            plan_challenge=ex_state.plan_challenge,
        )

    @staticmethod
    def _canonical_playground_skill_path(
        snapshots: tuple[tuple[Path, bytes], ...],
    ) -> str:
        """Compatibility marker: use the semantic source path, never a digest."""
        return str(snapshots[0][0]) if snapshots else ""

    @staticmethod
    def _playground_workflow_claimed(outcome: object) -> bool:
        extras = getattr(outcome, "extras", {})
        return bool(
            isinstance(extras, dict)
            and str(extras.get("skill_path") or "").endswith(
                "/chemistry-playground.md"
            )
        )

    @classmethod
    def _trusted_playground_workflow(
        cls,
        outcome: object,
        *,
        expected_path: str,
    ) -> bool:
        if not cls._playground_workflow_claimed(outcome):
            return False
        extras = getattr(outcome, "extras", {})
        if not isinstance(extras, dict):
            return False
        return bool(expected_path) and (
            str(extras.get("skill_path") or "") == expected_path
        )
