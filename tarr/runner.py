import sys, json, hashlib, threading, queue, asyncio
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import yaml

from .utils import now_ts_run
from .audit import open_audit
from .launcher import open_context
from .corpus import Corpus

SCRIPT_NAME = "teams_agent_recon2"

# -----------------------------
# Config + manifest helpers
# -----------------------------

def load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _cfg_hash(cfg: Dict[str, Any]) -> str:
    safe = {}
    for k, v in cfg.items():
        if k.startswith("__"):
            continue
        try:
            json.dumps(v, default=str)
            safe[k] = v
        except Exception:
            safe[k] = str(v)
    blob = json.dumps({k: safe[k] for k in sorted(safe.keys())}, sort_keys=True)
    import hashlib as _hl
    return _hl.sha256(blob.encode("utf-8")).hexdigest()

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

# -----------------------------
# Background Playwright worker
# -----------------------------

class PWWorker:
    """
    Spins up Playwright (Edge) in a background thread with its own asyncio loop.
    Exposes: loop, page, and a stop() method. Safe to call from Tk main thread.
    """

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
        self._stopped = threading.Event()

    def start(self):
        def runner():
            asyncio.run(self._async_main())

        self.thread = threading.Thread(target=runner, name="TARR-PW", daemon=True)
        self.thread.start()
        # block until ready (or error)
        ok, err = self._ready.get()
        if not ok:
            raise RuntimeError(err or "Failed to start Playwright worker")

    async def _async_main(self):
        try:
            self.loop = asyncio.get_running_loop()
            # open Edge
            self.browser, self.context = await open_context(
                self.cfg.get("browser_channel", "msedge"),
                bool(self.cfg.get("headless", False)),
                storage_state=None if self.init_mode else self.cfg.get("storage_state_path", "auth/auth_state.json"),
            )
            self.page = await self.context.new_page()
            # signal ready
            self._ready.put((True, None))
            # idle loop until asked to stop
            while not self._please_stop.is_set():
                await asyncio.sleep(0.1)
        except Exception as e:
            try:
                self._ready.put((False, repr(e)))
            except Exception:
                pass
        finally:
            # graceful shutdown
            try:
                if self.context: await self.context.close()
            except Exception:
                pass
            try:
                if self.browser:
                    await self.browser.close()
                    pw = getattr(self.browser, "_pw", None)
                    if pw:
                        await pw.stop()
            except Exception:
                pass
            self._stopped.set()

    def stop(self):
        if not self.thread:
            return
        self._please_stop.set()
        self.thread.join(timeout=5)

# -----------------------------
# Public entrypoints
# -----------------------------

def init_mode(cfg: Dict[str, Any]):
    # Create run context + audit
    run_ts = now_ts_run()
    cfg["__run_ts__"] = run_ts
    audit = open_audit(run_ts, SCRIPT_NAME, cfg.get("audit_dir", "audit"))
    _write_manifest(cfg, run_ts)

    print(f"[DEBUG] Launch init | channel={cfg.get('browser_channel','msedge')}", flush=True)
    audit.log("INIT_LAUNCH", channel=cfg.get("browser_channel", "msedge"), url=cfg.get("teams_channel_url", ""))

    # Spin up PW in background (no storage state for init)
    worker = PWWorker(cfg, init_mode=True)
    worker.start()

    # Let operator log in, then persist storage_state
    try:
        print("[READY] A browser window opened. Log into Teams, then press Enter here to save state.", flush=True)
        input()
    except KeyboardInterrupt:
        pass

    # Save storage state
    async def save_state():
        sp = Path(cfg.get("storage_state_path", "auth/auth_state.json"))
        sp.parent.mkdir(parents=True, exist_ok=True)
        await worker.context.storage_state(path=str(sp))
        try:
            import os
            os.chmod(sp, 0o600)
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

    # shutdown worker
    worker.stop()

def normal_mode(cfg: Dict[str, Any], show_controls: bool, controls_on_enter: bool, dry_run: bool):
    run_ts = now_ts_run()
    cfg["__run_ts__"] = run_ts
    cfg["__dry_run__"] = bool(dry_run)
    audit = open_audit(run_ts, SCRIPT_NAME, cfg.get("audit_dir", "audit"))
    _write_manifest(cfg, run_ts)

    sp = Path(cfg.get("storage_state_path", "auth/auth_state.json"))
    if not sp.exists():
        print(f"[FATAL] Storage state missing: {sp}. Run with --init first.", file=sys.stderr, flush=True)
        audit.log("STATE_MISSING", path=str(sp))
        return

    # Start PW worker (uses storage_state)
    worker = PWWorker(cfg, init_mode=False)
    worker.start()

    # Build Tk UI on main thread (blocking)
    corp = Corpus()
    if show_controls and bool(cfg.get("use_tk_controls", True)):
        from .tk_panel import start_tk_panel
        # start_tk_panel blocks in root.mainloop(); bridge uses worker.loop + worker.page
        start_tk_panel(worker.loop, worker.page, cfg, audit, corp)  # type: ignore
        audit.log("TK_PANEL", launched=True)
    else:
        print("[INFO] Controls not requested; nothing to do. Exiting.", flush=True)

    # On Tk close, stop Playwright
    worker.stop()

def main_entry(cfg_path: str, init: bool, show_controls: bool, controls_on_enter: bool, dry_run: bool):
    cfg = load(cfg_path)
    if init:
        init_mode(cfg)
    else:
        normal_mode(cfg, show_controls, controls_on_enter, dry_run)