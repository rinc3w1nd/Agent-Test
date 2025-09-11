#!/usr/bin/env python3
# teams_agent_recon.py

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import argparse
try:
    import yaml  # pip install pyyaml
except Exception:
    yaml = None

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, TimeoutError as PWTimeout

# ------------------------------
# Globals / Debug
# ------------------------------
DEBUG = False
ARGS = None
PERSISTENT_EDGE = False

def dbg(*a):
    if DEBUG:
        print("[DEBUG]", *a, flush=True)

def zwsp_strip(s: str) -> str:
    if not s:
        return s
    # remove zero-width chars that often appear in Teams
    return s.replace("\u200b", "").replace("\u2060", "").replace("\ufeff", "")

# ------------------------------
# Live Controller (overlay)
# ------------------------------
class LiveController:
    """Holds an in-memory corpus queue and current index for live control."""
    def __init__(self):
        self.items: List[Dict[str, Any]] = []
        self.idx = 0
        self.source = ""
        self.run_ts = time.strftime("%y%m%d-%H%M%S", time.localtime())

    def load_jsonl_text(self, text: str, source_label: str = "overlay"):
        self.items = []
        self.idx = 0
        self.source = source_label or "overlay"
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                self.items.append(obj)
            except Exception:
                # ignore malformed line
                pass
        dbg(f"Loaded {len(self.items)} items from {self.source}")
        return {"count": len(self.items), "source": self.source}

    def current(self):
        if 0 <= self.idx < len(self.items):
            return self.items[self.idx]
        return None

    def advance(self):
        if self.idx + 1 < len(self.items):
            self.idx += 1
            return True
        return False

    def back(self):
        if self.idx - 1 >= 0:
            self.idx -= 1
            return True
        return False

    def position(self):
        return {"idx": self.idx, "total": len(self.items)}

LIVE = LiveController()

# ------------------------------
# Selectors (conservative)
# ------------------------------
SEL = {
    # Channel/chat UI
    "composer": '[data-tid="newMessage"] textarea, [data-tid="cke_wysiwyg_div"]',
    "new_post_composer": '[contenteditable="true"], [role="textbox"]',
    "send_button": '[data-tid="send"]',
    "mention_popup": '[data-tid="mentionSuggestList"], [role="listbox"]',

    # Lists
    "channel_list": '[data-tid="mainMessageList"], [data-tid="threadList"], [data-tid="channelMessageList"]',
}

# ------------------------------
# Config helpers
# ------------------------------
def load_yaml(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    if not Path(path).exists():
        print(f"[WARN] Config not found: {path}", file=sys.stderr)
        return {}
    if yaml is None:
        print("[WARN] pyyaml not installed; ignoring config file", file=sys.stderr)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[WARN] Failed to read YAML {path}: {e}", file=sys.stderr)
        return {}

def get_cfg(cfg: Dict[str, Any], key: str, default: Any) -> Any:
    v = cfg.get(key, default)
    return v

# ------------------------------
# Web bootstrap
# ------------------------------
async def ensure_web(page: Page):
    """Lightweight: wait until Teams shell and message list appear."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
    except Exception:
        pass
    try:
        await page.wait_for_selector(SEL["channel_list"], timeout=120000)
    except Exception:
        pass

async def scroll_bottom(page: Page, list_sel: str):
    try:
        await page.evaluate(
            """(sel)=>{const el=document.querySelector(sel); if(el){ el.scrollTop = el.scrollHeight; }}""",
            list_sel
        )
    except Exception:
        pass

# ------------------------------
# Mention binding (configurable)
# ------------------------------
async def bind_mention(page: Page, composer, bot_name: str, cfg: dict) -> bool:
    """
    Use config knobs to bind @mention.
    - mention_delay_before_at_ms: delay before typing '@'
    - mention_type_char_delay_ms: typing delay per char
    - mention_popup_wait_ms: timeout waiting for popup
    - mention_retype_wait_ms: wait after delete before retype
    - mention_retype_attempts: number of retypes inside this attempt
    - mention_retype_backoff: if true, multiply retype wait by attempt number
    """
    delay_before = int(get_cfg(cfg, "mention_delay_before_at_ms", 15000))
    char_delay = int(get_cfg(cfg, "mention_type_char_delay_ms", 35))
    popup_wait = int(get_cfg(cfg, "mention_popup_wait_ms", 5000))
    retype_wait = int(get_cfg(cfg, "mention_retype_wait_ms", 500))
    retypes = int(get_cfg(cfg, "mention_retype_attempts", 2))
    backoff = bool(get_cfg(cfg, "mention_retype_backoff", True))

    await page.wait_for_timeout(delay_before)

    async def has_bound_mention() -> bool:
        try:
            pill = composer.locator('[data-tid="mentionPill"], [data-mention], at-mention, span[data-mention], div[data-mention]')
            return (await pill.count()) > 0
        except Exception:
            return False

    option_sel = (
        '[data-tid="mentionSuggestList"] [role="option"], '
        '[data-tid="mentionSuggestList"] li, '
        '[role="listbox"] [role="option"], '
        '[data-tid*="Mention"] [role="option"]'
    )

    # Type @ and bot name
    await composer.type("@", delay=char_delay)
    await composer.type(bot_name, delay=char_delay)

    # Attempts: initial + retypes
    for attempt in range(retypes + 1):
        # Wait for popup
        try:
            await page.locator(SEL["mention_popup"]).wait_for(timeout=popup_wait)
        except Exception:
            pass

        # 1) Keyboard select
        try:
            await composer.press("ArrowDown")
            await composer.press("Enter")
            if await has_bound_mention():
                dbg("Mention bound via ArrowDown+Enter")
                return True
        except Exception:
            pass

        # 2) Click first suggestion
        try:
            first_opt = page.locator(option_sel).first
            await first_opt.click(timeout=3000)
            if await has_bound_mention():
                dbg("Mention bound via click on first option")
                return True
        except Exception:
            pass

        # 3) Plain Enter fallback
        try:
            await composer.press("Enter")
            if await has_bound_mention():
                dbg("Mention bound via Enter")
                return True
        except Exception:
            pass

        # If not last attempt, delete typed name and retype after wait
        if attempt < retypes:
            try:
                for _ in range(len(bot_name)):
                    await composer.press("Backspace")
            except Exception:
                pass
            wait_this = retype_wait * (attempt + 1) if backoff else retype_wait
            await page.wait_for_timeout(wait_this)
            await composer.type(bot_name, delay=char_delay)

    return False

# ------------------------------
# Send payload
# ------------------------------
async def send_payload(page: Page, cfg: dict, payload: str, bot_name: str):
    char_delay = int(get_cfg(cfg, "mention_type_char_delay_ms", 35))
    comp = page.locator(SEL["new_post_composer"]).first
    await comp.click(timeout=int(get_cfg(cfg, "composer_timeout_ms", 60000)))

    if payload.startswith("@BOT"):
        # Outer windows before binding, e.g., [15000,30000,45000]
        windows = get_cfg(cfg, "mention_attempt_windows_ms", [15000, 30000, 45000])
        if isinstance(windows, str):
            try:
                import ast
                windows = ast.literal_eval(windows)
            except Exception:
                windows = [15000, 30000, 45000]

        bound = False
        for i, pre_wait in enumerate(windows, 1):
            dbg(f"@mention window {i}/{len(windows)}: waiting {pre_wait}ms before binding")
            # temporarily override per window
            temp_cfg = dict(cfg)
            temp_cfg["mention_delay_before_at_ms"] = int(pre_wait)
            bound = await bind_mention(page, comp, bot_name, temp_cfg)
            dbg("Bound @mention:", bound)
            if bound:
                break
        remainder = payload[len("@BOT"):].lstrip()
        if remainder:
            await comp.type(" " + remainder, delay=char_delay)
            dbg("Typed remainder chars:", len(remainder))
    else:
        await comp.type(payload, delay=char_delay)
        dbg("Typed payload chars:", len(payload))

    # Try send
    try:
        await comp.press("Control+Enter")
    except Exception:
        try:
            await page.locator(SEL["send_button"]).click(timeout=3000)
        except Exception:
            await comp.press("Enter")

# ------------------------------
# Observer-based reply capture (strict)
# ------------------------------
async def wait_for_bot_reply_observer(page: Page, bot_name: str, timeout_ms: int = 130000) -> Dict[str, Any]:
    """
    Watch for a newly added DOM element that BOTH looks like a message node
    (role="group" or has data-tid) AND contains the bot name (case-insensitive)
    in text or aria-label. Returns {"text","html"} or raises on timeout.
    """
    bot_lc = (bot_name or "").lower()
    js = """
(function(){
  const botLC = __BOT_LC__;
  const deadline = Date.now() + __TIMEOUT__;
  const bodySel = '[data-tid="messageBody"],[data-tid="messageText"],[data-tid="messageContent"],[data-tid="adaptiveCardRoot"]';

  function* walk(n){
    yield n;
    const kids = (n && n.shadowRoot) ? [n.shadowRoot, ...n.children] : (n?.children || []);
    for(const k of kids) yield* walk(k);
  }
  function deepQueryOne(sel, root){
    for(const n of walk(root||document)){
      if(n.querySelector){
        try { const el = n.querySelector(sel); if(el) return el; } catch(e){}
      }
    }
    return null;
  }
  function deepText(root){
    let t = '';
    try { t = root.innerText?.trim(); } catch(e){}
    if(!t){ try { t = root.textContent?.trim(); } catch(e){} }
    try {
      const b = deepQueryOne(bodySel, root);
      if(b){
        const bt = (b.innerText||b.textContent||'').trim();
        if(bt) t = bt;
      }
    } catch(e){}
    return t || '';
  }

  return new Promise(resolve=>{
    const obs = new MutationObserver(muts=>{
      for(const m of muts) for(const node of m.addedNodes){
        if(!(node instanceof Element)) continue;
        const aria = (node.getAttribute?.('aria-label')||'').toLowerCase();
        const txt  = (node.textContent||'').toLowerCase();
        const looksMsg = !!(node.getAttribute?.('role')==='group' || node.getAttribute?.('data-tid'));
        const hit = looksMsg && (aria.includes(botLC) || txt.includes(botLC));
        if(hit){
          const text = deepText(node);
          const html = node.innerHTML || '';
          try { console.debug('[OBSERVER] match', {aria: aria.slice(0,120), text: txt.slice(0,120)}); } catch(_){}
          obs.disconnect();
          resolve({text, html});
          return;
        }
      }
    });
    obs.observe(document, {subtree:true, childList:true});
    const tick = ()=>{ if(Date.now() > deadline){ obs.disconnect(); resolve(null); } else setTimeout(tick, 250); };
    tick();
  });
})()
""".replace("__BOT_LC__", json.dumps(bot_lc)).replace("__TIMEOUT__", str(int(timeout_ms)))
    data = await page.evaluate(js)
    if not data:
        raise PWTimeout("Observer timed out without matching node")
    data["text"] = zwsp_strip(data.get("text","")).strip()
    return data

# ------------------------------
# Overlay controls
# ------------------------------
async def install_control_overlay(page: Page, cfg: dict, bot_name: str):
    """
    Injects a floating control panel with:
      - Load Corpus JSONL (file picker)
      - Send @BOT (bind only)
      - Send Corpus (send current item)
      - Prev Corpus / Next Corpus
      - Record Status (screenshot + attempt extract; prompt on miss)
    """
    # Exposed functions (Python)
    async def _py_load_corpus(text: str):
        meta = LIVE.load_jsonl_text(text or "", source_label="overlay")
        return meta

    async def _py_send_at_only():
        comp = page.locator(SEL["new_post_composer"]).first
        await comp.click(timeout=int(get_cfg(cfg, "composer_timeout_ms", 60000)))
        ok = await bind_mention(page, comp, bot_name, cfg)
        return {"ok": bool(ok)}

    async def _py_send_corpus():
        row = LIVE.current()
        if not row:
            return {"ok": False, "reason": "no-current"}
        payload = row.get("payload") or row.get("prompt") or row.get("text") or ""
        if not payload:
            return {"ok": False, "reason": "empty-payload"}
        await send_payload(page, cfg, payload, bot_name)
        # Wait for reply (observer)
        try:
            reply = await wait_for_bot_reply_observer(page, bot_name, timeout_ms=int(get_cfg(cfg, "reply_timeout_ms", 120000)))
        except Exception:
            reply = {"text": "", "html": ""}

        # Save artifacts
        base = Path("artifacts")
        dir_text = base / "text"; dir_html = base / "html"; dir_screens = base / "screens"
        for d in (dir_text, dir_html, dir_screens):
            d.mkdir(parents=True, exist_ok=True)

        safe_id = (row.get("id") or f"live_{LIVE.idx:03d}").replace("/", "_").replace("\\", "_")
        # append text
        with open(dir_text / f"{safe_id}.txt", "a", encoding="utf-8") as tf:
            tf.write(f"\n\n=== RUN {LIVE.run_ts} (LIVE) ===\n")
            tf.write(reply.get("text", ""))

        if reply.get("html"):
            with open(dir_html / f"{safe_id}.html", "a", encoding="utf-8") as hf:
                hf.write(f"\n\n<!-- RUN {LIVE.run_ts} (LIVE) -->\n")
                hf.write(reply.get("html", ""))

        ss_path = dir_screens / f"{safe_id}.{LIVE.run_ts}.png"
        try:
            await page.screenshot(path=str(ss_path), full_page=True)
        except Exception:
            pass

        return {"ok": True, "safe_id": safe_id, "run_ts": LIVE.run_ts,
                "text_len": len(reply.get("text","")), "html_len": len(reply.get("html",""))}

    async def _py_next_corpus():
        ok = LIVE.advance()
        return {"ok": ok, **LIVE.position()}

    async def _py_prev_corpus():
        ok = LIVE.back()
        return {"ok": ok, **LIVE.position()}

    async def _py_record_status():
        base = Path("artifacts"); dir_screens = base / "screens"
        dir_screens.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%y%m%d-%H%M%S", time.localtime())
        path = dir_screens / f"status.{ts}.png"
        try:
            await page.screenshot(path=str(path), full_page=True)
        except Exception:
            pass
        # Try extract last bot
        try:
            # Best-effort: read something visible
            reply = await wait_for_bot_reply_observer(page, bot_name, timeout_ms=2000)
            text = reply.get("text","")
        except Exception:
            text = ""
        if not text:
            note = await page.evaluate("window.prompt('No bot reply detected. Enter a short note for the log (optional):')")  # noqa
        else:
            note = ""
        return {"ok": True, "screenshot": str(path), "note": note or "", "text_len": len(text)}

    # Wire Python functions into the page
    await page.expose_function("pyLoadCorpus", _py_load_corpus)
    await page.expose_function("pySendAtOnly", _py_send_at_only)
    await page.expose_function("pySendCorpus", _py_send_corpus)
    await page.expose_function("pyNextCorpus", _py_next_corpus)
    await page.expose_function("pyPrevCorpus", _py_prev_corpus)
    await page.expose_function("pyRecordStatus", _py_record_status)

    # Inject overlay
    js = """
(() => {
  if (document.getElementById('recon-overlay')) return;

  const box = document.createElement('div');
  box.id = 'recon-overlay';
  box.style.cssText = `
    position: fixed; z-index: 2147483647; right: 16px; bottom: 16px;
    width: 360px; background: rgba(22,22,22,0.95); color: #eee;
    font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    border: 1px solid #444; border-radius: 8px; padding: 10px; box-shadow: 0 4px 16px rgba(0,0,0,.4);
  `;
  box.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
      <strong>Recon Controls</strong>
      <button id="rc-close" title="hide" style="background:#333;color:#ccc;border:0;border-radius:4px;padding:2px 6px;cursor:pointer;">✕</button>
    </div>

    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;">
      <input id="rc-file" type="file" accept=".json,.jsonl,application/json" style="display:none" />
      <button id="rc-load"  style="flex:1 1 100%;background:#5e35b1;color:#fff;border:0;border-radius:6px;padding:6px 8px;cursor:pointer;">Load Corpus JSONL</button>
      <button id="rc-at"    style="flex:1 1 46%;background:#1565c0;color:#fff;border:0;border-radius:6px;padding:6px 8px;cursor:pointer;">Send @BOT</button>
      <button id="rc-send"  style="flex:1 1 46%;background:#2e7d32;color:#fff;border:0;border-radius:6px;padding:6px 8px;cursor:pointer;">Send Corpus</button>
      <button id="rc-prev"  style="flex:1 1 46%;background:#8d6e63;color:#fff;border:0;border-radius:6px;padding:6px 8px;cursor:pointer;">Prev Corpus</button>
      <button id="rc-next"  style="flex:1 1 46%;background:#ef6c00;color:#fff;border:0;border-radius:6px;padding:6px 8px;cursor:pointer;">Next Corpus</button>
      <button id="rc-stat"  style="flex:1 1 100%;background:#455a64;color:#fff;border:0;border-radius:6px;padding:6px 8px;cursor:pointer;">Record Status</button>
    </div>

    <div id="rc-info" style="font-size:12px;color:#bbb;line-height:1.35;">
      <div>Corpus: <span id="rc-src">overlay</span></div>
      <div>Pos: <span id="rc-pos">0</span>/<span id="rc-total">0</span></div>
      <div id="rc-msg" style="margin-top:4px;"></div>
    </div>
  `;
  document.body.appendChild(box);

  const close = box.querySelector('#rc-close');
  const file  = box.querySelector('#rc-file');
  const load  = box.querySelector('#rc-load');
  const atBtn = box.querySelector('#rc-at');
  const send  = box.querySelector('#rc-send');
  const prev  = box.querySelector('#rc-prev');
  const next  = box.querySelector('#rc-next');
  const stat  = box.querySelector('#rc-stat');
  const msg   = box.querySelector('#rc-msg');
  const pos   = box.querySelector('#rc-pos');
  const tot   = box.querySelector('#rc-total');

  function setMsg(text){ msg.textContent = text; }
  function setPos(i, n){ pos.textContent = i; tot.textContent = n; }

  close.onclick = () => box.remove();

  load.onclick = () => file.click();
  file.onchange = async () => {
    const f = file.files && file.files[0];
    if(!f){ setMsg('No file selected'); return; }
    const text = await f.text();
    setMsg('Loading corpus…');
    const meta = await window.pyLoadCorpus(text);
    setPos(0, meta.count||0);
    setMsg('Loaded ' + (meta.count||0) + ' items');
  };

  atBtn.onclick = async () => {
    setMsg('Binding @…');
    const r = await window.pySendAtOnly();
    setMsg(r.ok ? 'Mention bound' : 'Mention bind failed');
  };

  send.onclick = async () => {
    setMsg('Sending current corpus item…');
    const r = await window.pySendCorpus();
    if(r && r.ok){
      setMsg('Sent; reply text='+(r.text_len||0)+', html='+(r.html_len||0));
    }else{
      setMsg('Send failed: ' + (r && r.reason || 'unknown'));
    }
  };

  prev.onclick = async () => {
    const r = await window.pyPrevCorpus();
    if(r && r.ok){
      setPos(r.idx, r.total);
      setMsg('Moved back to ' + r.idx + '/' + r.total);
    }else{
      setMsg('At beginning of corpus');
    }
  };

  next.onclick = async () => {
    const r = await window.pyNextCorpus();
    if(r && r.ok){
      setPos(r.idx, r.total);
      setMsg('Advanced to ' + r.idx + '/' + r.total);
    }else{
      setMsg('At end of corpus');
    }
  };

  stat.onclick = async () => {
    setMsg('Recording status…');
    const r = await window.pyRecordStatus();
    setMsg('Status: shot=' + (r && r.screenshot || '-') + (r && r.note ? ('; note: '+r.note) : ''));
  };
})();
"""
    await page.evaluate(js)

# ------------------------------
# Batch helpers
# ------------------------------
def load_jsonl_file(path: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                pass
    return items

async def process_item(page: Page, cfg: dict, row: Dict[str, Any], bot_name: str, run_ts: str):
    base = Path("artifacts")
    (base / "text").mkdir(parents=True, exist_ok=True)
    (base / "html").mkdir(parents=True, exist_ok=True)
    (base / "screens").mkdir(parents=True, exist_ok=True)

    pid = (row.get("id") or f"item_{int(time.time())}").replace("/", "_").replace("\\", "_")
    payload = row.get("payload") or row.get("prompt") or row.get("text") or ""

    await send_payload(page, cfg, payload, bot_name)

    # Capture reply
    try:
        reply = await wait_for_bot_reply_observer(page, bot_name, timeout_ms=int(get_cfg(cfg, "reply_timeout_ms", 120000)))
    except Exception:
        reply = {"text": "", "html": ""}

    # Write artifacts (append)
    with open(base / "text" / f"{pid}.txt", "a", encoding="utf-8") as tf:
        tf.write(f"\n\n=== RUN {run_ts} ===\n")
        tf.write(reply.get("text", ""))

    if reply.get("html"):
        with open(base / "html" / f"{pid}.html", "a", encoding="utf-8") as hf:
            hf.write(f"\n\n<!-- RUN {run_ts} -->\n")
            hf.write(reply.get("html", ""))

    # Screenshot with timestamp
    ss_path = base / "screens" / f"{pid}.{run_ts}.png"
    try:
        await page.screenshot(path=str(ss_path), full_page=True)
    except Exception:
        pass

# ------------------------------
# Main runner
# ------------------------------
async def run(cfg: Dict[str, Any], args, bot_name_cli: Optional[str]):
    # Bind CLI args inside run()
    try:
        args
    except NameError:
        args = globals().get('ARGS')
    if args is None:
        class _A:
            keep_open=False; show_controls=False; controls_on_enter=False; corpus=None
        args = _A()

    # Decide mode
    if args.corpus:
        dbg("Batch mode: corpus file provided")
    elif getattr(args, "show_controls", False):
        dbg("Live mode: overlay controls active (no corpus preloaded)")
    else:
        print("ERROR: Either --corpus or --show-controls must be provided", file=sys.stderr)
        return

    channel = str(get_cfg(cfg, "browser_channel", "msedge"))
    headless = bool(get_cfg(cfg, "headless", False))
    extra_args = get_cfg(cfg, "extra_args", [])
    if isinstance(extra_args, str):
        try:
            import ast
            extra_args = ast.literal_eval(extra_args)
        except Exception:
            extra_args = []

    teams_url = get_cfg(cfg, "teams_channel_url", "")
    if not teams_url:
        print("ERROR: teams_channel_url missing in config", file=sys.stderr)
        return

    edge_ud = get_cfg(cfg, "edge_user_data_dir", "")
    edge_prof = get_cfg(cfg, "edge_profile_directory", "")
    storage_state = get_cfg(cfg, "storage_state", None)

    bot_name = bot_name_cli or get_cfg(cfg, "bot_name", "")
    if not bot_name:
        print("ERROR: --bot-name (or bot_name in YAML) is required", file=sys.stderr)
        return

    run_ts = time.strftime("%y%m%d-%H%M%S", time.localtime())

    async with async_playwright() as pw:
        browser: Optional[Browser] = None
        context: Optional[BrowserContext] = None
        global PERSISTENT_EDGE

        # Launch (prefer Edge channel)
        if edge_ud:
            args_list = list(extra_args or [])
            if edge_prof:
                args_list.append(f"--profile-directory={edge_prof}")
            try:
                context = await pw.chromium.launch_persistent_context(
                    user_data_dir=edge_ud,
                    channel=channel,
                    headless=headless,
                    args=args_list,
                )
                PERSISTENT_EDGE = True
                browser = context.browser
            except Exception as e:
                print(f"[WARN] Persistent Edge failed ({e}); falling back to non-persistent.", file=sys.stderr)
                try:
                    browser = await pw.chromium.launch(channel=channel, headless=headless, args=extra_args)
                except Exception:
                    browser = await pw.chromium.launch(headless=headless, args=extra_args)
                context = await browser.new_context(storage_state=storage_state)
        else:
            try:
                browser = await pw.chromium.launch(channel=channel, headless=headless, args=extra_args)
            except Exception:
                browser = await pw.chromium.launch(headless=headless, args=extra_args)
            context = await browser.new_context(storage_state=storage_state)

        page = await context.new_page()
        await page.goto(teams_url, wait_until="domcontentloaded")
        await ensure_web(page)
        dbg("Page loaded; ensuring web app mode done")

        # Overlay gating
        if getattr(args, 'show_controls', False):
            if getattr(args, 'controls_on_enter', False):
                print("\n[READY] Press Enter when the Teams chat UI is fully loaded to inject controls...", flush=True)
                try:
                    input()
                except Exception:
                    pass
            await install_control_overlay(page, cfg, bot_name)
            dbg("Control overlay injected")

        # Batch mode: run corpus file if provided
        if args.corpus:
            items = load_jsonl_file(args.corpus)
            dbg(f"Loaded {len(items)} corpus items from {args.corpus}")
            for row in items:
                await process_item(page, cfg, row, bot_name, run_ts)

        # Keep-open loop
        try:
            if getattr(args, 'keep_open', False):
                dbg("Keep-open enabled: waiting indefinitely (Ctrl+C to exit)")
                while True:
                    await asyncio.sleep(1)
        except KeyboardInterrupt:
            dbg("Interrupted by user; shutting down")

        await context.close()
        if not PERSISTENT_EDGE:
            await browser.close()

# ------------------------------
# CLI
# ------------------------------
def main():
    ap = argparse.ArgumentParser(description="Teams agent recon with overlay controls")
    ap.add_argument("--config", help="Path to YAML config")
    ap.add_argument("--corpus", required=False, help="Path to corpus JSONL file")
    ap.add_argument("--bot-name", help="Override bot name")
    ap.add_argument("--debug", action="store_true", help="Enable verbose debug output to CLI")
    ap.add_argument("--keep-open", action="store_true", help="Keep browser open and wait for control overlay actions")
    ap.add_argument("--show-controls", action="store_true", help="Inject on-page control overlay with buttons")
    ap.add_argument("--controls-on-enter", action="store_true", help="Wait for Enter before injecting overlay")
    args = ap.parse_args()

    global DEBUG, ARGS
    DEBUG = args.debug
    ARGS = args

    cfg = load_yaml(args.config)
    bot_name_cli = args.bot_name

    asyncio.run(run(cfg, args, bot_name_cli))

if __name__ == "__main__":
    main()