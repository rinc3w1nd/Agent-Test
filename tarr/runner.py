import asyncio, sys, json, hashlib, os
from pathlib import Path
from typing import Dict, Any

from .config import load as load_config
from .utils import now_ts_run
from .audit import open_audit
from .launcher import open_context
from .tarr_selectors import MESSAGE_LIST
from .overlay import inject
from .corpus import Corpus

SCRIPT_NAME = "teams_agent_recon2"

def _cfg_hash(cfg: Dict[str, Any]) -> str:
    """Stable hash of the effective config (for manifest)."""
    # Only include JSON-serializable keys
    safe = {}
    for k, v in cfg.items():
        if k.startswith("__"):  # runtime keys
            continue
        try:
            json.dumps(v, default=str)
            safe[k] = v
        except Exception:
            safe[k] = str(v)
    blob = json.dumps({k: safe[k] for k in sorted(safe.keys())}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

def _write_manifest(cfg: Dict[str, Any], run_ts: str) -> None:
    root = Path(cfg.get("artifacts_root", "artifacts"))
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "script": SCRIPT_NAME,
        "run_ts": run_ts,
        "url": cfg.get("teams_channel_url", ""),
        "channel": cfg.get("browser_channel", ""),
        "storage_state_path": cfg.get("storage_state_path", "auth/auth_state.json"),
        "config_hash": _cfg_hash(cfg),
    }
    (root / f"run.{run_ts}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

def _playwright_version_guard():
    try:
        import playwright  # type: ignore
        from packaging.version import Version  # type: ignore
        if Version(playwright.__version__) < Version("1.45.0"):
            print(f"[FATAL] Playwright {playwright.__version__} < 1.45.0; upgrade required.", file=sys.stderr)
            sys.exit(2)
    except Exception:
        # best-effort
        pass

async def init_mode(cfg: Dict[str, Any]):
    """--init: open Teams without state; user logs in; on Enter, save state and exit."""
    run_ts = now_ts_run()
    cfg["__run_ts__"] = run_ts
    audit = open_audit(run_ts, SCRIPT_NAME, cfg.get("audit_dir", "audit"))
    _write_manifest(cfg, run_ts)

    print(f"[DEBUG] Launch: non-persistent | channel={cfg.get('browser_channel','msedge')} | storage_state=None | url={cfg.get('teams_channel_url','')}", flush=True)
    audit.log("INIT_LAUNCH", channel=cfg.get("browser_channel","msedge"), url=cfg.get("teams_channel_url",""))

    browser, context = await open_context(cfg.get("browser_channel","msedge"), bool(cfg.get("headless", False)), storage_state=None)
    page = await context.new_page()
    await page.goto(cfg.get("teams_channel_url",""), wait_until="domcontentloaded")
    print("[READY] Log into Teams in the opened Edge window.\nPress Enter here when you are fully signed in to save state and exit.", flush=True)
    try:
        input()
    except KeyboardInterrupt:
        pass

    state_path = Path(cfg.get("storage_state_path", "auth/auth_state.json"))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(state_path))
    try:
        os.chmod(state_path, 0o600)
    except Exception:
        pass
    audit.log("STATE_SAVED", path=str(state_path))

    try:
        await context.close()
        await browser.close()
        await getattr(browser, "_pw").stop()
    except Exception:
        pass

async def normal_mode(cfg: Dict[str, Any], show_controls: bool, controls_on_enter: bool, dry_run: bool):
    """Normal run: load state, open Teams, optionally overlay, and/or launch external Tk controls."""
    run_ts = now_ts_run()
    cfg["__run_ts__"] = run_ts
    cfg["__dry_run__"] = bool(dry_run)

    audit = open_audit(run_ts, SCRIPT_NAME, cfg.get("audit_dir", "audit"))
    _write_manifest(cfg, run_ts)

    sp = Path(cfg.get("storage_state_path", "auth/auth_state.json"))
    if not sp.exists():
        msg = f"[FATAL] Storage state missing: {sp}. Run with --init first."
        print(msg, file=sys.stderr, flush=True)
        audit.log("STATE_MISSING", path=str(sp))
        return

    print(f"[DEBUG] Launch: non-persistent | channel={cfg.get('browser_channel','msedge')} | storage_state={sp} | url={cfg.get('teams_channel_url','')}", flush=True)
    audit.log("LAUNCH", mode="nonpersistent", channel=cfg.get("browser_channel","msedge"),
              storage_state=str(sp), url=cfg.get("teams_channel_url",""))

    # Open browser/context/page
    try:
        browser, context = await open_context(cfg.get("browser_channel","msedge"), bool(cfg.get("headless", False)), storage_state=str(sp))
        print("[DEBUG] Browser/context created", flush=True)
        page = await context.new_page()
        print("[DEBUG] New page opened", flush=True)
    except Exception as e:
        print(f"[FATAL] Failed to open context/page: {e!r}", file=sys.stderr, flush=True)
        audit.log("OPEN_FAIL", error=repr(e))
        return

    # Navigate and initial readiness
    try:
        print("[DEBUG] Navigating to Teams URL…", flush=True)
        await page.goto(cfg.get("teams_channel_url",""), wait_until="domcontentloaded")
        print("[DEBUG] DOMContentLoaded reached", flush=True)
        await page.wait_for_selector("body", timeout=15000)
    except Exception as e:
        print(f"[FATAL] Navigation error: {e!r}", file=sys.stderr, flush=True)
        audit.log("NAV_FAIL", error=repr(e))

    # Shared corpus state (used by overlay and/or Tk panel)
    corp = Corpus()

    # Optional: in-page overlay (kept OFF by default since you’re using Tk)
    nav_task = None
    stdin_task = None
    if show_controls and bool(cfg.get("enable_overlay", False)):
        async def do_inject(tag: str):
            print(f"[DEBUG] ({tag}) Injecting overlay…", flush=True)
            try:
                await inject(page, cfg, audit, corp)
                audit.log("OVERLAY", injected=True, via=tag)
                print("[INFO] Overlay injected.", flush=True)
            except Exception as e:
                audit.log("OVERLAY_FAIL", error=repr(e), via=tag)
                print(f"[FATAL] Overlay injection failed ({tag}): {e!r}", flush=True)

        async def watch_navigation():
            while True:
                try:
                    await page.wait_for_event("framenavigated")
                    await page.wait_for_selector("body", timeout=15000)
                    await do_inject("nav")
                except Exception as e:
                    audit.log("OVERLAY_WATCH_FAIL", error=repr(e))

        async def watch_stdin():
            loop = asyncio.get_running_loop()
            print("[INFO] Type 'i' + Enter to (re)inject overlay, 'q' + Enter to quit.", flush=True)
            while True:
                try:
                    line = await loop.run_in_executor(None, sys.stdin.readline)
                    if not line:
                        continue
                    cmd = line.strip().lower()
                    if cmd == "i":
                        await do_inject("cli")
                    elif cmd == "q":
                        print("[INFO] Quit requested from CLI.", flush=True)
                        break
                except Exception:
                    break

        if controls_on_enter:
            input("\n[READY] Press Enter to inject overlay…")
        await do_inject("initial")
        nav_task = asyncio.create_task(watch_navigation())
        stdin_task = asyncio.create_task(watch_stdin())

    # Launch external Tk controls (default ON; persists across reloads)
    if show_controls and bool(cfg.get("use_tk_controls", True)):
        try:
            from .tk_panel import start_tk_panel
            loop = asyncio.get_running_loop()
            # Make sure a run timestamp exists for artifacts invoked from Tk
            if "__run_ts__" not in cfg:
                cfg["__run_ts__"] = run_ts
            start_tk_panel(loop, page, cfg, audit, corp)
            print("[INFO] Tk control panel launched (external window).", flush=True)
            audit.log("TK_PANEL", launched=True)
        except Exception as e:
            print(f"[WARN] Tk panel failed to launch: {e!r}", flush=True)
            audit.log("TK_PANEL_FAIL", error=repr(e))

    # Long readiness wait (non-blocking to controls)
    try:
        print("[DEBUG] Waiting for message list (this may take a while)…", flush=True)
        await page.locator(MESSAGE_LIST).first.wait_for(timeout=int(cfg.get("message_list_timeout_ms", 240000)))
        audit.log("READY", what="message_list", result="ok")
        print("[DEBUG] Message list ready", flush=True)
    except Exception as e:
        audit.log("READY", what="message_list", result="timeout")
        print(f"[WARN] Message list wait timed out: {e!r}", flush=True)

    # Idle loop to keep everything alive for operator
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # cancel watchers if they exist
        try:
            if nav_task:
                nav_task.cancel()
        except Exception:
            pass
        try:
            if stdin_task:
                stdin_task.cancel()
        except Exception:
            pass
        # teardown
        try:
            await context.close()
            await browser.close()
            await getattr(browser, "_pw").stop()
            print("[DEBUG] Closed context/browser", flush=True)
        except Exception as e:
            print(f"[WARN] Teardown issue: {e!r}", flush=True)

async def main_entry(cfg_path: str, init: bool, show_controls: bool, controls_on_enter: bool, dry_run: bool):
    _playwright_version_guard()
    cfg = load_config(cfg_path)
    if init:
        await init_mode(cfg)
    else:
        await normal_mode(cfg, show_controls, controls_on_enter, dry_run)