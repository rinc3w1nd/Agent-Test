from typing import Dict
from .tarr_selectors import COMPOSER_CANDIDATES, MENTION_POPUP

async def _focus_any_composer(page) -> bool:
    """Try multiple selectors; ensure real focus on the contenteditable."""
    for sel in COMPOSER_CANDIDATES:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=1500)
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

def _pick(cfg: Dict, key: str, fast: bool, default):
    if fast:
        v = cfg.get(f"{key}_fast", None)
        if v is not None:
            return v
    return cfg.get(key, default)

async def bind(page, bot_name: str, cfg: Dict, audit, fast: bool = False) -> bool:
    """
    Bind the @mention for bot_name.
    - In fast mode, waits are minimal for snappy operator UX.
    - Always ensures composer focus before typing.
    """
    delay_before = int(_pick(cfg, "mention_delay_before_at_ms", fast, 15000))
    char_delay   = int(_pick(cfg, "mention_type_char_delay_ms", fast, 35))
    popup_wait   = int(_pick(cfg, "mention_popup_wait_ms", fast, 5000))
    retype_wait  = int(_pick(cfg, "mention_retype_wait_ms", fast, 500))
    backoff      = bool(cfg.get("mention_retype_backoff", True))
    windows      = _pick(cfg, "mention_attempt_windows_ms", fast, [5000, 5000, 5000])

    focused = await _focus_any_composer(page)
    audit.log("FOCUS", target="composer", ok=focused)

    if delay_before > 0:
        await page.wait_for_timeout(delay_before)

    for attempt, pre_wait in enumerate(windows, 1):
        if pre_wait > 0:
            await page.wait_for_timeout(int(pre_wait))

        ok = False
        try:
            await page.keyboard.type("@", delay=char_delay)
            await page.keyboard.type(bot_name, delay=char_delay)
        except Exception:
            pass

        try:
            await page.locator(MENTION_POPUP).first.wait_for(timeout=int(popup_wait))
        except Exception:
            pass

        try:
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            ok = True
        except Exception:
            ok = False

        if ok:
            audit.log("BIND", attempt=attempt, window_ms=pre_wait, result="success", fast=fast)
            return True

        audit.log("BIND", attempt=attempt, window_ms=pre_wait, result="retry", fast=fast)
        try:
            for _ in range(len(bot_name)):
                await page.keyboard.press("Backspace")
        except Exception:
            pass
        await page.wait_for_timeout(int(retype_wait) * (attempt if backoff else 1))

    audit.log("BIND", result="fail", fast=fast)
    return False