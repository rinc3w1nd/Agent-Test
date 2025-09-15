from typing import Dict
from .tarr_selectors import COMPOSER_CANDIDATES, MENTION_POPUP, MENTION_OPTION, MENTION_PILL

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

async def bind(page, bot_name: str, cfg: Dict, audit, fast: bool = True) -> bool:
    """
    Bind an @mention for the given bot name WITHOUT EVER PRESSING ENTER.
    Steps:
      1) Focus composer (single pass).
      2) Type '@' + bot_name at 50 ms/char.
      3) Wait briefly for the suggestion popup.
      4) Click the best matching suggestion (mouse click), never Enter.
      5) Verify a mention pill exists; if not, backspace cleanup and fail.
    """
    # fixed typing delay per spec
    char_delay = int(cfg.get("mention_type_char_delay_ms_fast", 50))

    focused = await _focus_any_composer(page)
    audit.log("FOCUS", target="composer", ok=focused, via="bind")
    if not focused:
        return False

    # Step 2: type "@<name>"
    try:
        await page.keyboard.type("@", delay=char_delay)
        await page.keyboard.type(bot_name, delay=char_delay)
    except Exception:
        return False

    # Step 3: wait for popup (short timeout so we don't hang)
    popup = page.locator(MENTION_POPUP).first
    try:
        await popup.wait_for(timeout=100)
    except Exception:
        # no popup; cleanup and fail
        try:
            for _ in range(len(bot_name) + 1):
                await page.keyboard.press("Backspace")
        except Exception:
            pass
        audit.log("BIND", result="no_popup", fast=fast)
        return False

    # Step 4: click the best matching suggestion
    try:
        # Prefer exact/contains text match if supported
        options = page.locator(MENTION_OPTION)
        # Try to click one that includes the bot name (case-insensitive)
        try:
            target = options.filter(has_text=bot_name).first
            await target.click(timeout=800)
        except Exception:
            # Fallback: click the very first option
            await options.first.click(timeout=800)
    except Exception:
        # cleanup backspace to avoid litter
        try:
            for _ in range(len(bot_name) + 1):
                await page.keyboard.press("Backspace")
        except Exception:
            pass
        audit.log("BIND", result="click_fail", fast=fast)
        return False

    # Step 5: verify a mention pill exists in the composer (best-effort)
    try:
        # Look briefly for a mention pill being inserted
        pill = page.locator(MENTION_PILL).first
        await pill.wait_for(timeout=500)
        audit.log("BIND", result="success_click", fast=fast)
        return True
    except Exception:
        # Could not confirm; do NOT press Enter. Leave as-is, but report failure.
        audit.log("BIND", result="uncertain_no_pill", fast=fast)
        return False