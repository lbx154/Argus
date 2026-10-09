"""Use the Argus web cockpit like a user would (xplat-dogfood workflow).

Starts ``argus --web`` on 127.0.0.1 with a fixed token against the backend
CLI found on PATH (the workflow puts a credential-free fake ``codex`` there),
then drives the page with Playwright:

1. first screen; create a project and open it;
2. type 你好 in the composer and wait for the reply;
3. open Settings from the sidebar, change the model and apply it;
4. send a direct task ("write hello.py ...") and wait for the Manager,
   Engineer and Reviewer to finish it and for the delivery to show;
5. send a staged task so the Planner runs too.

Process snapshots are taken while the team runs (spawned workers, process
groups). Everything lands in the output directory given as argv[1]; each
stage records what happened in ``web_report.json``. Never raises on a product
failure.
"""
from __future__ import annotations

import json
import os
import re
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
TASK_TIMEOUT = float(os.environ.get("ARGUS_DOGFOOD_TASK_TIMEOUT", "300"))


def stage(name: str, ok: bool, **info) -> None:
    REPORT["stages"][name] = {"ok": ok, **info}
    print(f"[{'OK' if ok else 'FAIL'}] {name} {json.dumps(info, ensure_ascii=False)[:800]}", flush=True)


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


def save(name: str, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    (OUT / name).write_text(text, encoding="utf-8")


def process_snapshot(label: str) -> None:
    """Record every Argus / fake-backend process with its parent (and group)."""
    try:
        if os.name == "nt":
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine"
                   " | ConvertTo-Json -Depth 2"]
            raw = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=60).stdout
            rows = json.loads(raw or "[]")
            keep = [r for r in rows if re.search(r"argus|codex|xplat|fake", str(r.get("CommandLine") or ""), re.I)]
            text = json.dumps(keep, indent=2)
        else:
            raw = subprocess.run(["ps", "-axo", "pid,ppid,pgid,etime,command"], capture_output=True,
                                 text=True, timeout=60).stdout
            lines = raw.splitlines()
            text = "\n".join(lines[:1] + [l for l in lines[1:] if re.search(r"argus|codex|xplat|fake", l, re.I)])
        save(f"processes-{label}.txt", text)
    except Exception as exc:  # noqa: BLE001
        save(f"processes-{label}.txt", f"snapshot failed: {exc!r}\n{traceback.format_exc()}")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def daemon_watch(sid: str, label: str, seconds: float = 40.0) -> dict:
    """Follow the project's daemon process for a while after a task finishes."""
    root = HOME / "projects" / sid
    timeline = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            pid = int((root / "daemon.pid").read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            pid = 0
        try:
            status = json.loads((root / "daemon.status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = {}
        timeline.append({"t": round(time.time() - t0, 1), "pid": pid, "alive": pid_alive(pid),
                         "state": status.get("state") or status.get("status"),
                         "status_pid": status.get("pid")})
        time.sleep(2)
    save(f"daemon-watch-{label}.json", timeline)
    died = [row for row in timeline if row["pid"] and not row["alive"]]
    return {"first": timeline[0] if timeline else None, "last": timeline[-1] if timeline else None,
            "pid_seen_dead_at": died[0]["t"] if died else None}


def project_events(sid: str) -> list[dict]:
    path = HOME / "projects" / sid / "events.jsonl"
    events = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return events


def wait_mission(sid: str, start_index: int, label: str, page, expected: int = 1) -> dict:
    """Wait until ``expected`` missions started after ``start_index`` complete (or one fails)."""
    t0 = time.time()
    snap_at = {20.0, 60.0}
    seen_types: list[str] = []
    while time.time() - t0 < TASK_TIMEOUT:
        events = project_events(sid)[start_index:]
        types = [str(e.get("type")) for e in events]
        seen_types = types
        elapsed = time.time() - t0
        for mark in sorted(snap_at):
            if elapsed >= mark:
                process_snapshot(f"{label}-{int(mark)}s")
                try:
                    page.screenshot(path=str(OUT / f"{label}-{int(mark)}s.png"), full_page=True)
                except Exception:  # noqa: BLE001
                    pass
                snap_at.discard(mark)
        done = [e for e in events if e.get("type") in ("life.mission.completed", "life.mission.failed")]
        if done and (len(done) >= expected or done[-1].get("type") == "life.mission.failed"):
            return {"ok": done[-1].get("type") == "life.mission.completed", "seconds": round(elapsed, 1),
                    "missions_finished": len(done),
                    "final": {k: str(v)[:300] for k, v in done[-1].items()
                              if k in ("type", "status", "title", "stop_kind", "reason")},
                    "event_types": sorted(set(types))}
        time.sleep(3)
    return {"ok": False, "timeout": TASK_TIMEOUT, "event_types": sorted(set(seen_types)),
            "last_events": [{k: str(v)[:200] for k, v in e.items() if k in ("type", "status", "reason")}
                            for e in project_events(sid)[-15:]]}


def main() -> int:
    HOME.mkdir(parents=True, exist_ok=True)
    WORKDIR.mkdir(parents=True, exist_ok=True)
    argus = shutil.which("argus")
    REPORT["argus_bin"] = argus
    REPORT["backend_bin"] = shutil.which("codex")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
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
        save("api_meta.json", meta)
        stage("api_meta", st == 200, status=st)
        try:
            run_browser()
        except Exception as exc:  # noqa: BLE001
            stage("browser_crashed", False, error=repr(exc), tb=traceback.format_exc())
    finally:
        process_snapshot("final")
        if os.name == "nt":
            try:
                query = ("Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddHours(-2)} "
                         "-ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match "
                         "'Application Error|Windows Error Reporting|Application Hang' } | "
                         "Select-Object TimeCreated,ProviderName,Id,Message | Format-List | Out-String -Width 400")
                events = subprocess.run(["powershell", "-NoProfile", "-Command", query], capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=120)
                save("windows-application-errors.txt", events.stdout + events.stderr)
            except Exception as exc:  # noqa: BLE001
                save("windows-application-errors.txt", repr(exc))
        if REPORT.get("sid"):
            # Stopping the project's daemon exercises the OS stop/signal path.
            try:
                stop = subprocess.run([argus or "argus", "--daemon-stop", "--resume", REPORT["sid"],
                                       "--life-dir", str(HOME)], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=120, env=env, cwd=str(WORKDIR))
                time.sleep(3)
                process_snapshot("after-daemon-stop")
                stage("daemon_stop", stop.returncode == 0, exit=stop.returncode,
                      output=(stop.stdout + stop.stderr)[-1500:])
            except Exception as exc:  # noqa: BLE001
                stage("daemon_stop", False, error=repr(exc))
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
            collect_state()
            save("web_report.json", REPORT)
    return 0


def collect_state() -> None:
    """Copy every events.jsonl / daemon log and the project workspaces' listing."""
    dest = OUT / "state"
    dest.mkdir(exist_ok=True)
    listing = []
    for path in HOME.rglob("*"):
        try:
            if not path.is_file():
                continue
            rel = path.relative_to(HOME)
            listing.append(f"{path.stat().st_size:>9} {rel}")
            if path.name in ("events.jsonl", "daemon.log", "worker.log") or path.suffix == ".log":
                target = dest / "__".join(rel.parts)
                shutil.copyfile(path, target)
        except OSError as exc:
            listing.append(f"ERR {path}: {exc!r}")
    save("state/tree.txt", "\n".join(sorted(listing)))
    tracebacks = []
    for path in [OUT / "server.log", *dest.glob("*")]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)", text, re.S):
            tracebacks.append(f"--- {path.name}\n{match.group(0)[:4000]}")
    save("tracebacks.txt", "\n\n".join(tracebacks) or "(none)")


def close_dialogs(page) -> None:
    for _ in range(3):
        dialogs = page.get_by_role("dialog")
        if not dialogs.count() or not dialogs.first.is_visible():
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
    for name in ("Close", "关闭"):
        button = page.get_by_role("dialog").get_by_role("button", name=name)
        if button.count():
            try:
                button.first.click(timeout=3000)
            except Exception:  # noqa: BLE001
                pass


def composer(page):
    for name in ("message Argus", "Message Argus"):
        box = page.get_by_role("textbox", name=name, exact=True)
        for index in range(box.count()):
            if box.nth(index).is_visible():
                return box.nth(index)
    return page.locator("textarea:visible").first


def send_in_ui(page, text: str) -> None:
    box = composer(page)
    box.click()
    box.fill(text)
    box.press("Enter")


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

            sid = ""
            try:
                st, created = api("POST", "/api/daemons", {})
                save("api_create_project.json", created)
                sid = str(created.get("sid") or "") if isinstance(created, dict) else ""
                stage("create_project", st == 200 and bool(sid), status=st, sid=sid,
                      workdir=created.get("workdir") if isinstance(created, dict) else None)
            except Exception as exc:  # noqa: BLE001
                stage("create_project", False, error=repr(exc), tb=traceback.format_exc())
            if not sid:
                return
            REPORT["sid"] = sid
            page.goto(f"{BASE}/?token={TOKEN}&project={sid}", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)
            for label in ("Conversation", "对话"):
                tab = page.get_by_role("button", name=label, exact=True)
                if tab.count():
                    try:
                        tab.first.click(timeout=5000)
                        break
                    except Exception:  # noqa: BLE001
                        pass
            page.wait_for_timeout(1000)
            page.screenshot(path=str(OUT / "02-project-open.png"), full_page=True)

            # 2. 你好 typed in the composer, reply shown on the page.
            try:
                send_in_ui(page, "你好")
                page.get_by_text(re.compile("离线测试替身|offline test backend")).first.wait_for(timeout=120000)
                page.wait_for_timeout(1500)
                page.screenshot(path=str(OUT / "03-chat-nihao-reply.png"), full_page=True)
                stage("chat_nihao_ui", True)
            except Exception as exc:  # noqa: BLE001
                page.screenshot(path=str(OUT / "03-chat-nihao-failed.png"), full_page=True)
                st, reply = api("POST", f"/api/projects/{sid}/message", {"text": "你好"}, timeout=180)
                save("api_message_nihao.json", reply)
                stage("chat_nihao_ui", False, error=repr(exc)[:400], api_status=st,
                      api_reply=json.dumps(reply, ensure_ascii=False)[:400])

            # 3. Settings: open from the sidebar, change the model, apply.
            try:
                page.get_by_role("button", name=re.compile(r"^(Open settings|打开设置)$")).first.click(timeout=15000)
                dialog = page.get_by_role("dialog").first
                dialog.wait_for(timeout=15000)
                page.wait_for_timeout(1500)
                page.screenshot(path=str(OUT / "04-settings-dialog.png"), full_page=True)
                select = dialog.get_by_role("combobox", name=re.compile(r"^(Model|模型)$")).first
                values = [v for v in select.locator("option").evaluate_all("els => els.map(e => e.value)") if v and v != "auto"]
                chosen = values[0] if values else "auto"
                select.select_option(chosen)
                with page.expect_response(lambda r: "/config/set" in r.url, timeout=30000) as info:
                    select.locator("xpath=following-sibling::button[1]").click()
                response = info.value
                body = response.text()
                page.wait_for_timeout(1500)
                page.screenshot(path=str(OUT / "05-settings-model-applied.png"), full_page=True)
                _, config = api("GET", f"/api/projects/{sid}/config")
                save("api_config_after_model.json", config)
                stage("settings_change_model", response.ok, model=chosen, options=values[:8],
                      status=response.status, body=body[:300])
            except Exception as exc:  # noqa: BLE001
                page.screenshot(path=str(OUT / "05-settings-failed.png"), full_page=True)
                stage("settings_change_model", False, error=repr(exc)[:600])
            close_dialogs(page)

            # 4. Direct task: Manager -> Engineer -> Reviewer.
            run_task(page, sid, "write hello.py that prints hi and run it", "hello.py", "06-task-hello")
            # 5. Staged task: the Planner splits it first.
            run_task(page, sid, "plan it in steps: write greet.py that prints hello, then run it",
                     "greet.py", "07-task-staged", expected=2)

            try:
                for label in ("Deliveries", "交付"):
                    tab = page.get_by_role("button", name=label)
                    if tab.count():
                        tab.first.click(timeout=5000)
                        break
                page.wait_for_timeout(2000)
                page.screenshot(path=str(OUT / "08-deliveries.png"), full_page=True)
                stage("deliveries_view", True)
            except Exception as exc:  # noqa: BLE001
                stage("deliveries_view", False, error=repr(exc)[:300])

            try:
                narrow = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
                npage = narrow.new_page()
                npage.goto(f"{BASE}/?token={TOKEN}&project={sid}", wait_until="networkidle", timeout=60000)
                npage.wait_for_timeout(2500)
                npage.screenshot(path=str(OUT / "09-narrow.png"), full_page=True)
                stage("narrow_screenshot", True)
            except Exception as exc:  # noqa: BLE001
                stage("narrow_screenshot", False, error=repr(exc))
        finally:
            save("browser_console.log", "\n".join(console))
            browser.close()


def run_task(page, sid: str, text: str, filename: str, label: str, expected: int = 1) -> None:
    start = len(project_events(sid))
    try:
        send_in_ui(page, text)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / f"{label}-sent.png"), full_page=True)
        result = wait_mission(sid, start, label, page, expected)
        _, project = api("GET", f"/api/projects/{sid}")
        workdir = ""
        if isinstance(project, dict):
            workdir = str(project.get("workdir") or (project.get("project") or {}).get("workdir") or "")
        candidates = [Path(workdir) / filename] if workdir else []
        candidates.append(HOME / "workspaces" / sid / filename)
        produced = next((p for p in candidates if p.exists()), None)
        output = ""
        if produced is not None:
            run = subprocess.run([sys.executable, str(produced)], capture_output=True, text=True, timeout=60)
            output = (run.stdout or run.stderr).strip()
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / f"{label}-done.png"), full_page=True)
        # A new file delivery is announced by a toast whose Open button shows it.
        modal = page.get_by_role("heading", name=re.compile(r"^(Result files|成果文件)$"))
        toast = page.locator(".delivery-toast")
        delivery = {"toast": False, "modal": False, "toast_text": ""}
        try:
            toast.first.wait_for(timeout=20000)
            delivery["toast"] = True
            delivery["toast_text"] = toast.first.inner_text()[:200]
            toast.first.get_by_role("button", name=re.compile(r"^(Open|查看)$")).click(timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        try:
            modal.first.wait_for(timeout=10000)
            delivery["modal"] = True
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUT / f"{label}-delivery.png"), full_page=True)
        finished = bool(result.pop("ok", False))
        watch = daemon_watch(sid, label)
        stage(label, finished and produced is not None, mission_completed=finished,
              delivery=delivery, daemon_after=watch, file=str(produced or ""), output=output, **result)
        for name in ("Back to map", "返回地图"):
            back = page.get_by_role("button", name=re.compile(name))
            if back.count():
                try:
                    back.first.click(timeout=3000)
                except Exception:  # noqa: BLE001
                    pass
        close_dialogs(page)
    except Exception as exc:  # noqa: BLE001
        try:
            page.screenshot(path=str(OUT / f"{label}-failed.png"), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        stage(label, False, error=repr(exc)[:600], tb=traceback.format_exc()[-1500:])


if __name__ == "__main__":
    sys.exit(main())
