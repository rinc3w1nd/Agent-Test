from typing import Dict
from .tarr_selectors import COMPOSER_CANDIDATES, MENTION_POPUP

async def _focus_any_composer(page) -> bool:
    """
    Single-pass composer focus for the mention flow.
    Mirrors composer.focus_composer but kept local to avoid circular import.
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

async def bind(page, bot_name: str, cfg: Dict, audit, fast: bool = True) -> bool:
    """
    Bind an @mention for the given bot name.
    - Types '@' + bot display name with fixed 10 ms/char.
    - Attempts a single ArrowDown+Enter to select the first suggestion.
    - On failure, backspaces the typed name to avoid litter.
    """
    char_delay = int(cfg.get("mention_type_char_delay_ms_fast", 10))  # fixed as per requirements
    focused = await _focus_any_composer(page)
    audit.log("FOCUS", target="composer", ok=focused, via="bind")
    if not focused:
        return False

    try:
        await page.keyboard.type("@", delay=char_delay)
        await page.keyboard.type(bot_name, delay=char_delay)
    except Exception:
        return False

    # Optional: wait briefly for popup (tiny timeout); then try to select top suggestion
    try:
        await page.locator(MENTION_POPUP).first.wait_for(timeout=250)
    except Exception:
        pass
    try:
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        audit.log("BIND", result="success", fast=fast)
        return True
    except Exception:
        # Cleanup: remove '@'+name so we don't leave trash in the composer
        try:
            for _ in range(len(bot_name) + 1):  # +1 for the '@'
                await page.keyboard.press("Backspace")
        except Exception:
            pass
        audit.log("BIND", result="fail", fast=fast)
        return False