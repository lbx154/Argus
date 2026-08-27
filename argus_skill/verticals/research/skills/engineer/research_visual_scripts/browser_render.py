"""Render one local browser figure to SVG, PNG, or PDF with Playwright."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_svg(svg: str) -> None:
    root = ElementTree.fromstring(svg)
    if not root.tag.endswith("svg"):
        raise ValueError("selected browser figure did not produce an SVG root")
    if not root.get("viewBox") and not (root.get("width") and root.get("height")):
        raise ValueError("SVG needs a viewBox or explicit width and height")
    for element in root.iter():
        if element.tag.endswith("script"):
            raise ValueError("standalone SVG must not contain scripts")
        for key in ("href", "src", "{http://www.w3.org/1999/xlink}href"):
            value = str(element.get(key) or "").strip()
            if value and not value.startswith(("#", "data:")):
                raise ValueError(f"SVG contains an external dependency: {value}")
        css_text = " ".join(
            [
                str(element.get("style") or ""),
                str(element.text or "") if element.tag.endswith("style") else "",
            ]
        )
        if "@import" in css_text:
            raise ValueError("SVG contains an external CSS import")
        for match in re.finditer(r"url\(([^)]+)\)", css_text):
            value = match.group(1).strip().strip("\"'")
            if value and not value.startswith(("#", "data:")):
                raise ValueError(f"SVG CSS contains an external dependency: {value}")


def _local_url(raw: str) -> str:
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return candidate.resolve().as_uri()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("input must be a local file or localhost URL")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("remote figure URLs are not reproducible; use local assets")
    return raw


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="local HTML path or localhost URL")
    parser.add_argument("--selector", default="[data-figure-root]")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--device-scale-factor", type=float, default=2.0)
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument(
        "--browser-channel",
        default="",
        help="Playwright browser channel such as chrome; empty uses bundled Chromium",
    )
    parser.add_argument(
        "--timeout-ms", type=int, default=0,
        help="explicit Playwright timeout; default 0 waits indefinitely",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.width < 1 or args.height < 1:
        raise ValueError("width and height must be positive")
    output = args.output.resolve()
    suffix = output.suffix.lower()
    if suffix not in {".svg", ".png", ".pdf"}:
        raise ValueError("output extension must be .svg, .png, or .pdf")
    url = _local_url(args.input)
    input_url = urlparse(url)
    allowed_origin = (
        f"{input_url.scheme}://{input_url.netloc}"
        if input_url.scheme in {"http", "https"}
        else None
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "browser rendering requires `pip install playwright` and "
            "`python -m playwright install chromium` in the project environment"
        ) from exc

    console_errors: list[str] = []
    page_errors: list[str] = []
    blocked_requests: list[str] = []
    blocked_websockets: list[str] = []
    blocked_workers: list[str] = []
    blocked_realtime: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            channel=args.browser_channel or None,
            args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
        )
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.device_scale_factor,
            locale=args.locale,
            timezone_id=args.timezone,
            service_workers="block",
        )

        def route_request(route) -> None:  # noqa: ANN001
            parsed = urlparse(route.request.url)
            request_origin = (
                f"{parsed.scheme}://{parsed.netloc}"
                if parsed.scheme in {"http", "https"}
                else None
            )
            if request_origin is not None and request_origin != allowed_origin:
                blocked_requests.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        context.route("**/*", route_request)
        context.add_init_script(
            """
            window.__ARGUS_BLOCKED_WEBSOCKETS__ = [];
            window.__ARGUS_BLOCKED_WORKERS__ = [];
            window.__ARGUS_BLOCKED_REALTIME__ = [];
            window.WebSocket = class BlockedWebSocket {
              constructor(url) {
                window.__ARGUS_BLOCKED_WEBSOCKETS__.push(String(url));
                throw new Error(`WebSocket blocked in research figure: ${url}`);
              }
            };
            window.Worker = class BlockedWorker {
              constructor(url) {
                window.__ARGUS_BLOCKED_WORKERS__.push(String(url));
                throw new Error(`Worker blocked in research figure: ${url}`);
              }
            };
            window.SharedWorker = window.Worker;
            window.RTCPeerConnection = class BlockedRTCPeerConnection {
              constructor(configuration) {
                window.__ARGUS_BLOCKED_REALTIME__.push(
                  JSON.stringify(configuration || {})
                );
                throw new Error('WebRTC blocked in research figure');
              }
            };
            window.webkitRTCPeerConnection = window.RTCPeerConnection;
            """
        )
        page = context.new_page()
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text)
                if message.type == "error"
                else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(url, wait_until="load", timeout=args.timeout_ms)
        page.wait_for_selector(args.selector, state="visible", timeout=args.timeout_ms)
        page.evaluate("document.fonts.ready")
        page.wait_for_function(
            """selector => {
              const root = document.querySelector(selector);
              return root && root.getAttribute('data-figure-ready') === 'true';
            }""",
            arg=args.selector,
            timeout=args.timeout_ms,
        )
        blocked_websockets.extend(
            page.evaluate("window.__ARGUS_BLOCKED_WEBSOCKETS__ || []")
        )
        blocked_workers.extend(
            page.evaluate("window.__ARGUS_BLOCKED_WORKERS__ || []")
        )
        blocked_realtime.extend(
            page.evaluate("window.__ARGUS_BLOCKED_REALTIME__ || []")
        )
        locator = page.locator(args.selector).first
        if (
            console_errors
            or page_errors
            or blocked_requests
            or blocked_websockets
            or blocked_workers
            or blocked_realtime
        ):
            raise RuntimeError(
                "browser figure emitted errors: "
                + json.dumps(
                    {
                        "console_errors": console_errors,
                        "page_errors": page_errors,
                        "blocked_requests": blocked_requests,
                        "blocked_websockets": blocked_websockets,
                        "blocked_workers": blocked_workers,
                        "blocked_realtime": blocked_realtime,
                    },
                    ensure_ascii=False,
                )
            )
        if suffix == ".svg":
            svg = locator.evaluate(
                """root => {
                  const svg = root.matches('svg') ? root : root.querySelector('svg');
                  if (!svg) throw new Error('figure root contains no SVG. A CSS layout has none: render it with --output *.pdf for vector or *.png for raster. Never hand-write an <svg> to satisfy this.');
                  const clone = svg.cloneNode(true);
                  const sourceNodes = [svg, ...svg.querySelectorAll('*')];
                  const cloneNodes = [clone, ...clone.querySelectorAll('*')];
                  const properties = [
                    'color', 'fill', 'fill-opacity', 'stroke', 'stroke-width',
                    'stroke-opacity', 'opacity', 'font-family', 'font-size',
                    'font-style', 'font-weight', 'letter-spacing', 'text-anchor',
                    'dominant-baseline', 'shape-rendering'
                  ];
                  sourceNodes.forEach((source, index) => {
                    const target = cloneNodes[index];
                    const computed = getComputedStyle(source);
                    properties.forEach(property => {
                      const value = computed.getPropertyValue(property);
                      if (value) target.style.setProperty(property, value);
                    });
                  });
                  return new XMLSerializer().serializeToString(clone);
                }"""
            )
            _validate_svg(svg)
            output.write_text(svg + "\n", encoding="utf-8")
        elif suffix == ".png":
            locator.screenshot(path=str(output), animations="disabled")
        else:
            page.evaluate(
                """({selector, width, height}) => {
                  const root = document.querySelector(selector);
                  if (!root) throw new Error('figure root not found');
                  const ancestors = new Set();
                  for (let node = root.parentElement; node; node = node.parentElement) {
                    ancestors.add(node);
                  }
                  for (const node of document.body.querySelectorAll('*')) {
                    if (node === root || root.contains(node) || ancestors.has(node)) continue;
                    node.style.setProperty('display', 'none', 'important');
                  }
                  root.style.width = `${width}px`;
                  root.style.height = `${height}px`;
                  root.style.margin = '0';
                  root.style.overflow = 'hidden';
                  root.style.position = 'fixed';
                  root.style.left = '0';
                  root.style.top = '0';
                }""",
                {
                    "selector": args.selector,
                    "width": args.width,
                    "height": args.height,
                },
            )
            page.emulate_media(media="print")
            page.add_style_tag(
                content=(
                    "@page{margin:0;size:"
                    f"{args.width}px {args.height}px"
                    "}html,body{margin:0;padding:0;overflow:hidden}"
                )
            )
            page.pdf(
                path=str(output),
                width=f"{args.width}px",
                height=f"{args.height}px",
                print_background=True,
                prefer_css_page_size=True,
            )
        browser.close()

    input_path = Path(args.input).expanduser()
    metadata = {
        "schema_version": 1,
        "input": str(input_path.resolve()) if input_path.is_file() else url,
        "input_sha256": _sha256(input_path.resolve()) if input_path.is_file() else None,
        "selector": args.selector,
        "output": str(output),
        "output_sha256": _sha256(output),
        "width": args.width,
        "height": args.height,
        "device_scale_factor": args.device_scale_factor,
        "locale": args.locale,
        "timezone": args.timezone,
        "playwright_version": version("playwright"),
        "browser": args.browser_channel or "chromium",
        "console_errors": console_errors,
        "page_errors": page_errors,
        "blocked_requests": blocked_requests,
        "blocked_websockets": blocked_websockets,
        "blocked_workers": blocked_workers,
        "blocked_realtime": blocked_realtime,
    }
    metadata_path = output.with_suffix(output.suffix + ".render.json")
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
