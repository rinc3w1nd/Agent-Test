from typing import Dict
from .tarr_selectors import COMPOSER, SEND_BUTTON
from .mention import bind
from .capture import poll_latest_reply
from .artifacts import append_text, append_html, screenshot
from .utils import now_ts_run

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
        ok = await bind(page, cfg.get("bot_name",""), cfg, audit)
        return {"ok": bool(ok)}

    async def _py_send_corpus():
        row = corpus_ctrl.current()
        if not row:
            return {"ok": False, "reason": "no-current"}

        payload = (row.get("payload","") or "")
        if DRY:
            audit.log("SEND_PREP", id=row.get("id",""), dry_run=True, chars=len(payload))
            return {"ok": True}

        comp = page.locator(COMPOSER).first
        await comp.click(timeout=int(cfg.get("dom_ready_timeout_ms", 120000)))

        await bind(page, cfg.get("bot_name",""), cfg, audit)

        if payload.startswith("@BOT"):
            payload = payload[len("@BOT"):].lstrip()

        await comp.type((" " + payload) if payload else "", delay=int(cfg.get("mention_type_char_delay_ms", 35)))
        audit.log("SEND_PREP", id=row.get("id",""), chars=len(payload))

        if overlay_state["auto_send_after_typing"]:
            try:
                await comp.press("Control+Enter"); audit.log("SEND", id=row.get("id",""), method="Ctrl+Enter")
            except Exception:
                try:
                    await page.locator(SEND_BUTTON).click(timeout=3000); audit.log("SEND", id=row.get("id",""), method="click")
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

    js = """
(() => {
  if (document.getElementById('tarr-overlay')) return;
  const box = document.createElement('div');
  box.id = 'tarr-overlay';
  box.style.cssText = 'position:fixed;right:16px;bottom:16px;width:360px;z-index:2147483647;background:#161616;color:#eee;border:1px solid #444;border-radius:8px;padding:10px;font-family:system-ui,Segoe UI,Roboto,Arial;box-shadow:0 4px 16px rgba(0,0,0,.4)';

  box.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
      <strong>Recon Controls</strong>
      <button id="rc-close" style="background:#333;color:#ccc;border:0;border-radius:4px;padding:2px 6px;cursor:pointer;">✕</button>
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

    <label style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:8px;">
      <input type="checkbox" id="rc-auto" /> Auto-send after typing
    </label>

    <div id="rc-info" style="font-size:12px;color:#bbb;line-height:1.35;">
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
  const auto  = box.querySelector('#rc-auto');

  function setMsg(t){ msg.textContent = t; }
  function setPos(i,n){ pos.textContent = i; tot.textContent = n; }

  close.onclick = () => box.remove();

  auto.onchange = async () => {
    const r = await window.pyToggleAutoSend(auto.checked);
    setMsg('Auto-send: ' + (r.enabled ? 'ON' : 'OFF'));
  };

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
    setMsg('Typing current corpus item…');
    const r = await window.pySendCorpus();
    setMsg(r && r.ok ? 'Typed.' : ('Send failed: ' + (r && r.reason || 'unknown')));
  };

  prev.onclick = async () => {
    const r = await window.pyPrevCorpus();
    if(r && r.ok){ setPos(r.idx, r.total); setMsg('Moved back to ' + r.idx + '/' + r.total); }
    else { setMsg('At beginning'); }
  };

  next.onclick = async () => {
    const r = await window.pyNextCorpus();
    if(r && r.ok){ setPos(r.idx, r.total); setMsg('Advanced to ' + r.idx + '/' + r.total); }
    else { setMsg('At end'); }
  };

  stat.onclick = async () => {
    setMsg('Recording status…');
    const r = await window.pyRecordStatus();
    setMsg('Recorded: text='+(r.text_len||0)+', html='+(r.html_len||0));
  };
})();
"""
    try:
        await page.evaluate(js)
    except Exception as e:
        audit.log("OVERLAY_EVAL_FAIL", error=repr(e))
        # Surface the exact JS error to the console
        raise