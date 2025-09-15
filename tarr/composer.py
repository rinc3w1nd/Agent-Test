from typing import List
from .tarr_selectors import COMPOSER_CANDIDATES

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