from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Literal, get_args

RunnerBackend = Literal["codex", "claude", "copilot", "opencode", "pi", "grok", "qoder", "dsh"]

#: Every backend the runtime can drive, derived from the type above so the two
#: can never disagree. The CLI's `--backend` choices, the readiness check and
#: the operator control-knob help all render from this. They used to hold their
#: own copies and the knob help had drifted to five of the eight, so a fully
#: supported backend was one a user could not learn existed.
SUPPORTED_BACKENDS: tuple[str, ...] = get_args(RunnerBackend)

BACKEND_CODEX: RunnerBackend = "codex"
BACKEND_CLAUDE: RunnerBackend = "claude"
BACKEND_COPILOT: RunnerBackend = "copilot"
BACKEND_OPENCODE: RunnerBackend = "opencode"
BACKEND_PI: RunnerBackend = "pi"
BACKEND_GROK: RunnerBackend = "grok"
BACKEND_QODER: RunnerBackend = "qoder"
BACKEND_DSH: RunnerBackend = "dsh"
DEFAULT_RUNNER_BACKEND: RunnerBackend = BACKEND_CODEX

#: The in-process orchestration/test backend. It drives no external CLI and
#: talks to no provider, so it is deliberately NOT in ``SUPPORTED_BACKENDS`` and
#: ``normalize_runner_backend`` rejects it. It is nonetheless a first-class
#: value elsewhere — ``LifeWorkerConfig.backend`` is documented as
#: ``"codex" | "memory"`` — so call sites that can receive it must branch on it
#: BEFORE normalizing. Named here so those branches are greppable instead of
#: each spelling the literal.
BACKEND_MEMORY: str = "memory"

# Qoder's official CLI (``qodercli``) is a Claude Code fork: it accepts the same
# headless argv (``-p --output-format stream-json --model … --permission-mode …
# --resume …``) and emits the same stream-json event schema. So ``qoder`` reuses
# the ``claude`` command builder, event consumer, sandbox policy, and prompt
# delivery verbatim. This family set is the single source of truth for "treat it
# like claude" so those call sites never drift apart.
CLAUDE_FAMILY: frozenset[str] = frozenset({BACKEND_CLAUDE, BACKEND_QODER})


#: Historical spelling accepted for ``opencode``; kept so existing configs and
#: the persisted knob store keep resolving after the strictening below.
_BACKEND_ALIASES: dict[str, RunnerBackend] = {"opencod": BACKEND_OPENCODE}

_SUPPORTED_BACKEND_SET: frozenset[str] = frozenset(SUPPORTED_BACKENDS)


def normalize_runner_backend(raw: str | None) -> RunnerBackend:
    """Canonicalize a backend name.

    Empty/``None`` means "not configured" and still yields
    ``DEFAULT_RUNNER_BACKEND`` — that is a real state with a real default, and
    several callers depend on it.

    A NON-empty value that names no known backend now raises ``ValueError``.
    It used to fall through to codex, so a typo'd ``ARGUS_SKILL_*_BACKEND``
    ("copilto") produced a working-looking run against an entirely different
    provider, with the operator's actual choice never mentioned anywhere.

    Callers that must tolerate an unknown value (display paths that echo back
    whatever the operator typed) already guard this call — see
    ``core.backend_readiness.resolve_backend_profile`` and
    ``core.role_config._normalize_backend``.
    """
    value = str(raw or "").strip().lower()
    if not value:
        return DEFAULT_RUNNER_BACKEND
    alias = _BACKEND_ALIASES.get(value)
    if alias is not None:
        return alias
    if value in _SUPPORTED_BACKEND_SET:
        # SUPPORTED_BACKENDS holds the canonical lowercase spellings, so a
        # membership hit IS the canonical name.
        return value  # type: ignore[return-value]
    raise ValueError(
        f"unknown agent-CLI backend {raw!r}; supported backends are: "
        f"{', '.join(SUPPORTED_BACKENDS)}"
    )


def default_runner_bin(backend: RunnerBackend) -> str:
    if backend == BACKEND_CLAUDE:
        return "claude"
    if backend == BACKEND_COPILOT:
        return "copilot"
    if backend == BACKEND_OPENCODE:
        return "opencode"
    if backend == BACKEND_PI:
        return "pi"
    if backend == BACKEND_GROK:
        return "grok"
    if backend == BACKEND_QODER:
        return "qodercli"
    if backend == BACKEND_DSH:
        return "dsh"
    return "codex"


def _resolve_explicit_candidate(candidate: Path) -> str | None:
    # Test fixtures, portable shims, and some user-local launchers are valid
    # extensionless files even on Windows. ``shutil.which`` applies PATHEXT and
    # can miss those exact candidates, so honor an explicitly located file
    # before probing sibling .exe/.cmd variants.
    try:
        is_file = candidate.is_file()
    except OSError:
        # PATH can contain an inaccessible launcher owned by another user.
        # Treat that entry as unavailable instead of breaking discovery for
        # every backend that appears later on PATH.
        return None
    if is_file and (os.name == "nt" or os.access(candidate, os.X_OK)):
        return str(candidate)
    resolved = shutil.which(str(candidate))
    if resolved:
        return resolved
    if os.name != "nt" or candidate.suffix:
        return None
    extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
    wanted = {f"{candidate.name}{extension}".casefold() for extension in extensions if extension}
    try:
        for entry in candidate.parent.iterdir():
            if entry.is_file() and entry.name.casefold() in wanted:
                return str(entry)
    except OSError:
        return None
    return None


def resolve_runner_bin(
    backend: RunnerBackend | str | None,
    configured: str | None = None,
) -> str | None:
    """Resolve a CLI independently of service-manager PATH omissions."""
    chosen = normalize_runner_backend(backend)
    requested = str(configured or default_runner_bin(chosen)).strip()
    if not requested:
        return None
    expanded = str(Path(requested).expanduser())
    resolved = shutil.which(expanded)
    if resolved:
        return resolved
    if Path(expanded).parent != Path("."):
        return None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        resolved = _resolve_explicit_candidate(Path(entry) / expanded)
        if resolved:
            return resolved
    if chosen == BACKEND_OPENCODE:
        opencode_home = Path.home() / ".opencode" / "bin" / expanded
        resolved = _resolve_explicit_candidate(opencode_home)
        if resolved:
            return resolved
    if chosen == BACKEND_DSH:
        # dsh is installed through the nvm-managed Node toolchain, whose bin
        # directory is absent from non-interactive PATHs (the daemon may be
        # started from one). Probe the per-version nvm bins newest-first.
        nvm_versions = Path.home() / ".nvm" / "versions" / "node"
        if nvm_versions.is_dir():
            for version_dir in sorted(nvm_versions.iterdir(), reverse=True):
                candidate = version_dir / "bin" / expanded
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
    user_local = Path.home() / ".local" / "bin" / expanded
    resolved = _resolve_explicit_candidate(user_local)
    if resolved:
        return resolved
    return None


def resolve_available_runner(
    backend: RunnerBackend | str | None,
    configured: str | None = None,
) -> tuple[RunnerBackend, str]:
    """Resolve the requested CLI, falling back only when Codex is absent."""
    raw = str(backend or "").strip().lower()
    chosen = normalize_runner_backend(backend)
    resolved = resolve_runner_bin(chosen, configured)
    if resolved:
        return chosen, resolved
    if chosen == BACKEND_CODEX and raw in ("", BACKEND_CODEX):
        copilot = resolve_runner_bin(BACKEND_COPILOT)
        if copilot:
            return BACKEND_COPILOT, copilot
    return chosen, str(configured or default_runner_bin(chosen)).strip()
