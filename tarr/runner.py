import asyncio, sys, json, hashlib
from pathlib import Path
from typing import Dict, Any
import yaml

from .utils import now_ts_run
from .audit import open_audit
from .launcher import open_context
from .corpus import Corpus

SCRIPT_NAME = "teams_agent_recon2"

def load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _cfg_hash(cfg: Dict[str, Any]) -> str:
    safe = {}
    for k, v in cfg.items():
        if k.startswith("__"): continue
        try:
            json.dumps(v, default=str); safe[k] = v
        except Exception:
            safe[k] = str(v)
    blob = json.dumps({k: safe[k] for k in sorted(safe.keys())}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

def _write_manifest(cfg: Dict[str, Any], run_ts: str) -> None:
    root = Path(cfg.get("artifacts_root", "artifacts")); root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "script": SCRIPT_NAME,
        "run_ts": run_ts,
        "url": cfg.get("teams_channel_url", ""),
        "channel": cfg.get("browser_channel", ""),
        "storage_state_path": cfg.get("storage_state_path", "auth/auth_state.json"),
        "config_hash": _cfg_hash(cfg),
    }
    (root / f"run.{run_ts}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

async def init_mode(cfg: Dict[str, Any]):
    run_ts = now_ts_run(); cfg["__run_ts__"] = run_ts
    audit = open_audit(run_ts, SCRIPT_NAME, cfg.get("audit_dir", "audit"))
    _write_manifest(cfg, run_ts)
    print(f"[DEBUG] Launch init | channel={cfg.get('browser_channel','msedge')}", flush=True)
    audit.log("INIT_LAUNCH", channel=cfg.get("browser_channel","msedge"), url=cfg.get("teams_channel_url",""))
    browser, context = await open_context(cfg.get("browser_channel","msedge"), bool(cfg.get("headless", False)), storage_state=None)
    page = await context.new_page()
    print("[READY] Log into Teams in the opened Edge window, then press Enter here to save state.", flush=True)
    try: input()
    except KeyboardInterrupt: pass
    sp = Path(cfg.get("storage_state_path", "auth/auth_state.json")); sp.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(sp))
    try: import os; os.chmod(sp, 0o600)
    except Exception: pass
    audit.log("STATE_SAVED", path=str(sp))
    try: await context.close(); await browser.close(); await getattr(browser, "_pw").stop()
    except Exception: pass

async def normal_mode(cfg: Dict[str, Any], show_controls: bool, controls_on_enter: bool, dry_run: bool):
    run_ts = now_ts_run(); cfg["__run_ts__"] = run_ts; cfg["__dry_run__"] = bool(dry_run)
    audit = open_audit(run_ts, SCRIPT_NAME, cfg.get("audit_dir", "audit"))
    _write_manifest(cfg, run_ts)
    sp = Path(cfg.get("storage_state_path", "auth/auth_state.json"))
    if not sp.exists():
        print(f"[FATAL] Storage state missing: {sp}. Run with --init first.", file=sys.stderr, flush=True)
        audit.log("STATE_MISSING", path=str(sp)); return
    browser, context = await open_context(cfg.get("browser_channel","msedge"), bool(cfg.get("headless", False)), storage_state=str(sp))
    page = await context.new_page()
    corp = Corpus()
    if show_controls:
        from .tk_panel import start_tk_panel
        loop = asyncio.get_running_loop()
        start_tk_panel(loop, page, cfg, audit, corp)
        print("[INFO] Tk control panel launched.", flush=True)
        audit.log("TK_PANEL", launched=True)
    try:
        while True: await asyncio.sleep(1)
    except KeyboardInterrupt: pass
    finally:
        try: await context.close(); await browser.close(); await getattr(browser, "_pw").stop()
        except Exception: pass

async def main_entry(cfg_path: str, init: bool, show_controls: bool, controls_on_enter: bool, dry_run: bool):
    cfg = load(cfg_path)
    if init: await init_mode(cfg)
    else: await normal_mode(cfg, show_controls, controls_on_enter, dry_run)