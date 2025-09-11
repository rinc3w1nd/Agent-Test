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

    def _strip_bot_directive(s: str) -> str:
        if not s: 
            return ""
        # normalize leading ZWSPs and whitespace
        s = s.lstrip("\u200b\u2060\ufeff \t\r\n")
        # case-insensitive @BOT at the very start, with optional punctuation/space
        import re
        return re.sub(r"^@bot[\s\u200b\u2060\ufeff]*[:,\-–—]*[\s\u200b\u2060\ufeff]*", "", s, flags=re.IGNORECASE)

    async def _py_send_corpus():
        row = corpus_ctrl.current()
        if not row:
            return {"ok": False, "reason": "no-current"}
    
        payload = (row.get("payload","") or "")
        payload = _strip_bot_directive(payload)  # <-- key line
    
        if DRY:
            audit.log("SEND_PREP", id=row.get("id",""), dry_run=True, chars=len(payload))
            return {"ok": True}
    
        comp = page.locator(COMPOSER).first
        await comp.click(timeout=int(cfg.get("dom_ready_timeout_ms", 120000)))
    
        # Always ensure the @mention pill is bound first
        await bind(page, cfg.get("bot_name",""), cfg, audit)
    
        # If payload is now empty, we're done (mention-only)
        if not payload.strip():
            audit.log("SEND_PREP", id=row.get("id",""), chars=0, note="mention_only_after_strip")
            return {"ok": True}
    
        await comp.type(" " + payload, delay=int(cfg.get("mention_type_char_delay_ms", 35)))
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

    js = r"""
(() => {
  if (document.getElementById('tarr-overlay')) return;

  // helpers
  const css = (el, styles) => Object.assign(el.style, styles);
  const btn = (id, label, styles = {}) => {
    const b = document.createElement('button');
    b.id = id;
    b.type = 'button';
    b.textContent = label;
    css(b, Object.assign({
      background: '#333', color: '#eee', border: '0', borderRadius: '6px',
      padding: '6px 8px', cursor: 'pointer', flex: '1 1 46%'
    }, styles));
    return b;
  };
  const row = (gap = '6px', wrap = true) => {
    const d = document.createElement('div');
    css(d, { display: 'flex', gap, flexWrap: wrap ? 'wrap' : 'nowrap', marginBottom: '8px' });
    return d;
  };

  // container
  const box = document.createElement('div');
  box.id = 'tarr-overlay';
  css(box, {
    position: 'fixed', right: '16px', bottom: '16px', width: '360px', zIndex: '2147483647',
    background: '#161616', color: '#eee', border: '1px solid #444', borderRadius: '8px',
    padding: '10px', fontFamily: 'system-ui,Segoe UI,Roboto,Arial', boxShadow: '0 4px 16px rgba(0,0,0,.4)'
  });

  // header
  const header = row('6px', false);
  css(header, { alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' });
  const title = document.createElement('strong'); title.textContent = 'Recon Controls';
  const close = btn('rc-close', '✕', { background: '#333', color: '#ccc', borderRadius: '4px', padding: '2px 6px', flex: '0 0 auto' });
  header.appendChild(title); header.appendChild(close);

  // controls
  const controls = row();
  // hidden file input
  const file = document.createElement('input');
  file.type = 'file'; file.accept = '.json,.jsonl,application/json';
  css(file, { display: 'none' }); file.id = 'rc-file';

  const loadBtn = btn('rc-load', 'Load Corpus JSONL', { background: '#5e35b1', color: '#fff', flex: '1 1 100%' });
  const atBtn   = btn('rc-at',   'Send @BOT',        { background: '#1565c0', color: '#fff' });
  const sendBtn = btn('rc-send', 'Send Corpus',      { background: '#2e7d32', color: '#fff' });
  const prevBtn = btn('rc-prev', 'Prev Corpus',      { background: '#8d6e63', color: '#fff' });
  const nextBtn = btn('rc-next', 'Next Corpus',      { background: '#ef6c00', color: '#fff' });
  const statBtn = btn('rc-stat', 'Record Status',    { background: '#455a64', color: '#fff', flex: '1 1 100%' });

  controls.append(file, loadBtn, atBtn, sendBtn, prevBtn, nextBtn, statBtn);

  // auto-send checkbox
  const label = document.createElement('label');
  css(label, { display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', marginBottom: '8px' });
  const auto = document.createElement('input'); auto.type = 'checkbox'; auto.id = 'rc-auto';
  const autoTxt = document.createElement('span'); autoTxt.textContent = 'Auto-send after typing';
  label.append(auto, autoTxt);

  // info/status
  const info = document.createElement('div');
  css(info, { fontSize: '12px', color: '#bbb', lineHeight: '1.35' });
  const posLine = document.createElement('div');
  const posText = document.createElement('span'); posText.textContent = 'Pos: ';
  const pos = document.createElement('span'); pos.id = 'rc-pos'; pos.textContent = '0';
  const slash = document.createElement('span'); slash.textContent = '/';
  const tot = document.createElement('span'); tot.id = 'rc-total'; tot.textContent = '0';
  posLine.append(posText, pos, slash, tot);
  const msg = document.createElement('div'); msg.id = 'rc-msg'; css(msg, { marginTop: '4px' });
  info.append(posLine, msg);

  // assemble
  box.append(header, controls, label, info);
  document.body.appendChild(box);

  // helpers
  const setMsg = (t) => { msg.textContent = t || ''; };
  const setPos = (i, n) => { pos.textContent = String(i ?? 0); tot.textContent = String(n ?? 0); };

  // wire events
  close.addEventListener('click', () => box.remove());
  auto.addEventListener('change', async () => {
    const r = await window.pyToggleAutoSend(!!auto.checked);
    setMsg('Auto-send: ' + (r && r.enabled ? 'ON' : 'OFF'));
  });
  loadBtn.addEventListener('click', () => file.click());
  file.addEventListener('change', async () => {
    const f = file.files && file.files[0];
    if (!f) { setMsg('No file selected'); return; }
    setMsg('Loading corpus…');
    const text = await f.text();
    const meta = await window.pyLoadCorpus(text);
    setPos(0, meta && meta.count || 0);
    setMsg('Loaded ' + (meta && meta.count || 0) + ' items');
  });
  atBtn.addEventListener('click', async () => {
    setMsg('Binding @…');
    const r = await window.pySendAtOnly();
    setMsg(r && r.ok ? 'Mention bound' : 'Mention bind failed');
  });
  sendBtn.addEventListener('click', async () => {
    setMsg('Typing current corpus item…');
    const r = await window.pySendCorpus();
    setMsg(r && r.ok ? 'Typed.' : ('Send failed: ' + (r && r.reason || 'unknown')));
  });
  prevBtn.addEventListener('click', async () => {
    const r = await window.pyPrevCorpus();
    if (r && r.ok) { setPos(r.idx, r.total); setMsg('Moved back to ' + r.idx + '/' + r.total); }
    else { setMsg('At beginning'); }
  });
  nextBtn.addEventListener('click', async () => {
    const r = await window.pyNextCorpus();
    if (r && r.ok) { setPos(r.idx, r.total); setMsg('Advanced to ' + r.idx + '/' + r.total); }
    else { setMsg('At end'); }
  });
  statBtn.addEventListener('click', async () => {
    setMsg('Recording status…');
    const r = await window.pyRecordStatus();
    setMsg('Recorded: text=' + (r && r.text_len || 0) + ', html=' + (r && r.html_len || 0));
  });
})();
"""
    try:
        await page.evaluate(js)  # inject UI without using innerHTML (Trusted Types safe)
    except Exception as e:
        audit.log("OVERLAY_EVAL_FAIL", error=repr(e))
        raise