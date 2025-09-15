# tarr/mention.py
from typing import Dict
from .tarr_selectors import COMPOSER_CANDIDATES, MENTION_POPUP, MENTION_OPTION, MENTION_PILL

POPUP_WAIT_MS_DEFAULT = 1200   # >= 1000ms as requested
TYPE_DELAY_MS_DEFAULT = 100    # 100ms per char as requested

async def _focus_any_composer(page) -> bool:
    """
    Single-pass composer focus for the mention flow.
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

async def _backspace_cleanup(page, bot_name: str):
    try:
        for _ in range(len(bot_name) + 1):  # +1 for '@'
            await page.keyboard.press("Backspace")
    except Exception:
        pass

async def bind(page, bot_name: str, cfg: Dict, audit, fast: bool = True) -> bool:
    """
    Bind an @mention for the given bot name WITHOUT EVER PRESSING ENTER.

    Steps:
      1) Focus composer (single pass).
      2) Type '@' + bot_name at 100 ms/char.
      3) Wait >= 1000 ms for the suggestion popup.
      4) Click the best matching suggestion (mouse click), never Enter.
      5) Verify a mention pill exists; if not, backspace cleanup and fail.
    """
    # Allow config overrides, but default to your requested timings.
    char_delay = int(cfg.get("mention_type_char_delay_ms_fast", TYPE_DELAY_MS_DEFAULT))
    popup_wait = int(cfg.get("mention_popup_wait_ms", POPUP_WAIT_MS_DEFAULT))
    if popup_wait < 1000:
        popup_wait = 1000  # enforce your "at least 1000ms"

    focused = await _focus_any_composer(page)
    audit.log("FOCUS", target="composer", ok=focused, via="bind")
    if not focused:
        return False

    # Step 2: type "@<name>" at 100ms/char
    try:
        await page.keyboard.type("@", delay=char_delay)
        await page.keyboard.type(bot_name, delay=char_delay)
    except Exception:
        return False

    # Step 3: wait for popup (>= 1000ms)
    popup = page.locator(MENTION_POPUP).first
    try:
        await popup.wait_for(timeout=popup_wait)
    except Exception:
        await _backspace_cleanup(page, bot_name)
        audit.log("BIND", result="no_popup", delay_ms=char_delay, wait_ms=popup_wait)
        return False

    # Step 4: click the best matching suggestion
    try:
        options = page.locator(MENTION_OPTION)
        # Prefer a text-matching option first
        try:
            target = options.filter(has_text=bot_name).first
            await target.click(timeout=800)
        except Exception:
            # Fallback to the very first option
            await options.first.click(timeout=800)
    except Exception:
        await _backspace_cleanup(page, bot_name)
        audit.log("BIND", result="click_fail", delay_ms=char_delay, wait_ms=popup_wait)
        return False

    # Step 5: verify pill insertion
    try:
        pill = page.locator(MENTION_PILL).first
        await pill.wait_for(timeout=700)
        audit.log("BIND", result="success_click", delay_ms=char_delay, wait_ms=popup_wait)
        return True
    except Exception:
        # Don’t press Enter; report uncertain state (composer contains plain text)
        audit.log("BIND", result="uncertain_no_pill", delay_ms=char_delay, wait_ms=popup_wait)
        return False