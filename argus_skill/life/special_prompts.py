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
``20-...``) controls precedence. An optional tiny frontmatter block may set
``scope: paper`` (or ``scope: nonpaper``); mission type comes from the resolved
vertical, never from objective keywords. The directory is operator-owned and
lives outside the repo so it never gets committed.
"""
from __future__ import annotations

import os
from pathlib import Path


def special_prompts_dir() -> Path:
    env = os.environ.get("ARGUS_SKILL_SPECIAL_PROMPTS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".argus-skill" / "special_prompts"


def _scoped_body(raw: str) -> tuple[str, str]:
    """Return ``(scope, body)`` from optional minimal frontmatter.

    Unknown/malformed metadata is treated as ``all`` so a typo never silently
    drops an operator directive.  This intentionally parses only ``scope``;
    pulling in a YAML dependency for two explicit mission classes would add
    complexity without value.
    """
    text = raw.strip()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "all", text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return "all", text
    scope = "all"
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "scope":
            candidate = value.strip().lower()
            if candidate in {"all", "paper", "nonpaper"}:
                scope = candidate
    return scope, "\n".join(lines[end + 1 :]).strip()


def load_special_prompts(
    *, paper_mission: bool | None = None,
) -> list[tuple[str, str]]:
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
            scope, body = _scoped_body(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if scope == "paper" and paper_mission is False:
            continue
        if scope == "nonpaper" and paper_mission is True:
            continue
        if body:
            out.append((path.stem, body))
    return out


def render_special_prompts_context(
    *, paper_mission: bool | None = None,
) -> str:
    """Render operator directives as a high-priority runtime context block.

    Returns ``""`` when no directives exist, so callers can concatenate
    unconditionally.
    """
    prompts = load_special_prompts(paper_mission=paper_mission)
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


def describe_special_prompt_gate() -> tuple[bool, str]:
    """Return ``(ok, message)`` for the launch-time special-prompt gate.

    ``ok`` is True when at least one trusted directive is loadable. When
    False, ``message`` is an actionable diagnostic that distinguishes
    "no directory / no files" from "files present but rejected by the
    trust check" (symlink / wrong owner / group-or-world-writable), so an
    operator hitting the umask-0664 pitfall gets exact ``chmod`` guidance.
    """
    directory = special_prompts_dir()
    if load_special_prompts():
        return True, ""
    if not directory.is_dir():
        return False, (
            f"no operator special prompts configured — create at least one "
            f"house-rules directive:\n"
            f"  mkdir -p {directory}\n"
            f"  printf 'Operational house rules for this box.\\n' > "
            f"{directory}/10-house-rules.md\n"
            f"  chmod 0644 {directory}/10-house-rules.md\n"
            f"(override the location with $ARGUS_SKILL_SPECIAL_PROMPTS_DIR)"
        )
    md_files = [p for p in directory.glob("*.md") if not p.is_symlink()]
    if not md_files:
        return False, (
            f"no operator special prompts found in {directory} — drop at least "
            f"one *.md directive there (mode 0644, owned by you)."
        )
    return False, (
        f"special prompts in {directory} were all rejected by the trust check. "
        f"Each *.md must be a regular file, owned by the directory owner, and "
        f"NOT group/world-writable. Fix with:\n"
        f"  chmod 0644 {directory}/*.md"
    )


def has_trusted_special_prompt() -> bool:
    """True when at least one trusted operator directive is loadable."""
    return bool(load_special_prompts())


__all__ = [
    "special_prompts_dir",
    "load_special_prompts",
    "render_special_prompts_context",
    "describe_special_prompt_gate",
    "has_trusted_special_prompt",
]
