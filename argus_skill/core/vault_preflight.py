"""Pre-flight check for the model_api vault routes.

Validates that each configured route in
``~/.argus-skill/capabilities/model_api.json`` points at a real,
reachable model deployment **before** the daemon starts pushing
work to it. Catches the class of bugs where the vault references
``gpt-5.5-mini`` but only ``gpt-5.5`` actually exists on Azure
(observed 2026-06-01: 47 min / $2.50 wasted in a doom loop because
the daemon couldn't tell its own config was wrong).

Pure plumbing per edit-principle skills/04 — this is a **structural** check
("does the endpoint respond with non-404 to a minimal probe?"),
not a quality judgment.

Design:

* Network call goes through an injectable ``probe`` function so
  tests can stub it without touching the network.
* Default probe sends a 1-token request to the route's base_url +
  model. We treat any 2xx response as "route is alive". 4xx (esp.
  404 deployment-not-found, 401 auth-fail) → route is dead.
* Each route is checked independently; one bad route surfaces the
  bad route, doesn't cascade.
* On any failure, exit 2 with a red-flag message naming the route(s)
  + the specific error. Daemon startup wiring should refuse to come
  up if exit != 0.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# Routes the daemon actually uses. Any route in vault but NOT in this
# list is informational only — we don't fail preflight on it.
DEFAULT_REQUIRED_ROUTES: tuple[str, ...] = (
    "engineer",
    "reviewer",
    "text",
)
# Optional routes — checked if vault has them, but absence is OK.
DEFAULT_OPTIONAL_ROUTES: tuple[str, ...] = ("image", "image_review")

# Default network timeout per probe. Short — preflight should be fast.
DEFAULT_PROBE_TIMEOUT_S = 10.0


@dataclass
class RouteCheck:
    """Result of probing one route."""

    name: str
    required: bool
    skipped: bool = False
    skip_reason: str = ""
    ok: bool = False
    http_status: int | None = None
    error: str = ""
    model: str = ""
    base_url: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "required": self.required,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "ok": self.ok,
            "http_status": self.http_status,
            "error": self.error,
            "model": self.model,
            "base_url": self.base_url,
        }


@dataclass
class PreflightReport:
    """Aggregate preflight outcome."""

    checks: list[RouteCheck] = field(default_factory=list)

    @property
    def required_failures(self) -> list[RouteCheck]:
        # A required route that is unusable / missing / corrupt is marked
        # skipped with ok=False by check_routes — that is a FAILURE, not a pass.
        # (Only the optional image / wire_api=images skips set ok=True; they are
        # required=False and excluded anyway.) Previously excluding `skipped`
        # here let a missing/corrupt required route boot the 7x24 daemon straight
        # into the doom-loop this preflight exists to prevent.
        return [c for c in self.checks if c.required and not c.ok]

    @property
    def ok(self) -> bool:
        """True iff every REQUIRED route passed (a config-present optional skip
        sets ok=True). A required route that is unusable / missing / corrupt is a
        failure; optional routes that fail don't fail the preflight."""
        return not self.required_failures

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "required_failure_count": len(self.required_failures),
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Probe — sends a minimal request and reports 2xx / non-2xx
# ---------------------------------------------------------------------------


ProbeResult = tuple[bool, int | None, str]
"""``(ok, http_status, error_message)``. ok=True for 2xx; error_message
empty when ok."""


def default_probe(
    base_url: str,
    api_key: str,
    model: str,
    wire_api: str,
    *,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> ProbeResult:
    """Send a tiny request to the route and report whether it answered.

    Treats any 2xx as alive. 4xx / 5xx / network error → not alive.

    Network call. Tests inject their own probe via DI; this default
    is only used in real preflight runs.
    """
    if not base_url.endswith("/"):
        base_url = base_url + "/"
    # Use the lightest endpoint available on the responses-API route.
    # A 1-token completion is the most universal probe; the body is
    # tolerant to extra fields (Azure /v1/responses ignores unknown).
    url = f"{base_url}responses"
    # max_output_tokens=16 is the smallest Azure accepts (min 16 per
    # OpenAI Responses API contract). We want the cheapest possible
    # probe but Azure 400s anything < 16.
    body = json.dumps({
        "model": model,
        "input": [{"role": "user", "content": "ping"}],
        "max_output_tokens": 16,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "api-key": api_key,  # Azure-style header
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            return (200 <= status < 300, status, "")
    except urllib.error.HTTPError as exc:
        # Read the error body for a useful message; some endpoints
        # return JSON with a `code` / `message` field.
        try:
            payload = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            payload = ""
        snippet = payload[:200].replace("\n", " ").strip()
        return (False, exc.code, f"HTTP {exc.code}: {snippet}")
    except urllib.error.URLError as exc:
        return (False, None, f"URL error: {exc.reason}")
    except Exception as exc:  # noqa: BLE001
        return (False, None, f"{type(exc).__name__}: {exc}")


def check_routes(
    *,
    required: Iterable[str] = DEFAULT_REQUIRED_ROUTES,
    optional: Iterable[str] = DEFAULT_OPTIONAL_ROUTES,
    probe: Callable[..., ProbeResult] | None = None,
    route_loader: Callable[[str], Any] | None = None,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> PreflightReport:
    """Run the preflight check.

    ``probe`` and ``route_loader`` are DI hooks so tests can stub them.
    Defaults call into capability_vault.load_model_api_route + the
    network probe.
    """
    if route_loader is None:
        from ..tools.capability_vault import load_model_api_route

        def route_loader(name: str) -> Any:
            return load_model_api_route(name)

    if probe is None:
        def probe(base_url: str, api_key: str, model: str, wire_api: str,
                  *, timeout_s: float = timeout_s) -> ProbeResult:
            return default_probe(
                base_url, api_key, model, wire_api, timeout_s=timeout_s,
            )

    report = PreflightReport()
    seen: set[str] = set()
    for name in list(required) + list(optional):
        if name in seen:
            continue
        seen.add(name)
        is_required = name in required
        route = route_loader(name)
        check = RouteCheck(
            name=name, required=is_required,
            model=getattr(route, "model", "") if route else "",
            base_url=getattr(route, "base_url", "") if route else "",
        )
        if route is None or not getattr(route, "usable", False):
            check.skipped = True
            check.skip_reason = "route not configured in vault"
            report.checks.append(check)
            continue
        wire_api = getattr(route, "wire_api", "responses")
        # image / image_review use a different endpoint shape; skip
        # network probe for them to avoid false negatives. They get
        # marked ok=True on config-present, which is a weaker signal
        # but the right cost/benefit (image-gen 404s are rare and
        # rarely a daemon-blocker).
        if wire_api in ("images",) or name in ("image", "image_review"):
            check.skipped = True
            check.skip_reason = f"wire_api={wire_api!r} not probed (config-present only)"
            check.ok = True  # treat as ok for the optional roll-up
            report.checks.append(check)
            continue
        try:
            ok, status, err = probe(
                route.base_url, route.api_key, route.model, wire_api,
                timeout_s=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            ok, status, err = (False, None, f"probe raised {type(exc).__name__}: {exc}")
        check.ok = ok
        check.http_status = status
        check.error = err
        report.checks.append(check)
    return report


def format_report(report: PreflightReport) -> str:
    lines: list[str] = []
    lines.append("argus-skill vault pre-flight")
    for c in report.checks:
        if c.skipped:
            mark = "⏭" if c.ok else "❔"
            lines.append(
                f"  {mark} {c.name:14s} skipped ({c.skip_reason})"
            )
            continue
        if c.ok:
            mark = "✅"
            extra = f"HTTP {c.http_status}"
        else:
            mark = "❌"
            extra = c.error
        lines.append(
            f"  {mark} {c.name:14s} model={c.model!r:30s} {extra}"
        )
    if report.ok:
        lines.append("OK — all required routes probe successfully.")
    else:
        lines.append(
            f"FAIL — {len(report.required_failures)} required route(s) "
            f"not reachable; daemon will not be started."
        )
        lines.append("Likely fixes:")
        for c in report.required_failures:
            if c.http_status == 404:
                lines.append(
                    f"  - {c.name}: deployment {c.model!r} not found at "
                    f"{c.base_url}. Check the deployment name in your "
                    f"Azure portal vs vault."
                )
            elif c.http_status == 401:
                lines.append(
                    f"  - {c.name}: 401 unauthorized. The api_key in "
                    f"vault is missing/rotated/wrong for this base_url."
                )
            else:
                lines.append(f"  - {c.name}: {c.error}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--required",
        nargs="*",
        default=list(DEFAULT_REQUIRED_ROUTES),
        help="routes that must succeed (default: engineer reviewer text)",
    )
    parser.add_argument(
        "--optional",
        nargs="*",
        default=list(DEFAULT_OPTIONAL_ROUTES),
        help="routes probed if present but not required",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PROBE_TIMEOUT_S,
        help="probe timeout in seconds (default 10)",
    )
    args = parser.parse_args(argv)

    report = check_routes(
        required=args.required,
        optional=args.optional,
        timeout_s=args.timeout,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report))
    return 0 if report.ok else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
