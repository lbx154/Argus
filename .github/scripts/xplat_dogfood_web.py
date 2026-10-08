"""Drive the Argus web cockpit like a user would (xplat-dogfood workflow).

Starts ``argus --web`` on 127.0.0.1 with a fixed token and the in-process
memory backend, waits for HTTP 200, then uses Playwright to screenshot the
page, create a project, send two messages and open the settings dialog.
Everything lands in the output directory given as argv[1]. Never raises on a
product failure: each stage records what happened in ``web_report.json``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ["ARGUS_SKILL_WEB_TOKEN"]
PORT = int(os.environ.get("ARGUS_DOGFOOD_PORT", "8799"))
BASE = f"http://127.0.0.1:{PORT}"
HOME = Path(os.environ.get("ARGUS_DOGFOOD_HOME", str(OUT / "argus-home"))).resolve()
WORKDIR = HOME.parent / "web-workdir"
REPORT: dict = {"stages": {}}


def stage(name: str, ok: bool, **info) -> None:
    REPORT["stages"][name] = {"ok": ok, **info}
    print(f"[{'OK' if ok else 'FAIL'}] {name} {json.dumps(info, ensure_ascii=False)[:600]}", flush=True)


def api(method: str, path: str, body: dict | None = None, timeout: float = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def find_sid(payload) -> str:
    if isinstance(payload, dict):
        for key in ("sid", "session_id", "id"):
            if isinstance(payload.get(key), str) and payload[key]:
                return payload[key]
        for value in payload.values():
            got = find_sid(value)
            if got:
                return got
    if isinstance(payload, list):
        for value in payload:
            got = find_sid(value)
            if got:
                return got
    return ""


def main() -> int:
    HOME.mkdir(parents=True, exist_ok=True)
    WORKDIR.mkdir(parents=True, exist_ok=True)
    argus = shutil.which("argus")
    REPORT["argus_bin"] = argus
    env = dict(os.environ, ARGUS_SKILL_RUNNER_BACKEND="memory", PYTHONIOENCODING="utf-8")
    cmd = [argus or "argus", "--web", "--web-host", "127.0.0.1", "--web-port", str(PORT),
           "--no-daemon", "--life-dir", str(HOME)]
    REPORT["server_cmd"] = cmd
    log = open(OUT / "server.log", "wb")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(WORKDIR))
    try:
        t0 = time.time()
        status = None
        while time.time() - t0 < 120:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"{BASE}/?token={TOKEN}", timeout=5) as resp:
                    status = resp.status
                    if status == 200:
                        break
            except Exception as exc:  # noqa: BLE001
                status = repr(exc)
            time.sleep(1)
        ready = status == 200
        stage("server_http_200", ready, status=status, seconds=round(time.time() - t0, 1),
              exited=proc.poll())
        if not ready:
            return 0
        st, meta = api("GET", "/api/meta")
        (OUT / "api_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        stage("api_meta", st == 200, status=st)
        run_browser()
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
        finally:
            log.close()
            REPORT["server_exit"] = proc.poll()
            (OUT / "web_report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def run_browser() -> None:
    from playwright.sync_api import sync_playwright

    console: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            try:
                page.goto(f"{BASE}/?token={TOKEN}", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(2000)
                page.screenshot(path=str(OUT / "01-desktop-first-screen.png"), full_page=True)
                stage("desktop_screenshot", True, title=page.title())
            except Exception as exc:  # noqa: BLE001
                stage("desktop_screenshot", False, error=repr(exc))

            # Create a project the way the "New project" button does.
            sid = ""
            try:
                st, created = api("POST", "/api/daemons", {})
                (OUT / "api_create_project.json").write_text(json.dumps(created, ensure_ascii=False, indent=2), encoding="utf-8")
                sid = find_sid(created)
                if not sid:
                    _, projects = api("GET", "/api/projects")
                    sid = find_sid(projects)
                stage("create_project", st == 200 and bool(sid), status=st, sid=sid)
            except Exception as exc:  # noqa: BLE001
                stage("create_project", False, error=repr(exc), tb=traceback.format_exc())

            for idx, text in enumerate(("你好", "hello"), start=1):
                if not sid:
                    break
                try:
                    st, reply = api("POST", f"/api/projects/{sid}/message", {"text": text}, timeout=180)
                    (OUT / f"api_message_{idx}.json").write_text(json.dumps(reply, ensure_ascii=False, indent=2), encoding="utf-8")
                    stage(f"message_{idx}", st == 200, status=st, text=text,
                          reply=(json.dumps(reply, ensure_ascii=False)[:400]))
                except Exception as exc:  # noqa: BLE001
                    stage(f"message_{idx}", False, error=repr(exc))

            try:
                page.reload(wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                page.screenshot(path=str(OUT / "02-desktop-after-messages.png"), full_page=True)
                stage("desktop_after_messages", True)
            except Exception as exc:  # noqa: BLE001
                stage("desktop_after_messages", False, error=repr(exc))

            try:
                btn = page.get_by_role("button", name="Runtime settings")
                btn.first.click(timeout=15000)
                page.wait_for_timeout(1500)
                page.screenshot(path=str(OUT / "03-settings-dialog.png"), full_page=True)
                stage("settings_dialog", page.get_by_role("dialog").count() > 0)
            except Exception as exc:  # noqa: BLE001
                try:
                    page.screenshot(path=str(OUT / "03-settings-dialog-failed.png"), full_page=True)
                except Exception:  # noqa: BLE001
                    pass
                stage("settings_dialog", False, error=repr(exc))

            try:
                narrow = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
                npage = narrow.new_page()
                npage.goto(f"{BASE}/?token={TOKEN}", wait_until="networkidle", timeout=60000)
                npage.wait_for_timeout(2500)
                npage.screenshot(path=str(OUT / "04-narrow.png"), full_page=True)
                stage("narrow_screenshot", True)
            except Exception as exc:  # noqa: BLE001
                stage("narrow_screenshot", False, error=repr(exc))
        finally:
            (OUT / "browser_console.log").write_text("\n".join(console), encoding="utf-8")
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
