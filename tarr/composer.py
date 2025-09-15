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

async def insert_text_10ms(page, text: str) -> str:
    """
    Insert text into the composer with preference for instant APIs.
    Falls back to keyboard.type at 10 ms/char to satisfy the timing spec.

    Returns one of: 'insertText' | 'execCommand' | 'keyboard.type' | 'fail'
    """
    try:
        await page.keyboard.insertText(text)
        return "insertText"
    except Exception:
        pass
    try:
        ok = await page.evaluate(
            """(t) => { try { document.execCommand('insertText', false, t); return true; }
                       catch(e){ return false; } }""",
            text,
        )
        if ok:
            return "execCommand"
    except Exception:
        pass
    try:
        await page.keyboard.type(text, delay=10)  # fixed 10ms/char
        return "keyboard.type"
    except Exception:
        return "fail"