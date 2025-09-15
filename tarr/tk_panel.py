# tarr/tk_panel.py
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from typing import Dict, Callable
from concurrent.futures import Future
import datetime as dt

from .composer import _strip_bot_directive, focus_composer, insert_text_10ms, paste_from_clipfile, paste_payload
#from .mention import bind
from .graph_watch import GraphWatcher
from .artifacts import append_text, append_html, screenshot
from .utils import now_ts_run
from .tarr_selectors import COMPOSER_CANDIDATES

class MyAskString(simpledialog._QueryString):
    def body(self, master):
        entry = super().body(master)
        # Bind Enter at the toplevel level instead of just the Entry widget
        self.bind("<Return>", lambda event: self.ok())
        return entry

def askstring(title, prompt, **kw):
    d = MyAskString(title, prompt, **kw)
    return d.result

def _dbg(msg: str):
    if os.environ.get("TARR_VERBOSE", "1") != "0":
        print(f"[DBG][TK] {msg}", flush=True)

def _post(loop, coro) -> Future:
    import asyncio
    # schedule coroutine on the Playwright loop (running in background thread)
    return asyncio.run_coroutine_threadsafe(coro, loop)

async def _flash_composer(page) -> bool:
    for sel in COMPOSER_CANDIDATES:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=500)
            ok = await loc.evaluate("""(el)=>{
              try{
                el.scrollIntoView({block:'center', inline:'nearest'});
                const po = el.style.outline, ps = el.style.boxShadow;
                el.style.outline='3px solid #00d4ff';
                el.style.boxShadow='0 0 0 3px rgba(0,212,255,0.2), 0 0 12px rgba(0,212,255,0.6)';
                setTimeout(()=>{ el.style.outline=po; el.style.boxShadow=ps; }, 1200);
                return true;
              }catch(e){ return false; }
            }""")
            if ok:
                return True
        except Exception:
            continue
    return False

def start_tk_panel(loop, page, cfg: Dict, audit, corpus_ctrl):
    """
    Tk must be created on the MAIN THREAD (macOS/Cocoa rule).
    This function blocks inside root.mainloop(), while Playwright runs in a bg loop.
    """
    _dbg("starting start_tk_panel()")

    # --- State (incl. Graph reply cache) ---
    state = {
        "auto_send": False,
        "last_hint": "",
        "last_sent_utc": None,
        "team_id": None,
        "channel_id": None,
        "last_root_id": None,
        "last_reply": None,
        "last_all_replies": None,
    }

    gw = GraphWatcher(
        tenant_id = cfg.get("graph_tenant_id",""),
        client_id = cfg.get("graph_client_id",""),
        scopes    = cfg.get("graph_scopes", ["ChannelMessage.Read.All"]),
        cache_path= cfg.get("graph_cache_path", "auth/msal_token.json"),
    )

    # --- Tk (main thread) ---
    root = tk.Tk()
    root.title("TARR Controls (Tk)")
    root.geometry("760x250")  # 5-row layout space

    msg = tk.StringVar(value="Ready.")
    pos = tk.StringVar(value="0/0")
    autosend_var = tk.BooleanVar(value=False)
    url_var = tk.StringVar(value=cfg.get("teams_channel_url",""))

    def set_msg(t): msg.set(t)

    def with_status(label: str, func: Callable):
        def w():
            try:
                set_msg(label + "…")
                func()
                set_msg(label + " ✓")
            except Exception as e:
                set_msg(f"{label} ✖")
                messagebox.showerror("Error", str(e))
        return w

    # ---- Actions ----
    def do_open_teams():
        _dbg("Open Teams clicked")
        _post(loop, page.goto(url_var.get())).result()

    def do_load():
        path = filedialog.askopenfilename(
            title="Load Corpus JSONL",
            filetypes=[("JSON Lines", "*.jsonl *.json"), ("All files", "*.*")]
        )
        if not path:
            return
        text = open(path, "r", encoding="utf-8").read()
        n = corpus_ctrl.load_jsonl(text)
        audit.log("CORPUS_LOAD", count=n, source=path)
        pos.set(f"1/{n}")
        set_msg(f"Loaded {n} items")

    def do_send_at():
        bot = cfg.get("bot_name","").strip()
        if not bot:
            raise RuntimeError("bot_name not set in config")
        clip_path = (cfg.get("clip_path") or "").strip()
        _dbg(f"Replay stored clip for @{bot} from {clip_path or '[MISSING clip_path]'}")
        ok = _post(loop, paste_from_clipfile(page, cfg, audit)).result()
        if not ok:
            raise RuntimeError("Replay of stored mention/card failed")

    def do_send_corpus():
        _dbg("Send Corpus clicked")
        row = corpus_ctrl.current()
        if not row:
            raise RuntimeError("No current corpus item")
        payload = _strip_bot_directive((row.get("payload","") or ""))
        if not payload.strip():
            audit.log("SEND_PREP", id=row.get("id",""), chars=0, via="tk", note="empty after strip")
            set_msg("Nothing to send (payload empty after @BOT strip).")
            return

    def do_send_both():
        """
        Paste the stored @BOT mention, move caret after the chip, then paste the corpus payload.
        Respects Auto-send toggle.
        """
        bot = cfg.get("bot_name","").strip()
        if not bot:
            raise RuntimeError("bot_name not set in config")

        # 1) Paste the stored rich clip (mention/card)
        _dbg(f"SendBoth: replay clip for @{bot}")
        ok = _post(loop, paste_from_clipfile(page, cfg, audit)).result()
        if not ok:
            raise RuntimeError("Replay of stored mention/card failed")

        # 2) Ensure caret is AFTER the mention chip (chip can be selected right after paste)
        #    Nudge right a few times and add a single space separator for sanity.
        for _ in range(3):
            page.keyboard.press("ArrowRight")
        page.keyboard.type(" ")

        # 3) Paste the current corpus payload (as html/plain -- here we reuse the same string for both)
        row = corpus_ctrl.current()
        if not row:
            raise RuntimeError("No current corpus item")
        payload = _strip_bot_directive((row.get("payload","") or ""))
        if not payload.strip():
            audit.log("SEND_PREP", id=row.get("id",""), chars=0, via="tk", note="empty after strip (SendBoth)")
            set_msg("Nothing to paste from corpus (empty after @BOT strip).")
            return

        _dbg(f"SendBoth: paste corpus id={row.get('id','')}, len={len(payload)}")
        ok2 = _post(loop, paste_payload(page, payload, payload, audit)).result()
        if not ok2:
            raise RuntimeError("Corpus paste failed (SendBoth)")

        audit.log("SEND_BOTH", id=row.get("id",""), chars=len(payload), via="tk")

        if state.get("auto_send"):
            page.keyboard.press("Enter")
            set_msg("Pasted mention + corpus and sent")
        else:
            set_msg("Pasted mention + corpus")

        # remember for Graph & clear reply cache
        state["last_hint"] = payload
        state["last_sent_utc"] = dt.datetime.now(dt.timezone.utc)
        state["last_root_id"] = None
        state["last_reply"] = None
        state["last_all_replies"] = None

        # focus HARD before typing
        foc = _post(loop, focus_composer(page)).result()
        audit.log("FOCUS", target="composer", ok=bool(foc), via="send_corpus")
        if not foc:
            raise RuntimeError("Composer not found")

        # guaranteed typing: fast insert + type(10ms)
        method = _post(loop, insert_text_10ms(page, " " + payload)).result()
        audit.log("SEND_PREP", id=row.get("id",""), method=method, chars=len(payload), via="tk")
        if method == "fail":
            set_msg("Typing failed -- composer refused input.")
            raise RuntimeError("Insert failed")

        if state["auto_send"]:
            try:
                _post(loop, page.keyboard.press("Control+Enter")).result()
                audit.log("SEND", id=row.get("id",""), method="Ctrl+Enter", via="tk")
                set_msg(f"Sent (method: {method})")
            except Exception:
                try:
                    _post(loop, page.locator(cfg.get("send_button_selector","[data-tid=\"send\"]")).click(timeout=1500)).result()
                    audit.log("SEND", id=row.get("id",""), method="click", via="tk")
                    set_msg(f"Sent (method: {method}, click)")
                except Exception:
                    _post(loop, page.keyboard.press("Enter")).result()
                    audit.log("SEND", id=row.get("id",""), method="Enter", via="tk")
                    set_msg(f"Sent (method: {method}, Enter)")
        else:
            set_msg(f"Typed (method: {method})")

    def do_prev():
        ok = corpus_ctrl.prev()
        audit.log("CORPUS_PREV", ok=ok, idx=corpus_ctrl.i, total=len(corpus_ctrl.items), via="tk")
        pos.set(f"{corpus_ctrl.i+1}/{len(corpus_ctrl.items)}")

    def do_next():
        ok = corpus_ctrl.next()
        audit.log("CORPUS_NEXT", ok=ok, idx=corpus_ctrl.i, total=len(corpus_ctrl.items), via="tk")
        pos.set(f"{corpus_ctrl.i+1}/{len(corpus_ctrl.items)}")

    async def _flash(page_obj):
        return await _flash_composer(page_obj)

    def do_find_composer():
        _dbg("Find Composer clicked")
        ok = _post(loop, _flash(page)).result()
        if not ok:
            raise RuntimeError("Composer not found (no candidates matched).")

    # ---- Graph helpers ----
    def _ensure_ids():
        if not state["team_id"]:
            team_name = cfg.get("graph_team_name")
            if not team_name:
                raise RuntimeError("graph_team_name not set")
            tid = gw.resolve_team_id(team_name)
            if not tid:
                raise RuntimeError(f"Team not found: {team_name}")
            state["team_id"] = tid
        if not state["channel_id"]:
            chan = cfg.get("graph_channel_name")
            if not chan:
                raise RuntimeError("graph_channel_name not set")
            cid = gw.resolve_channel_id(state["team_id"], chan)
            if not cid:
                raise RuntimeError(f"Channel not found: {chan}")
            state["channel_id"] = cid

    def do_poll_graph():
        _dbg("Poll Graph clicked")
        if not state["last_hint"] or not state["last_sent_utc"]:
            raise RuntimeError("No remembered payload to match. Send a corpus item first.")
        _ensure_ids()
        since = state["last_sent_utc"] - dt.timedelta(seconds=30)
        root_id = gw.find_recent_root_from_me(state["team_id"], state["channel_id"], since, state["last_hint"], max_checks=3)
        if not root_id:
            state["last_root_id"] = None
            state["last_reply"] = None
            state["last_all_replies"] = None
            raise RuntimeError("Could not locate the just-sent message in Graph.")
        timeout_s = int(cfg.get("graph_reply_timeout_s", 120))
        poll_every = float(cfg.get("graph_poll_every_s", 1.5))
        reply, all_replies = gw.wait_for_reply(state["team_id"], state["channel_id"], root_id,
                                               cfg.get("graph_bot_name",""), timeout_s, poll_every)
        state["last_root_id"] = root_id
        state["last_reply"] = reply
        state["last_all_replies"] = all_replies
        set_msg(f"Graph replies: {len(all_replies)} | {'FOUND' if reply else 'NO BOT REPLY'}")

    def do_record_graph():
        _dbg("Record Status clicked")
        row = corpus_ctrl.current() or {}
        rid = row.get("id","live")
        reply = state.get("last_reply")
        all_replies = state.get("last_all_replies") or []

        if reply is None:
            if not state["last_hint"] or not state["last_sent_utc"]:
                raise RuntimeError("No remembered payload to match. Send a corpus item first.")
            _ensure_ids()
            since = state["last_sent_utc"] - dt.timedelta(seconds=30)
            root_id = state.get("last_root_id") or gw.find_recent_root_from_me(
                state["team_id"], state["channel_id"], since, state["last_hint"], max_checks=3)
            if not root_id:
                raise RuntimeError("Could not locate the just-sent message in Graph. Try Poll Graph first.")
            timeout_s = int(cfg.get("graph_reply_timeout_s", 120))
            poll_every = float(cfg.get("graph_poll_every_s", 1.5))
            reply, all_replies = gw.wait_for_reply(state["team_id"], state["channel_id"], root_id,
                                                   cfg.get("graph_bot_name",""), timeout_s, poll_every)
            state["last_root_id"] = root_id
            state["last_reply"] = reply
            state["last_all_replies"] = all_replies

        text = (reply or {}).get("text","") or ""
        html = (reply or {}).get("html","") or ""
        note = simpledialog.askstring("Record Status", "Operator note (prefilled from reply):", initialvalue=text)
        run_ts = cfg.get("__run_ts__", now_ts_run())

        tpath = append_text(run_ts, rid, row, text, cfg.get("text_dir","artifacts/text"),
                            reply_detected=bool(text), reply_len=len(text), operator_note=(note or ""))
        hpath = None
        if html:
            hpath = append_html(run_ts, rid, html, cfg.get("html_dir","artifacts/html"))
        ss_ts = now_ts_run()
        spath = _post(loop, screenshot(ss_ts, rid, page, cfg.get("screens_dir","artifacts/screens"))).result()

        audit.log("RECORD_GRAPH", id=rid, reply_detected=bool(text), reply_len=len(text),
                  text_path=str(tpath), html_path=str(hpath or ""), screenshot=str(spath),
                  note=(note or ""), replies_seen=len(all_replies))
        set_msg("Recorded artifacts (text/html/screenshot)")

    # ---- Grid (5 rows, 4 cols) ----
    frm = tk.Frame(root); frm.pack(fill="both", expand=True, padx=10, pady=10)

    # Row 0: URL + Open
    tk.Label(frm, text="Teams URL:").grid(row=0, column=0, sticky="e")
    tk.Entry(frm, textvariable=url_var, width=26).grid(row=0, column=1, columnspan=2, sticky="we", padx=6)
    tk.Button(frm, text="Open Teams", width=14, command=with_status("Open Teams", do_open_teams)).grid(row=0, column=3, padx=6, pady=4)

    # Row 1: Corpus load + position + autosend
    tk.Button(frm, text="Load Corpus", width=14, command=with_status("Load", do_load)).grid(row=1, column=0, padx=6, pady=4)
    tk.Label(frm, text="Index:").grid(row=1, column=1, sticky="e")
    tk.Label(frm, textvariable=pos, anchor="w").grid(row=1, column=2, sticky="w")
    tk.Checkbutton(frm, text="Auto-send", variable=autosend_var,
                   command=lambda: (state.update({"auto_send": bool(autosend_var.get())}),
                                    audit.log("AUTO_SEND", enabled=bool(autosend_var.get()), via="tk"))
    ).grid(row=1, column=3, padx=6, pady=4)

    # Row 2: Send controls + helpers
    tk.Button(frm, text="Send @BOT", width=14, command=with_status("Bind", do_send_at)).grid(row=2, column=0, padx=6, pady=4)
    tk.Button(frm, text="Send Corpus", width=14, command=with_status("Send", do_send_corpus)).grid(row=2, column=1, padx=6, pady=4)
    tk.Button(frm, text="Send Both", width=14, command=with_status("Send", do_send_both)).grid(row=2, column=3, padx=6, pady=4)

    #Row 3: Diag stuff
    tk.Button(frm, text="Find Composer", width=14, command=with_status("Find", do_find_composer)).grid(row=3, column=0, padx=6, pady=4)
    tk.Button(frm, text="Poll Graph", width=14, command=with_status("Poll Graph", do_poll_graph)).grid(row=3, column=1, padx=6, pady=4)

    # Row 4: Corpus navigation
    tk.Button(frm, text="Prev Corpus", width=14, command=with_status("Prev", do_prev)).grid(row=4, column=0, padx=6, pady=4)
    tk.Button(frm, text="Next Corpus", width=14, command=with_status("Next", do_next)).grid(row=4, column=1, padx=6, pady=4)

    # Row 5: Record + status
    tk.Button(frm, text="Record Status (Graph)", width=20, command=with_status("Record", do_record_graph)).grid(row=5, column=0, padx=6, pady=8)
    tk.Label(frm, textvariable=msg, anchor="w").grid(row=5, column=1, columnspan=3, sticky="we")

    _dbg("Tk window created; entering mainloop()")
    root.mainloop()
    _dbg("mainloop() exited")