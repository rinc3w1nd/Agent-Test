from typing import List
from .tarr_selectors import COMPOSER_CANDIDATES

async def _focus_first_composer(page):
    """
    Minimal helper: focus the first visible composer from COMPOSER_CANDIDATES.
    Returns True on success.
    """
    for sel in COMPOSER_CANDIDATES:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=500)
            await loc.click()
            focused = await loc.evaluate("el => (el === document.activeElement)")
            if not focused:
                await loc.evaluate("el => { el.focus(); }")
                focused = await loc.evaluate("el => (el === document.activeElement)")
            if focused:
                return True
        except Exception:
            continue
    return False

def _strip_bot_directive(s: str) -> str:
    """
    Remove a leading '@bot' directive if present (case-insensitive),
    allowing optional punctuation and zero-width characters after it.
    """
    if not s:
        return ""
    import re
    s = s.lstrip("\u200b\u2060\ufeff \t\r\n")
    return re.sub(
        r"^@bot[\s\u200b\u2060\ufeff]*[:,\-\u2013\u2014]*[\s\u200b\u2060\ufeff]*",
        "",
        s,
        flags=re.IGNORECASE,
    )

async def focus_composer(page) -> bool:
    """
    Single-pass, fast attempt to find and focus the Teams composer.
    No retries/backoffs — if not found immediately, return False.
    """
    for sel in COMPOSER_CANDIDATES:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=500)
            await loc.click()
            focused = await loc.evaluate("el => (el === document.activeElement)")
            if not focused:
                await loc.evaluate("el => { el.focus(); }")
                focused = await loc.evaluate("el => (el === document.activeElement)")
            if focused:
                return True
        except Exception:
            continue
    return False

# tarr/composer.py

from .tarr_selectors import COMPOSER_CANDIDATES

def _strip_bot_directive(s: str) -> str:
    if not s:
        return ""
    import re
    s = s.lstrip("\u200b\u2060\ufeff \t\r\n")
    return re.sub(
        r"^@bot[\s\u200b\u2060\ufeff]*[:,\-\u2013\u2014]*[\s\u200b\u2060\ufeff]*",
        "",
        s,
        flags=re.IGNORECASE,
    )

async def focus_composer(page) -> bool:
    for sel in COMPOSER_CANDIDATES:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=500)
            await loc.click()
            focused = await loc.evaluate("el => (el === document.activeElement)")
            if not focused:
                await loc.evaluate("el => { el.focus(); }")
                focused = await loc.evaluate("el => (el === document.activeElement)")
            if focused:
                return True
        except Exception:
            continue
    return False

async def insert_text_10ms(page, text: str) -> str:
    """
    Insert text with best effort:
      1) keyboard.insertText (fast)
      2) document.execCommand('insertText') (fast)
      3) keyboard.type at 10 ms/char (guaranteed attempt)
    Returns a composite method string like 'insertText+keyboard.type' or 'keyboard.type'
    so you can see exactly what ran.
    """
    methods = []

    # 1) Fast path: Playwright's insertText
    try:
        await page.keyboard.insertText(text)
        methods.append("insertText")
    except Exception:
        pass

    # 2) Fallback fast path: execCommand
    if not methods:
        try:
            ok = await page.evaluate(
                """(t) => { try { document.execCommand('insertText', false, t); return true; }
                           catch(e){ return false; } }""",
                text,
            )
            if ok:
                methods.append("execCommand")
        except Exception:
            pass

    # 3) Always finish with a real type to guarantee characters appear
    try:
        await page.keyboard.type(text, delay=10)  # fixed 10ms/char
        methods.append("keyboard.type")
    except Exception:
        if not methods:
            return "fail"
        # we at least ran one fast method; report that
        return "+".join(methods)

    return "+".join(methods) if methods else "fail"

async def paste_from_clipfile(page, cfg: dict, audit) -> bool:
    """
    Load a previously captured clip file (YAML or JSON) produced by pb_capture.py
    and synthesize a paste into the Teams composer using text/html (+ text/plain fallback).
    Config key: cfg['clip_path'] (path string). Returns True if paste event dispatched.
    """
    from pathlib import Path
    import json
    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None
        
async def paste_payload(page, html: str, plain: str = "", audit=None) -> bool:
    """
    Paste arbitrary payload into the Teams composer via synthetic paste.
    `html` is required (can be plain text too), `plain` is optional fallback.
    """
    # Reuse your focus helper
    ok = await _focus_first_composer(page)
    if not ok:
        if audit: audit.log("PASTE_PREP_FAIL", reason="composer_not_found")
        return False

    try:
        await page.evaluate(
            """({html, plain}) => {
                const el = document.querySelector('[contenteditable="true"][role="textbox"]');
                if (!el) throw new Error('Composer not found');
                const dt = new DataTransfer();
                dt.setData('text/html', html);
                if (plain) dt.setData('text/plain', plain);
                const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
                el.dispatchEvent(ev);
            }""",
            {"html": html, "plain": plain},
        )
        if audit: audit.log("PASTE_PAYLOAD", ok=True, bytes_html=len(html), bytes_plain=len(plain))
        return True
    except Exception as e:
        if audit: audit.log("PASTE_PAYLOAD_FAIL", ok=False, error=repr(e))
        return False 

    clip_path = (cfg.get("clip_path") or "").strip()
    if not clip_path:
        raise RuntimeError("clip_path not set in config")
    p = Path(clip_path)
    if not p.exists():
        raise FileNotFoundError(f"clip payload not found: {p}")

    text = p.read_text(encoding="utf-8", errors="replace")
    data = None
    if p.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("pyyaml not installed; run: pip install pyyaml")
        data = yaml.safe_load(text)
    elif p.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        # try YAML first (common in your flow), else JSON
        if yaml is not None:
            try:
                data = yaml.safe_load(text)
            except Exception:
                data = None
        if data is None:
            data = json.loads(text)

    html  = (data.get("text/html")  or data.get("html")  or "").strip()
    plain = (data.get("text/plain") or data.get("plain") or "").strip()
    if not html:
        raise ValueError("No text/html found in clip payload")

    ok = await _focus_first_composer(page)
    if not ok:
        audit.log("PASTE_PREP_FAIL", reason="composer_not_found")
        return False

    # Dispatch a synthetic paste carrying our stored flavors
    try:
        await page.evaluate(
            """({html, plain}) => {
                const el = document.querySelector('[contenteditable="true"][role="textbox"]');
                if (!el) throw new Error('Composer not found');
                const dt = new DataTransfer();
                dt.setData('text/html', html);
                if (plain) dt.setData('text/plain', plain);
                const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
                el.dispatchEvent(ev);
            }""",
            {"html": html, "plain": plain},
        )
        audit.log("PASTE_REPLAY", ok=True, bytes_html=len(html), bytes_plain=len(plain))
        return True
    except Exception as e:
        audit.log("PASTE_REPLAY_FAIL", ok=False, error=repr(e))
        return False