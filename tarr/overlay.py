from typing import Dict
from .tarr_selectors import COMPOSER, SEND_BUTTON
from .mention import bind
from .capture import poll_latest_reply
from .artifacts import append_text, append_html, screenshot
from .utils import now_ts_run

def _strip_bot_directive(s: str) -> str:
    if not s: 
        return ""
    import re
    s = s.lstrip("\u200b\u2060\ufeff \t\r\n")
    return re.sub(r"^@bot[\s\u200b\u2060\ufeff]*[:,\-–--]*[\s\u200b\u2060\ufeff]*", "", s, flags=re.IGNORECASE)

async def inject(page, cfg: Dict, audit, corpus_ctrl):
    overlay_state = {"auto_send_after_typing": False}
    DRY = bool(cfg.get("__dry_run__", False))

    async def _py_load_corpus(text: str):
        n = corpus_ctrl.load_jsonl(text or "")
        audit.log("CORPUS_LOAD", count=n)
        return {"count": n}

    async def _py_send_at_only():
        if DRY:
            audit.log("BIND", result="dry_run")
            return {"ok": True}
        fast = bool(cfg.get("mention_fast_mode_on_ui", True))
        ok = await bind(page, cfg.get("bot_name",""), cfg, audit, fast=fast)
        return {"ok": bool(ok)}

    async def _py_send_corpus():
        row = corpus_ctrl.current()
        if not row:
            return {"ok": False, "reason": "no-current"}

        payload = _strip_bot_directive((row.get("payload","") or ""))

        if DRY:
            audit.log("SEND_PREP", id=row.get("id",""), dry_run=True, chars=len(payload))
            return {"ok": True}

        comp = page.locator(COMPOSER).first
        await comp.click(timeout=int(cfg.get("dom_ready_timeout_ms", 120000)))

        fast = bool(cfg.get("mention_fast_mode_on_ui", True))
        await bind(page, cfg.get("bot_name",""), cfg, audit, fast=fast)

        if not payload.strip():
            audit.log("SEND_PREP", id=row.get("id",""), chars=0, note="mention_only_after_strip", fast=fast)
            return {"ok": True}

        # Instant insert
        try:
            await page.keyboard.insertText(" " + payload)
            audit.log("SEND_PREP", id=row.get("id",""), method="insertText", chars=len(payload), fast=fast)
        except Exception:
            delay = int(cfg.get("mention_type_char_delay_ms_fast", 1 if fast else cfg.get("mention_type_char_delay_ms", 35)))
            await comp.type(" " + payload, delay=delay)
            audit.log("SEND_PREP", id=row.get("id",""), method="type", chars=len(payload), fast=fast)

        if overlay_state["auto_send_after_typing"]:
            try:
                await comp.press("Control+Enter"); audit.log("SEND", id=row.get("id",""), method="Ctrl+Enter")
            except Exception:
                try:
                    await page.locator(SEND_BUTTON).click(timeout=1500); audit.log("SEND", id=row.get("id",""), method="click")
                except Exception:
                    await comp.press("Enter"); audit.log("SEND", id=row.get("id",""), method="Enter")
        return {"ok": True}

    async def _py_next_corpus():
        ok = corpus_ctrl.next()
        audit.log("CORPUS_NEXT", ok=ok, idx=corpus_ctrl.i, total=len(corpus_ctrl.items))
        return {"ok": ok, "idx": corpus_ctrl.i, "total": len(corpus_ctrl.items)}

    async def _py_prev_corpus():
        ok = corpus_ctrl.prev()
        audit.log("CORPUS_PREV", ok=ok, idx=corpus_ctrl.i, total=len(corpus_ctrl.items))
        return {"ok": ok, "idx": corpus_ctrl.i, "total": len(corpus_ctrl.items)}

    async def _py_record_status():
        row = corpus_ctrl.current() or {}
        rid = row.get("id","live")

        if DRY:
            audit.log("RECORD", id=rid, dry_run=True)
            return {"ok": True, "text_len": 0, "html_len": 0, "note": ""}

        data = await poll_latest_reply(page, cfg.get("bot_name",""), int(cfg.get("reply_timeout_ms", 120000)))
        text = (data or {}).get("text","")
        html = (data or {}).get("html","")

        prompt_arg = ("JSON.stringify(" + repr(text) + ")") if text else "''"
        note = await page.evaluate(f"window.prompt('Enter note (detected reply shown below):', {prompt_arg})")

        run_ts = cfg.get("__run_ts__", "unknown")
        tpath = append_text(run_ts, rid, row, text, cfg.get("text_dir","artifacts/text"),
                            reply_detected=bool(text), reply_len=len(text), operator_note=(note or ""))
        hpath = None
        if html:
            hpath = append_html(run_ts, rid, html, cfg.get("html_dir","artifacts/html"))

        ss_ts = now_ts_run()
        spath = await screenshot(ss_ts, rid, page, cfg.get("screens_dir","artifacts/screens"))

        audit.log("RECORD", id=rid, reply_detected=bool(text), reply_len=len(text),
                  text_path=str(tpath), html_path=str(hpath or ""), screenshot=str(spath), note=(note or ""))
        return {"ok": True, "text_len": len(text), "html_len": len(html), "note": note or ""}

    async def _py_toggle_auto_send(on: bool):
        overlay_state["auto_send_after_typing"] = bool(on)
        audit.log("AUTO_SEND", enabled=overlay_state["auto_send_after_typing"])
        return {"ok": True, "enabled": overlay_state["auto_send_after_typing"]}

    await page.expose_function("pyLoadCorpus", _py_load_corpus)
    await page.expose_function("pySendAtOnly", _py_send_at_only)
    await page.expose_function("pySendCorpus", _py_send_corpus)
    await page.expose_function("pyNextCorpus", _py_next_corpus)
    await page.expose_function("pyPrevCorpus", _py_prev_corpus)
    await page.expose_function("pyRecordStatus", _py_record_status)
    await page.expose_function("pyToggleAutoSend", _py_toggle_auto_send)

    # Trusted-Types safe draggable overlay
    js = r"""
(() => {
  if (document.getElementById('tarr-overlay')) return;
  const css = (el, styles) => Object.assign(el.style, styles);
  const box = document.createElement('div');
  box.id = 'tarr-overlay';
  css(box, {
    position:'fixed', right:'16px', bottom:'16px', width:'360px', zIndex:'2147483647',
    background:'#161616', color:'#eee', border:'1px solid #444', borderRadius:'8px',
    padding:'10px', fontFamily:'system-ui,Segoe UI,Roboto,Arial', boxShadow:'0 4px 16px rgba(0,0,0,.4)'
  });

  // DRAG support
  const POS_KEY = 'tarrOverlayPos';
  const clamp = (v,min,max)=>Math.max(min,Math.min(max,v));
  const restorePos = () => {
    try {
      const saved = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
      if (!saved) return;
      box.style.right='auto'; box.style.bottom='auto';
      box.style.left=saved.left+'px'; box.style.top=saved.top+'px';
    } catch{}
  };
  const savePos = () => {
    try {
      const r=box.getBoundingClientRect();
      localStorage.setItem(POS_KEY, JSON.stringify({left:Math.round(r.left), top:Math.round(r.top)}));
    } catch{}
  };

  // Header
  const header = document.createElement('div');
  css(header,{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:'6px',cursor:'move'});
  const title=document.createElement('strong'); title.textContent='Recon Controls';
  const close=document.createElement('button'); close.textContent='✕';
  css(close,{background:'#333',color:'#ccc',border:'0',borderRadius:'4px',padding:'2px 6px'});
  close.addEventListener('click',()=>box.remove());
  header.append(title,close);

  // Drag handlers
  let drag=null;
  header.addEventListener('pointerdown',ev=>{
    if(ev.target===close) return;
    ev.preventDefault();
    const r=box.getBoundingClientRect();
    drag={dx:ev.clientX-r.left, dy:ev.clientY-r.top};
    header.setPointerCapture(ev.pointerId);
  });
  header.addEventListener('pointermove',ev=>{
    if(!drag) return;
    ev.preventDefault();
    const vw=document.documentElement.clientWidth||window.innerWidth;
    const vh=document.documentElement.clientHeight||window.innerHeight;
    const nl=clamp(ev.clientX-drag.dx,0,vw-box.offsetWidth);
    const nt=clamp(ev.clientY-drag.dy,0,vh-box.offsetHeight);
    box.style.right='auto'; box.style.bottom='auto';
    box.style.left=nl+'px'; box.style.top=nt+'px';
  });
  const endDrag=ev=>{ if(!drag) return; try{header.releasePointerCapture(ev.pointerId);}catch{} drag=null; savePos(); };
  header.addEventListener('pointerup',endDrag);
  header.addEventListener('pointercancel',endDrag);

  // ... build the rest of your controls/buttons same as before ...
  box.append(header);
  document.body.appendChild(box);
  restorePos();
})();
"""
    try:
        await page.evaluate(js)
    except Exception as e:
        audit.log("OVERLAY_EVAL_FAIL", error=repr(e))
        raise