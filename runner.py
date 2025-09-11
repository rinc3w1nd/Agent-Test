import asyncio, sys
from pathlib import Path
from typing import Dict, Any
from .config import load as load_config
from .utils import now_ts_run
from .audit import open_audit
from .launcher import open_context
from .selectors import MESSAGE_LIST
from .overlay import inject
from .corpus import Corpus
import json, hashlib

def _cfg_hash(cfg: Dict[str,Any]) -> str:
    # stable hash of relevant config
    blob = json.dumps({k: cfg[k] for k in sorted(cfg.keys())}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

Path(cfg["artifacts_root"]).mkdir(parents=True, exist_ok=True)
(Path(cfg["artifacts_root"]) / f"run.{run_ts}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
SCRIPT_NAME = "teams_agent_recon2"

async def init_mode(cfg: Dict[str,Any]):
    """--init: open Teams without state; user logs in; on Enter, save state and exit (NFR R1c)."""
    print(f"[DEBUG] Launch: non-persistent | channel={cfg['browser_channel']} | storage_state=None | url={cfg['teams_channel_url']}", flush=True)
    run_ts = now_ts_run()
    audit = open_audit(run_ts, SCRIPT_NAME, cfg["audit_dir"])
    manifest = {
        "script": SCRIPT_NAME,
        "run_ts": run_ts,
        "url": cfg["teams_channel_url"],
        "channel": cfg["browser_channel"],
        "storage_state_path": cfg["storage_state_path"],
        "config_hash": _cfg_hash(cfg),
    }
    audit.log("INIT_LAUNCH", channel=cfg["browser_channel"], url=cfg["teams_channel_url"])

    browser, context = await open_context(cfg["browser_channel"], cfg["headless"], storage_state=None)
    page = await context.new_page()
    await page.goto(cfg["teams_channel_url"], wait_until="domcontentloaded")
    print("[READY] Log into Teams in the opened Edge window.\nPress Enter here when you are fully signed in to save state and exit.", flush=True)
    try:
        input()
    except KeyboardInterrupt:
        pass

    state_path = Path(cfg["storage_state_path"])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(state_path))
    import os
        try:
            os.chmod(state_path, 0o600)
        except Exception:
            pass
    audit.log("STATE_SAVED", path=str(state_path))

    try:
        await context.close()
        await browser.close()
        # Close playwright root
        await getattr(browser, "_pw").stop()
    except Exception:
        pass

async def normal_mode(cfg: Dict[str,Any], show_controls: bool, controls_on_enter: bool):
    """Normal run: load state, open Teams, inject overlay, operator drives actions (NFR R1d, U*)."""
    print(f"[DEBUG] Launch: non-persistent | channel={cfg['browser_channel']} | storage_state={cfg['storage_state_path']} | url={cfg['teams_channel_url']}", flush=True)
    run_ts = now_ts_run()
    cfg["__run_ts__"] = run_ts
    cfg["__dry_run__"] = bool(dry_run)
    audit = open_audit(run_ts, SCRIPT_NAME, cfg["audit_dir"])
    manifest = {
        "script": SCRIPT_NAME,
        "run_ts": run_ts,
        "url": cfg["teams_channel_url"],
        "channel": cfg["browser_channel"],
        "storage_state_path": cfg["storage_state_path"],
        "config_hash": _cfg_hash(cfg),
    }
    audit.log("LAUNCH", mode="nonpersistent", channel=cfg["browser_channel"], storage_state=cfg["storage_state_path"], url=cfg["teams_channel_url"])

    # Fail fast if no state (NFR R1e)
    sp = Path(cfg["storage_state_path"])
    if not sp.exists():
        print(f"[FATAL] Storage state missing: {sp}. Run with --init first.", file=sys.stderr)
        audit.log("STATE_MISSING", path=str(sp))
        return

    browser, context = await open_context(cfg["browser_channel"], cfg["headless"], storage_state=str(sp))
    page = await context.new_page()
    await page.goto(cfg["teams_channel_url"], wait_until="domcontentloaded")

    # Generous readiness (NFR P1)
    try:
        await page.locator(MESSAGE_LIST).first.wait_for(timeout=int(cfg["message_list_timeout_ms"]))
        audit.log("READY", what="message_list", result="ok")
    except Exception:
        audit.log("READY", what="message_list", result="timeout")

    if show_controls:
        if controls_on_enter:
            input("\n[READY] Press Enter to inject overlay…")
        corp = Corpus()
        await inject(page, cfg, audit, corp)
        audit.log("OVERLAY", injected=True)
        print("[INFO] Overlay injected. Use on-page controls. Ctrl+C to exit.", flush=True)
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass

    try:
        await context.close()
        await browser.close()
        await getattr(browser, "_pw").stop()
    except Exception:
        pass

async def main_entry(cfg_path: str, init: bool, show_controls: bool, controls_on_enter: bool, dry_run: bool):
    cfg = load_config(cfg_path)
    if init:
        await init_mode(cfg)
    else:
        await normal_mode(cfg, show_controls, controls_on_enter, dry_run)