# tarr/runner.py  (replace your file with this)
import sys, json, hashlib, threading, queue, asyncio, traceback, os
from pathlib import Path
from typing import Dict, Any, Optional

import yaml

from .utils import now_ts_run
from .audit import open_audit
from .launcher import open_context
from .corpus import Corpus

SCRIPT_NAME = "teams_agent_recon2"

VERBOSE = os.environ.get("TARR_VERBOSE", "1") != "0"

def _dbg(msg: str):
    if VERBOSE:
        print(f"[DBG] {msg}", flush=True)

def load(path: str) -> Dict[str, Any]:
    _dbg(f"Loading config: {path}")
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
    p = root / f"run.{run_ts}.json"
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _dbg(f"Wrote manifest: {p}")

class PWWorker:
    def __init__(self, cfg: Dict[str, Any], init_mode: bool = False):
        self.cfg = cfg
        self.init_mode = init_mode
        self.thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.page = None
        self.context = None
        self.browser = None
        self._ready = queue.Queue(maxsize=1)
        self._please_stop = threading.Event()

    def start(self):
        def runner():
            try:
                asyncio.run(self._async_main())
            except Exception as e:
                tb = traceback.format_exc()
                try:
                    self._ready.put((False, f"{e!r}\n{tb}"))
                except Exception:
                    pass

        _dbg("Starting Playwright worker thread…")
        self.thread = threading.Thread(target=runner, name="TARR-PW", daemon=True)
        self.thread.start()
        ok, err = self._ready.get()
        if not ok:
            print("[FATAL] Playwright worker failed to start:\n" + (err or ""), file=sys.stderr, flush=True)
            raise RuntimeError(err or "Playwright worker failed")

    async def _async_main(self):
        self.loop = asyncio.get_running_loop()
        chan = self.cfg.get("browser_channel", "msedge")
        headless = bool(self.cfg.get("headless", False))
        storage_state = None if self.init_mode else self.cfg.get("storage_state_path", "auth/auth_state.json")
        _dbg(f"PW _async_main: channel={chan} headless={headless} storage_state={storage_state}")

        self.browser, self.context = await open_context(chan, headless, storage_state)
        self.page = await self.context.new_page()
        _dbg("PW ready (browser+context+page)")
        self._ready.put((True, None))

        # keep the loop alive until stop
        while not self._please_stop.is_set():
            await asyncio.sleep(0.1)

        _dbg("PW shutting down…")
        try:
            if self.context: await self.context.close()
        except Exception as e:
            _dbg(f"Context close error: {e!r}")
        try:
            if self.browser:
                await self.browser.close()
                pw = getattr(self.browser, "_pw", None)
                if pw:
                    await pw.stop()
        except Exception as e:
            _dbg(f"Browser close error: {e!r}")

    def stop(self):
        if not self.thread:
            return
        _dbg("Stopping Playwright worker…")
        self._please_stop.set()
        self.thread.join(timeout=5)
        _dbg("Playwright worker stopped.")

def init_mode(cfg: Dict[str, Any]):
    run_ts = now_ts_run(); cfg["__run_ts__"] = run_ts
    audit = open_audit(run_ts, SCRIPT_NAME, cfg.get("audit_dir", "audit"))
    _write_manifest(cfg, run_ts)

    print(f"[DEBUG] Launch init | channel={cfg.get('browser_channel','msedge')}", flush=True)
    audit.log("INIT_LAUNCH", channel=cfg.get("browser_channel","msedge"), url=cfg.get("teams_channel_url",""))

    worker = PWWorker(cfg, init_mode=True)
    worker.start()

    try:
        print("[READY] A browser window opened. Log into Teams, then press Enter here to save state.", flush=True)
        input()
    except KeyboardInterrupt:
        pass

    async def save_state():
        sp = Path(cfg.get("storage_state_path", "auth/auth_state.json"))
        sp.parent.mkdir(parents=True, exist_ok=True)
        await worker.context.storage_state(path=str(sp))
        try:
            import os; os.chmod(sp, 0o600)
        except Exception:
            pass
        return str(sp)

    fut = asyncio.run_coroutine_threadsafe(save_state(), worker.loop)  # type: ignore
    try:
        path = fut.result(timeout=30)
        print(f"[INFO] Storage state saved: {path}")
        audit.log("STATE_SAVED", path=path)
    except Exception as e:
        print(f"[FATAL] Failed saving storage state: {e}", file=sys.stderr)
        audit.log("STATE_SAVE_FAIL", error=repr(e))

    worker.stop()

def normal_mode(cfg: Dict[str, Any], show_controls: bool, controls_on_enter: bool, dry_run: bool):
    run_ts = now_ts_run(); cfg["__run_ts__"] = run_ts; cfg["__dry_run__"] = bool(dry_run)
    audit = open_audit(run_ts, SCRIPT_NAME, cfg.get("audit_dir", "audit"))
    _write_manifest(cfg, run_ts)

    sp = Path(cfg.get("storage_state_path", "auth/auth_state.json"))
    _dbg(f"Checking storage state at {sp} (exists={sp.exists()})")
    if not sp.exists():
        print(f"[FATAL] Storage state missing: {sp}. Run with --init first.", file=sys.stderr, flush=True)
        audit.log("STATE_MISSING", path=str(sp)); return

    worker = PWWorker(cfg, init_mode=False)
    try:
        worker.start()
    except Exception:
        # already printed by start(); also write audit
        audit.log("PW_START_FAIL")
        return

    _dbg("Launching Tk panel…")
    corp = Corpus()
    try:
        if show_controls and bool(cfg.get("use_tk_controls", True)):
            from .tk_panel import start_tk_panel
            start_tk_panel(worker.loop, worker.page, cfg, audit, corp)  # blocks until window closed
            audit.log("TK_PANEL", launched=True)
        else:
            print("[INFO] Controls not requested; exiting.", flush=True)
    except Exception as e:
        print(f"[FATAL] Tk panel failed to launch: {e}", file=sys.stderr, flush=True)
        traceback.print_exc()
        audit.log("TK_FAIL", error=repr(e))
    finally:
        worker.stop()

def main_entry(cfg_path: str, init: bool, show_controls: bool, controls_on_enter: bool, dry_run: bool):
    try:
        cfg = load(cfg_path)
        if init:
            init_mode(cfg)
        else:
            normal_mode(cfg, show_controls, controls_on_enter, dry_run)
    except Exception as e:
        print(f"[FATAL] Uncaught error in runner: {e}", file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)