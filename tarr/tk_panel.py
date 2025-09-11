import threading, tkinter as tk
from tkinter import filedialog, messagebox
from concurrent.futures import Future
from typing import Dict, Callable

# Reuse your helpers so behavior matches the overlay exactly
from .overlay import _strip_bot_directive, _focus_composer, _insert_text_fast
from .mention import bind
from .capture import poll_latest_reply
from .artifacts import append_text, append_html, screenshot
from .utils import now_ts_run

def _post(loop, coro) -> Future:
    """Schedule a coroutine on the asyncio loop from the Tk thread."""
    import asyncio
    return asyncio.run_coroutine_threadsafe(coro, loop)

def start_tk_panel(loop, page, cfg: Dict, audit, corpus_ctrl):
    """
    Launch a Tk window in a separate thread that controls Playwright actions.
    Buttons: Load Corpus, Send @BOT, Send Corpus, Prev/Next, Record Status, Auto-send toggle.
    """
    state = {"auto_send": False}

    def ui_thread():
        root = tk.Tk()
        root.title("TARR Controls (External)")
        root.geometry("420x260")

        msg = tk.StringVar(value="Ready.")
        pos = tk.StringVar(value="0/0")
        autosend_var = tk.BooleanVar(value=False)

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

        def do_load():
            path = filedialog.askopenfilename(
                title="Load Corpus JSONL",
                filetypes=[("JSON Lines", "*.jsonl *.json"), ("All files", "*.*")]
            )
            if not path: 
                return
            text = open(path, "r", encoding="utf-8").read()
            def run():
                n = corpus_ctrl.load_jsonl(text)
                audit.log("CORPUS_LOAD", count=n, source=path)
                pos.set(f"0/{n}")
                set_msg(f"Loaded {n} items")
            run()

        def do_send_at():
            bot = cfg.get("bot_name","")
            def run():
                _post(loop, _focus_composer(page)).result()
                ok = _post(loop, bind(page, bot, cfg, audit, fast=bool(cfg.get("mention_fast_mode_on_ui", True)))).result()
                if not ok:
                    raise RuntimeError("Mention bind failed")
            run()

        def do_send_corpus():
            row = corpus_ctrl.current()
            if not row:
                raise RuntimeError("No current corpus item")
            payload = _strip_bot_directive((row.get("payload","") or ""))
            if not payload.strip():
                audit.log("SEND_PREP", id=row.get("id",""), chars=0, via="tk")
                return
            def run():
                _post(loop, _focus_composer(page)).result()
                method = _post(loop, _insert_text_fast(page, " " + payload)).result()
                audit.log("SEND_PREP", id=row.get("id",""), method=method, chars=len(payload), via="tk")
                if method == "fail":
                    raise RuntimeError("Insert failed")
                if state["auto_send"]:
                    try:
                        _post(loop, page.keyboard.press("Control+Enter")).result()
                        audit.log("SEND", id=row.get("id",""), method="Ctrl+Enter", via="tk")
                    except Exception:
                        try:
                            _post(loop, page.locator(cfg.get("send_button_selector","[data-tid=\"send\"]")).click(timeout=1500)).result()
                            audit.log("SEND", id=row.get("id",""), method="click", via="tk")
                        except Exception:
                            _post(loop, page.keyboard.press("Enter")).result()
                            audit.log("SEND", id=row.get("id",""), method="Enter", via="tk")
            run()

        def do_prev():
            ok = corpus_ctrl.prev()
            audit.log("CORPUS_PREV", ok=ok, idx=corpus_ctrl.i, total=len(corpus_ctrl.items), via="tk")
            pos.set(f"{corpus_ctrl.i}/{len(corpus_ctrl.items)}")

        def do_next():
            ok = corpus_ctrl.next()
            audit.log("CORPUS_NEXT", ok=ok, idx=corpus_ctrl.i, total=len(corpus_ctrl.items), via="tk")
            pos.set(f"{corpus_ctrl.i}/{len(corpus_ctrl.items)}")

        def do_record():
            row = corpus_ctrl.current() or {}
            rid = row.get("id","live")
            bot = cfg.get("bot_name","")
            # one-shot capture
            data = _post(loop, poll_latest_reply(page, bot, int(cfg.get("reply_timeout_ms", 120000)))).result()
            text = (data or {}).get("text","")
            html = (data or {}).get("html","")
            note = tk.simpledialog.askstring("Record Status", "Operator note (bot reply prefilled below):", initialvalue=text)
            run_ts = cfg.get("__run_ts__", now_ts_run())
            tpath = append_text(run_ts, rid, row, text, cfg.get("text_dir","artifacts/text"),
                                reply_detected=bool(text), reply_len=len(text), operator_note=(note or ""))
            hpath = None
            if html:
                hpath = append_html(run_ts, rid, html, cfg.get("html_dir","artifacts/html"))
            ss_ts = now_ts_run()
            spath = _post(loop, screenshot(ss_ts, rid, page, cfg.get("screens_dir","artifacts/screens"))).result()
            audit.log("RECORD", id=rid, reply_detected=bool(text), reply_len=len(text),
                      text_path=str(tpath), html_path=str(hpath or ""), screenshot=str(spath), note=(note or ""), via="tk")
            set_msg("Recorded.")

        # Layout
        frm = tk.Frame(root); frm.pack(fill="both", expand=True, padx=10, pady=10)
        tk.Button(frm, text="Load Corpus JSONL", width=36, command=with_status("Load", do_load)).grid(row=0, column=0, columnspan=2, pady=4)
        tk.Button(frm, text="Send @BOT", width=18, command=with_status("Bind", do_send_at)).grid(row=1, column=0, pady=4)
        tk.Button(frm, text="Send Corpus", width=18, command=with_status("Send", do_send_corpus)).grid(row=1, column=1, pady=4)
        tk.Button(frm, text="Prev Corpus", width=18, command=with_status("Prev", do_prev)).grid(row=2, column=0, pady=4)
        tk.Button(frm, text="Next Corpus", width=18, command=with_status("Next", do_next)).grid(row=2, column=1, pady=4)
        tk.Button(frm, text="Record Status", width=36, command=with_status("Record", do_record)).grid(row=3, column=0, columnspan=2, pady=8)

        tk.Checkbutton(frm, text="Auto-send after typing", variable=autosend_var,
                       command=lambda: state.update({"auto_send": bool(autosend_var.get())}) or audit.log("AUTO_SEND", enabled=bool(autosend_var.get()), via="tk")
        ).grid(row=4, column=0, columnspan=2, pady=4)

        tk.Label(frm, textvariable=msg, anchor="w").grid(row=5, column=0, columnspan=2, sticky="we")
        tk.Label(frm, textvariable=pos, anchor="e").grid(row=6, column=0, columnspan=2, sticky="we")

        root.mainloop()

    th = threading.Thread(target=ui_thread, name="TARR-Tk", daemon=True)
    th.start()
    return th