"""Special prompts — operator-authored, machine-specific standing directives.

The framework hardcodes general, portable guidance (role contracts, pipeline
stages, skills). But every physical box has its own *house rules* that the
operator — not the framework — owns: "this machine reclaims idle GPUs, free
the keep-alive before training", "scratch lives on /mnt/fast", "never touch
/data/raw", and so on. Baking those into the package would be wrong; they are
deployment facts, not framework behaviour.

A **special prompt** is exactly that channel. The operator drops Markdown
files into ``~/.argus-skill/special_prompts/`` and their contents are injected
verbatim, high in every agent's runtime context, ahead of general guidance.
They are standing instructions: the agent treats them as authoritative house
rules and follows them like a human operator would.

Files are read in sorted filename order, so a numeric prefix (``10-...``,
``20-...``) controls precedence. The directory is operator-owned and lives
outside the repo so it never gets committed.
"""
from __future__ import annotations

import os
from pathlib import Path


def special_prompts_dir() -> Path:
    env = os.environ.get("ARGUS_SKILL_SPECIAL_PROMPTS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".argus-skill" / "special_prompts"


def load_special_prompts() -> list[tuple[str, str]]:
    """Return ``[(name, body)]`` for each trusted ``*.md`` directive.

    Sorted by filename. Empty/whitespace-only files are skipped. For safety —
    these are injected as authoritative house rules — a file is REJECTED (and
    silently skipped) if it is a symlink, is group/world-writable, or is not
    owned by the directory owner. That keeps the channel operator-controlled:
    a project repo or the agent itself cannot smuggle in directives.
    """
    directory = special_prompts_dir()
    if not directory.is_dir():
        return []
    try:
        dir_uid = directory.stat().st_uid
    except OSError:
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.md")):
        try:
            if path.is_symlink():
                continue
            st = path.stat()
            if st.st_uid != dir_uid:
                continue  # not owned by the operator
            if st.st_mode & 0o022:
                continue  # group/world-writable -> untrusted
            body = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if body:
            out.append((path.stem, body))
    return out


def render_special_prompts_context() -> str:
    """Render operator directives as a high-priority runtime context block.

    Returns ``""`` when no directives exist, so callers can concatenate
    unconditionally.
    """
    prompts = load_special_prompts()
    if not prompts:
        return ""
    parts = [
        "## Operator Directives (special prompts)",
        "Standing, machine-specific operational house rules set by the human "
        "operator of this box. Treat them as authoritative for HOW to operate "
        "this machine (paths, GPUs, schedulers, quotas): when they conflict "
        "with general workflow guidance, follow the directive. They do NOT "
        "override your safety, security, or correctness obligations. Apply "
        "them as a careful human running this box would.",
    ]
    for name, body in prompts:
        parts.append(f"### {name}\n{body}")
    return "\n\n".join(parts)


__all__ = [
    "special_prompts_dir",
    "load_special_prompts",
    "render_special_prompts_context",
]
