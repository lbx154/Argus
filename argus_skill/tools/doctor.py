"""Self-diagnosis for argus-skill — the ``/doctor`` command.

Answers the one question a stuck user actually has: *why is nothing
executing my backlog, and what do I type to fix it?* This is the gap a real
user hit on 2026-06-26 — a bare ``argus-skill`` opened a fresh empty session,
the daemon auto-spawn failed silently (gpt-5.5 backend 429 / vault preflight),
and the REPL gave no path forward.

The module is a thin,领域无关 diagnostic harness: it reuses the existing
status / lock / vault / preflight primitives and reports their state. It makes
**no** research judgement and **no** network call by default — every check is
fail-soft (an import error or exception becomes a failed :class:`Check` with a
concrete fix, never an exception into the REPL).

Public surface::

    Check(name, ok, detail, fix)
    run_diagnostics(project_root, *, global_root=None, probe=None) -> list[Check]
    render_report(checks, theme=None) -> str

Each :class:`Check` carries a concrete, copy-pasteable ``fix`` string; the
rendered report ends with the single highest-priority recommended fix so the
operator never has to guess which line matters.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["Check", "run_diagnostics", "render_report"]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """One diagnostic line.

    ``ok`` is the pass/fail bit; ``detail`` is a human-readable description of
    the observed state; ``fix`` is a concrete, copy-pasteable action that
    resolves the problem (empty string when ``ok`` — nothing to fix).
    """

    name: str
    ok: bool
    detail: str
    fix: str = ""


# Recommendation priority: when several checks fail, surface the *root cause*
# (a missing backend / unconfigured-or-unreachable model API is why daemon
# auto-spawn fails) before the visible *symptom* (no daemon running). Lower
# number == recommended first. Unknown names sort last.
_RECO_PRIORITY = {
    "backend preflight": 0,
    "model API capability": 1,
    "daemon": 2,
    "lock sanity": 3,
    "empty session": 4,
}


# ---------------------------------------------------------------------------
# Individual checks — each fully fail-soft (return a Check, never raise)
# ---------------------------------------------------------------------------

def _check_daemon(project_root: Path) -> Check:
    """(1) Is a daemon alive for *this* project? It is the sole executor."""
    from ..daemon.life_worker import read_daemon_status

    st = read_daemon_status(project_root)
    if getattr(st, "alive", False) and getattr(st, "pid", None):
        backend = getattr(st, "backend", None) or "?"
        uptime = getattr(st, "uptime_seconds", None)
        tail = f", up {uptime:.0f}s" if isinstance(uptime, (int, float)) else ""
        return Check(
            "daemon",
            True,
            f"running (pid {st.pid}, backend={backend}{tail}) — draining backlog",
            "",
        )
    return Check(
        "daemon",
        False,
        "no daemon is running for this project — queued backlog will NOT execute",
        "run: argus-skill --daemon",
    )


def _check_locks(project_root: Path) -> Check:
    """(2) Lock sanity: flag stale ``daemon.pid`` / ``repl.pid`` (dead pid).

    A live lock (this REPL, a running daemon) is never flagged. A stale lock
    is reclaimable — flock liveness is authoritative — but it is litter worth
    surfacing, and an orphan ``daemon.pid`` confuses a casual ``ps``.
    """
    from ..core.daemon_lock import is_pid_running, read_daemon_pid

    stale: list[tuple[str, int]] = []
    for name in ("daemon.pid", "repl.pid"):
        path = project_root / name
        pid = read_daemon_pid(path)
        if pid is None:
            continue  # absent / empty / garbage -> nothing to reclaim
        if not is_pid_running(pid):
            stale.append((name, pid))
    if not stale:
        return Check("lock sanity", True, "no stale lock files", "")
    desc = ", ".join(f"{name} (pid {pid} not running)" for name, pid in stale)
    rm = " ".join(str(project_root / name) for name, _ in stale)
    return Check(
        "lock sanity",
        False,
        f"stale lock file(s): {desc}",
        f"remove the stale lock(s): rm {rm}",
    )


def _check_model_api(probe: Callable[..., Any] | None) -> Check:
    """(3) Is the model-API / vault capability usable (and reachable)?

    Offline by default: verifies the engineer/reviewer/text routes are
    configured + usable in the vault (no network). When a ``probe`` callable
    is injected, additionally checks reachability via
    :func:`argus_skill.core.vault_preflight.check_routes` so a 429 / dead
    deployment surfaces with a switch-backend fix. Import failure -> a failed
    Check with a reinstall fix, never an exception.
    """
    try:
        from ..tools.capability_vault import default_vault_path, load_model_api_route
    except Exception as exc:  # noqa: BLE001 — fail-soft, never raise into REPL
        return Check(
            "model API capability",
            False,
            f"capability_vault not importable ({type(exc).__name__})",
            "reinstall argus-skill (the bundled capability_vault module is missing)",
        )

    missing: list[str] = []
    for name in ("engineer", "reviewer", "text"):
        try:
            route = load_model_api_route(name)
        except Exception:  # noqa: BLE001
            route = None
        if not (route is not None and getattr(route, "usable", False)):
            missing.append(name)

    try:
        vault = default_vault_path()
    except Exception:  # noqa: BLE001
        vault = None
    vault_note = f" (vault {vault})" if vault is not None else ""

    if missing:
        return Check(
            "model API capability",
            False,
            f"route(s) not configured/usable: {', '.join(missing)}{vault_note}",
            "configure the model API: python -m argus_skill.tools.capability_vault "
            "init-model-api  (or export OPENAI_API_KEY + OPENAI_BASE_URL)",
        )

    if probe is None:
        # Offline-only: routes are configured. Reachability (and thus a live
        # 429) is intentionally NOT probed here to keep /doctor instant and
        # network-free; the daemon's own vault preflight probes at start.
        return Check(
            "model API capability",
            True,
            f"engineer/reviewer/text routes configured{vault_note}; "
            "reachability not probed (offline check)",
            "",
        )

    # Reachability probe requested — delegate to the preflight checker so we
    # reuse the exact route loader + report shape the daemon trusts.
    try:
        from ..core.vault_preflight import check_routes

        report = check_routes(probe=probe)
    except Exception as exc:  # noqa: BLE001
        return Check(
            "model API capability",
            False,
            f"reachability probe could not run ({type(exc).__name__}: {exc})",
            "retry, or set ARGUS_SKILL_SKIP_VAULT_PREFLIGHT=1 to bypass the probe",
        )

    if getattr(report, "ok", False):
        return Check(
            "model API capability", True, "required routes configured + reachable", ""
        )

    fails = list(getattr(report, "required_failures", []))
    detail = "; ".join(
        f"{c.name}: HTTP {c.http_status} {str(c.error)[:80]}".strip() for c in fails
    ) or "one or more required routes unreachable"
    rate_limited = any(getattr(c, "http_status", None) == 429 for c in fails)
    if rate_limited:
        fix = (
            "gpt-5.5 backend rate-limited (429) — wait and retry, or switch "
            "backend with /backend memory"
        )
    else:
        fix = (
            "fix the vault route(s) above (check deployment name / base_url), "
            "or wait if the backend is rate-limited"
        )
    return Check("model API capability", False, f"unreachable: {detail}", fix)


def _check_backend_preflight() -> Check:
    """(4) Reuse ``_codex_preflight_warning`` — can the codex backend run?

    The warning already embeds its own fix (install codex / reinstall). On
    import failure we still return a failed Check rather than raising.
    """
    try:
        from ..apps._runtime import _codex_preflight_warning
    except Exception as exc:  # noqa: BLE001
        return Check(
            "backend preflight",
            False,
            f"could not load backend preflight ({type(exc).__name__})",
            "reinstall argus-skill",
        )
    try:
        warning = _codex_preflight_warning()
    except Exception as exc:  # noqa: BLE001
        return Check(
            "backend preflight",
            False,
            f"backend preflight raised ({type(exc).__name__}: {exc})",
            "reinstall argus-skill",
        )
    if warning:
        return Check("backend preflight", False, f"codex backend cannot run: {warning}", warning)
    return Check(
        "backend preflight",
        True,
        "codex backend runnable (bundled agent_cli + codex binary present)",
        "",
    )


def _continuous_objective(project_root: Path) -> str:
    try:
        from ..daemon.life_worker import read_continuous_config

        _enabled, objective = read_continuous_config(project_root)
        return str(objective or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _has_real_activity(project_root: Path) -> bool:
    for name in ("backlog.jsonl", "events.jsonl"):
        path = project_root / name
        try:
            if path.exists() and path.stat().st_size > 2:
                return True
        except OSError:
            continue
    return False


def _count_global_empty_sessions(global_root: Path) -> int:
    """Best-effort count of empty, not-live project shells under ``global_root``.

    Mirrors the project-GC notion of "empty" (no backlog/events) and "live"
    (a running daemon/repl lock) so the number lines up with what ``--gc``
    would consider. Fully fail-soft.
    """
    root = Path(global_root) / "projects"
    if not root.exists():
        return 0
    try:
        from ..core.daemon_lock import is_pid_running, read_daemon_pid
    except Exception:  # noqa: BLE001
        return 0
    count = 0
    try:
        children = list(root.iterdir())
    except OSError:
        return 0
    for child in children:
        try:
            if not child.is_dir():
                continue
            live = False
            for lock_file in ("daemon.pid", "repl.pid"):
                pid = read_daemon_pid(child / lock_file)
                if pid is not None and is_pid_running(pid):
                    live = True
                    break
            if live:
                continue
            if not _has_real_activity(child):
                count += 1
        except OSError:
            continue
    return count


def _check_empty_session(
    project_root: Path,
    global_root: Path | None,
    *,
    daemon_alive: bool,
) -> Check:
    """(5) Is this project an empty / littered session?

    A bare ``argus-skill`` launch creates a fresh session dir; if it is never
    used it becomes one of the empty shells that accumulate under
    ``~/.argus-skill/projects`` (observed: 69 of 72 empty). Not a problem when
    a daemon is live, an objective is set, or any backlog/events exist.
    """
    if daemon_alive or _has_real_activity(project_root) or _continuous_objective(project_root):
        return Check(
            "empty session",
            True,
            "project has work (backlog/events/objective or a live daemon)",
            "",
        )
    extra = ""
    if global_root is not None:
        try:
            n = _count_global_empty_sessions(global_root)
            if n:
                extra = f"; {n} empty session shell(s) under {Path(global_root) / 'projects'}"
        except Exception:  # noqa: BLE001
            extra = ""
    return Check(
        "empty session",
        False,
        f"this is an empty session — no backlog, no events yet{extra}",
        "run: argus-skill --gc  (sweep empty shells)  ·  or relaunch with "
        "--resume / --continue to attach to existing work",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_diagnostics(
    project_root: Path | str,
    *,
    global_root: Path | None = None,
    probe: Callable[..., Any] | None = None,
) -> list[Check]:
    """Run every diagnostic and return the ordered list of :class:`Check`.

    ``project_root`` is the per-project life dir (``mem.project.root`` in the
    REPL) that owns the daemon/repl pid locks and event log. ``global_root``
    (``~/.argus-skill`` by default in callers) enables the empty-shell roll-up.
    ``probe`` is an optional reachability probe — when omitted the model-API
    check is offline (no network), which is the safe default for an
    interactive ``/doctor``.

    Every check is fail-soft: a check that raises is converted into a failed
    Check with a reinstall fix, so this function never propagates an exception
    into the REPL.
    """
    root = Path(project_root).expanduser()

    checks: list[Check] = []

    def _run(name: str, fn: Callable[[], Check]) -> Check:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — turn any failure into a Check
            return Check(
                name,
                False,
                f"diagnostic raised ({type(exc).__name__}: {exc})",
                "reinstall argus-skill or report this /doctor failure",
            )

    daemon_check = _run("daemon", lambda: _check_daemon(root))
    checks.append(daemon_check)
    checks.append(_run("lock sanity", lambda: _check_locks(root)))
    checks.append(_run("model API capability", lambda: _check_model_api(probe)))
    checks.append(_run("backend preflight", _check_backend_preflight))
    checks.append(
        _run(
            "empty session",
            lambda: _check_empty_session(
                root, global_root, daemon_alive=daemon_check.ok
            ),
        )
    )
    return checks


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _paint(theme: Any, method: str, text: str) -> str:
    """Apply ``theme.<method>`` if available; plain text otherwise (fail-soft)."""
    if theme is None:
        return text
    fn = getattr(theme, method, None)
    if not callable(fn):
        return text
    try:
        return fn(text)
    except Exception:  # noqa: BLE001
        return text


def _recommended_fix(checks: list[Check]) -> str:
    """Highest-priority actionable fix among failing checks (root cause first)."""
    candidates = [c for c in checks if not c.ok and c.fix]
    if not candidates:
        return ""
    candidates.sort(
        key=lambda c: (_RECO_PRIORITY.get(c.name, len(_RECO_PRIORITY)), c.name)
    )
    return candidates[0].fix


def render_report(checks: list[Check], theme: Any = None) -> str:
    """Render a scannable check report ending with the top recommended fix.

    ``theme`` is an optional :class:`argus_skill.cli.theme.Theme`-shaped object
    (any object exposing ``green``/``red``/``yellow``/``gray``/``bold`` text
    methods). ``None`` produces plain, un-colored text suitable for tests and
    non-TTY output.
    """
    lines: list[str] = [_paint(theme, "bold", "argus-skill doctor — self-diagnosis")]
    n_fail = sum(1 for c in checks if not c.ok)
    summary = (
        "all checks passed"
        if n_fail == 0
        else f"{n_fail} issue(s) found"
    )
    lines.append(_paint(theme, "gray", summary))
    lines.append("")

    for check in checks:
        glyph = "✓" if check.ok else "✗"
        head = f"{glyph} {check.name:<22} {check.detail}"
        color = "green" if check.ok else "red"
        lines.append(_paint(theme, color, head))
        if not check.ok and check.fix:
            lines.append(_paint(theme, "gray", f"    ↳ fix: {check.fix}"))

    lines.append("")
    rec = _recommended_fix(checks)
    if rec:
        lines.append(_paint(theme, "yellow", f"→ recommended: {rec}"))
    else:
        lines.append(_paint(theme, "green", "→ all checks passed — nothing to fix."))
    return "\n".join(lines)
