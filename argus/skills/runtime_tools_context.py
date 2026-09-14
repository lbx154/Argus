"""Attach project tool learning to the existing, budgeted Pi provider call."""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from ..advisor.runtime import caller_role
from ..core.role_tool_bridge import CallBoundBridge
from .role_memory import role_skill_maintenance_enabled
from .runtime_tools import RuntimeToolService

EXTENSION = str(Path(__file__).with_name('runtime_extension.mjs'))


@contextmanager
def runtime_tools_run(ctx):
    options = ctx.options
    role = caller_role(ctx.run_label)
    if ctx.run_label in {'main', 'self-implement', 'self-micro'}:
        role = 'engineer' if ctx.run_label == 'main' else 'self'
    if (ctx.backend._backend_name != 'pi' or ctx.usage_project_root is None
            or role is None or options.disable_tools or not options.working_dir
            or options.isolate_workdir):
        yield
        return
    root = Path(ctx.usage_project_root).resolve()
    skills = Path(os.environ.get('ARGUS_SKILL_PROJECT_SKILLS_DIR') or root / 'skills').resolve()
    writable = (role in {'engineer', 'manager', 'self'}
                and options.sandbox_mode != 'read-only' and role_skill_maintenance_enabled())
    service = RuntimeToolService(root, skills_root=skills, role=role,
                                 call_id=ctx.call_id, writable=writable)
    names = ['list_learned_tools', 'run_learned_tool']
    if writable:
        names += ['evolve_runtime', 'rollback_runtime']
    with CallBoundBridge(service.dispatch, env_prefix='ARGUS_PLUGIN_RUNTIME',
                         on_close=service.close) as bridge:
        ctx.options = replace(
            options,
            trusted_extensions=list(dict.fromkeys([*(options.trusted_extensions or []), EXTENSION])),
            trusted_tool_names=list(dict.fromkeys([*(options.trusted_tool_names or []), *names])),
            extension_env={**(options.extension_env or {}), **bridge.environment,
                           'ARGUS_PLUGIN_RUNTIME_WRITABLE': '1' if writable else '0'},
        )
        ctx.prompt += (
            '\n\nProject runtime tools: list_learned_tools and run_learned_tool reuse validated '
            'JSON transformations from this project. Consult them when the task needs repeated '
            'parsing, normalization or calculation; skip discovery when irrelevant. Read the '
            f'applicable Skill under {skills} and capability facts in '
            f'{root / ".autors/runtime/wiki"} before reuse. This is the same role turn and budget. '
            'These tools consume JSON values and return JSON; use existing file tools to read inputs '
            'and write results. They cannot open files, use network, run commands or create agents. '
        )
        if writable:
            ctx.prompt += (
                'When at least three actual tool results expose reusable execution friction, '
                'evolve_runtime can propose one project tool improvement. Cite two distinct '
                '[observation:ID] references, list the current revision first (0 for a new name), '
                'and supply Python def run(value), at least two distinct input/expected cases '
                'including a boundary case, a reusable Skill procedure and a Wiki capability/limits '
                'page with their evidence. The source API permits JSON values, basic builtins and '
                'explicit from math/re/json imports of pure functions; no general Python imports, '
                'reflection or I/O. Supply Markdown bodies without frontmatter. Improve an existing '
                'relevant name instead of making duplicates. Validation preserves the previous '
                'contract. A candidate remains private until run_learned_tool successfully uses it '
                'on this task; then its code, Skill and Wiki are published together for later turns. '
                'At most three proposals and one publication per call; do not spend turns evolving '
                'a tool that the current task does not need. A passing case or a successful use '
                'does not prove broad accuracy or performance; document counterexamples and limits. '
                'Use rollback_runtime with the current revision when fresh evidence invalidates '
                'an active tool. Continue the existing end-of-task Skill/Wiki maintenance; do not '
                'start another reviewer, agent, model request or background evolution loop. '
            )
        yield
