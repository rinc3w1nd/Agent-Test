# injectors.py patched full
from typing import Dict, Any

async def _remove_node_by_id(page, elt_id: str) -> bool:
    try:
        return await page.evaluate("""
            (id) => { const el = document.getElementById(id); if (el) { el.remove(); return true; } return false; }
        """, elt_id)
    except Exception:
        return False

async def inject_overlay_js(page, cfg: Dict[str, Any], audit, overwrite: bool = False):
    if overwrite:
        removed = await _remove_node_by_id(page, "tarr-overlay")
        audit.log("OVERLAY_REMOVE", existed=bool(removed))

    # Trusted-Types safe overlay (no innerHTML)
    js = r"""
(() => {
  const css = (el, styles) => Object.assign(el.style, styles);
  const btn = (id, label, styles = {}) => {
    const b = document.createElement('button');
    b.id = id; b.type = 'button'; b.textContent = label;
    css(b, Object.assign({
      background:'#333', color:'#eee', border:'0', borderRadius:'6px',
      padding:'6px 8px', cursor:'pointer', flex:'1 1 46%'
    }, styles));
    return b;
  };
  const row = (gap='6px', wrap=true) => {
    const d = document.createElement('div');
    css(d, { display:'flex', gap, flexWrap:(wrap?'wrap':'nowrap'), marginBottom:'8px' });
    return d;
  };

  const box = document.createElement('div');
  box.id = 'tarr-overlay';
  css(box, {
    position:'fixed', right:'16px', bottom:'16px', width:'360px', zIndex:'2147483647',
    background:'#161616', color:'#eee', border:'1px solid #444', borderRadius:'8px',
    padding:'10px', fontFamily:'system-ui,Segoe UI,Roboto,Arial', boxShadow:'0 4px 16px rgba(0,0,0,.4)'
  });

  const POS_KEY='tarrOverlayPos';
  const restorePos=()=>{ try{
    const saved=JSON.parse(localStorage.getItem(POS_KEY)||'null');
    if(!saved) return;
    box.style.right='auto'; box.style.bottom='auto';
    box.style.left=saved.left+'px'; box.style.top=saved.top+'px';
  }catch{} };
  const savePos=()=>{ try{
    const r=box.getBoundingClientRect();
    localStorage.setItem(POS_KEY, JSON.stringify({left:Math.round(r.left), top:Math.round(r.top)}));
  }catch{} };

  const header=row('6px', false);
  Object.assign(header.style, { alignItems:'center', justifyContent:'space-between', marginBottom:'6px', cursor:'move' });
  const title=document.createElement('strong'); title.textContent='Recon Controls';
  const close=document.createElement('button'); close.textContent='✕';
  Object.assign(close.style,{ background:'#333', color:'#ccc', border:'0', borderRadius:'4px', padding:'2px 6px', flex:'0 0 auto' });
  header.append(title, close);

  let drag=null;
  header.addEventListener('pointerdown', ev=>{
    if(ev.target===close) return;
    ev.preventDefault();
    const r=box.getBoundingClientRect();
    drag={dx:ev.clientX-r.left, dy:ev.clientY-r.top};
    header.setPointerCapture(ev.pointerId);
  });
  header.addEventListener('pointermove', ev=>{
    if(!drag) return;
    ev.preventDefault();
    const vw=document.documentElement.clientWidth||window.innerWidth;
    const vh=document.documentElement.clientHeight||window.innerHeight;
    const nl=Math.max(0, Math.min(vw-box.offsetWidth , ev.clientX-drag.dx));
    const nt=Math.max(0, Math.min(vh-box.offsetHeight, ev.clientY-drag.dy));
    box.style.right='auto'; box.style.bottom='auto';
    box.style.left=nl+'px'; box.style.top=nt+'px';
  });
  const endDrag=ev=>{ if(!drag) return; try{header.releasePointerCapture(ev.pointerId);}catch{} drag=null; savePos(); };
  header.addEventListener('pointerup', endDrag);
  header.addEventListener('pointercancel', endDrag);

  const controls=row();
  const mk = (id, label, cb, styles={}) => { const b = btn(id, label, styles); b.addEventListener('click', cb); return b; };
  const call = async (name, ...args) => {
    if (typeof window[name] !== 'function') { alert(name+' not available'); return {ok:false, reason:'no-func'}; }
    return await window[name](...args);
  };

  const file = document.createElement('input');
  file.type='file'; file.accept='.json,.jsonl,application/json'; file.id='rc-file'; file.style.display='none';
  file.addEventListener('change', async ()=>{
    const f = file.files && file.files[0]; if (!f) return;
    const text = await f.text(); await call('pyLoadCorpus', text);
  });

  const loadBtn=mk('rc-load','Load Corpus JSONL', ()=>document.getElementById('rc-file').click(), { background:'#5e35b1', color:'#fff', flex:'1 1 100%' });
  const atBtn  =mk('rc-at','Send @BOT',        ()=>call('pySendAtOnly'), { background:'#1565c0', color:'#fff' });
  const sendBtn=mk('rc-send','Send Corpus',    ()=>call('pySendCorpus'), { background:'#2e7d32', color:'#fff' });
  const prevBtn=mk('rc-prev','Prev Corpus',    ()=>call('pyPrevCorpus'), { background:'#8d6e63', color:'#fff' });
  const nextBtn=mk('rc-next','Next Corpus',    ()=>call('pyNextCorpus'), { background:'#ef6c00', color:'#fff' });
  const statBtn=mk('rc-stat','Record Status',  ()=>call('pyRecordStatus'), { background:'#455a64', color:'#fff', flex:'1 1 100%' });

  controls.append(file, loadBtn, atBtn, sendBtn, prevBtn, nextBtn, statBtn);

  const label=document.createElement('label');
  Object.assign(label.style,{ display:'flex', alignItems:'center', gap:'6px', fontSize:'12px', marginBottom:'8px' });
  const auto=document.createElement('input'); auto.type='checkbox'; auto.id='rc-auto';
  const autoTxt=document.createElement('span'); autoTxt.textContent='Auto-send after typing';
  label.append(auto, autoTxt);
  auto.addEventListener('change', ()=>call('pyToggleAutoSend', !!auto.checked));

  const info=document.createElement('div'); Object.assign(info.style,{ fontSize:'12px', color:'#bbb', lineHeight:'1.35' });
  const posLine=document.createElement('div');
  const posLbl=document.createElement('span'); posLbl.textContent='Pos: ';
  const pos=document.createElement('span'); pos.id='rc-pos'; pos.textContent='0';
  const slash=document.createElement('span'); slash.textContent='/';
  const tot=document.createElement('span'); tot.id='rc-total'; tot.textContent='0';
  posLine.append(posLbl, pos, slash, tot);
  const msg=document.createElement('div'); msg.id='rc-msg'; Object.assign(msg.style,{ marginTop:'4px' });
  info.append(posLine, msg);

  const old = document.getElementById('tarr-overlay'); if (old) old.remove();
  box.append(header, controls, label, info);
  document.body.appendChild(box);
  restorePos();

  const rect = box.getBoundingClientRect();
  const vw = document.documentElement.clientWidth || window.innerWidth;
  const vh = document.documentElement.clientHeight || window.innerHeight;
  if (rect.right < 50 || rect.bottom < 50 || rect.left > vw-50 || rect.top > vh-50) {
    box.style.left='auto'; box.style.top='auto'; box.style.right='16px'; box.style.bottom='16px';
    try { localStorage.removeItem('tarrOverlayPos'); } catch {}
  }
})();
"""
    try:
        await page.evaluate(js)
        audit.log("OVERLAY_INJECT", ok=True, overwrite=overwrite)
        return {"ok": True}
    except Exception as e:
        audit.log("OVERLAY_INJECT_FAIL", ok=False, overwrite=overwrite, error=repr(e))
        return {"ok": False, "error": repr(e)}

async def inject_observer_js(page, audit, overwrite: bool = True, mode: str = "content"):
    js_body = r"""
window.__TARR_OBSERVER = async (botNameLower) => {
  const out = [];
  const items = document.querySelectorAll('[role="listitem"], [data-tid="message"]');
  for (const it of items) {
    const who = (it.querySelector('[data-tid="messageAuthor"]') || it.querySelector('[aria-label]'))?.textContent?.trim() || '';
    const aria = it.getAttribute('aria-label') || '';
    const bodyNode = it.querySelector('[data-tid="messageBody"]') || it.querySelector('[data-tid="messageText"]') || it;
    const html = bodyNode ? (bodyNode.innerHTML || '') : '';
    out.push({ who, aria, html });
  }
  const bot = (out.reverse().find(x => (x.who||'').toLowerCase() === botNameLower) || null);
  return bot;
};
"""
    try:
        if overwrite:
            await page.evaluate("() => { try { delete window.__TARR_OBSERVER; } catch (e) {} }")
        if mode == "init":
            await page.add_init_script(js_body)
        else:
            await page.add_script_tag(content=js_body)
        audit.log("OBSERVER_INJECT", ok=True, overwrite=overwrite, mode=mode)
        return {"ok": True}
    except Exception as e:
        audit.log("OBSERVER_INJECT_FAIL", ok=False, overwrite=overwrite, mode=mode, error=repr(e))
        return {"ok": False, "error": repr(e)}

async def remove_overlay(page, audit) -> bool:
    ok = await _remove_node_by_id(page, "tarr-overlay")
    audit.log("OVERLAY_REMOVE", existed=ok)
    return ok