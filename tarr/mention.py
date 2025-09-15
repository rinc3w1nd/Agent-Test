# tarr/mention.py
from typing import Dict, Optional
from .tarr_selectors import COMPOSER_CANDIDATES, MENTION_POPUP, MENTION_OPTION, MENTION_PILL

POPUP_WAIT_MS_DEFAULT = 1200   # >= 1000ms so Teams can render suggestions
TYPE_DELAY_MS_DEFAULT  = 100   # 100ms/char per your request

async def _focus_any_composer(page) -> bool:
    for sel in COMPOSER_CANDIDATES:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=700)
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

async def _pick_target_option(page, bot_name: str):
    """
    Return a locator for the best matching option in the mention popup, or None.
    Prefers exact (case-insensitive), else contains.
    """
    options = page.locator(MENTION_OPTION)
    # Build two filters: exact (case-insensitive) and contains
    try:
        # Try exact (case-insensitive) via JS filter to handle nested text nodes reliably
        handle = await page.evaluate_handle(
            """(sel, name) => {
                const items = Array.from(document.querySelectorAll(sel));
                const n = name.trim().toLowerCase();
                const exact = items.find(el => (el.textContent || '').trim().toLowerCase() === n);
                if (exact) return exact;
                return items.find(el => (el.textContent || '').toLowerCase().includes(n)) || null;
            }""",
            MENTION_OPTION, bot_name
        )
        if handle:
            # Wrap back into a locator
            # Playwright python: we can use page.locator(":scope") with element handle via locator.set_checked? Not needed.
            # Instead, expose a data-attr temporarily to re-select as locator.
            await page.evaluate("(el)=>{ el.setAttribute('data-tarr-pick','1'); }", handle)
            target = page.locator(f"{MENTION_OPTION}[data-tarr-pick='1']").first
            return target
    except Exception:
        pass

    # Fallback to first option
    try:
        return options.first
    except Exception:
        return None

async def _click_robust(page, target) -> bool:
    """
    Try multiple click styles to overcome overlay/focus oddities.
    """
    # 1) Direct click
    try:
        await target.scroll_into_view_if_needed(timeout=800)
        await target.click(timeout=800)
        return True
    except Exception:
        pass

    # 2) Hover + mouse click at bbox center
    try:
        await target.scroll_into_view_if_needed(timeout=800)
        await target.hover(timeout=800)
        box = await target.bounding_box()
        if box and box.get("width") and box.get("height"):
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            await page.mouse.click(x, y)
            return True
    except Exception:
        pass

    # 3) In-page JS click (bypass hit testing)
    try:
        await page.evaluate(
            """(el) => {
                el.scrollIntoView({block:'nearest', inline:'nearest'});
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true}));
                el.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true, cancelable:true}));
                el.click();
            }""",
            target
        )
        return True
    except Exception:
        pass

    return False

async def bind(page, bot_name: str, cfg: Dict, audit, fast: bool = True) -> bool:
    """
    Bind an @mention WITHOUT pressing Enter by default.
    Steps:
      1) Focus composer.
      2) Type '@' + bot_name at 100ms/char.
      3) Wait >=1000ms for popup.
      4) Pick best option and click robustly (multi-style).
      5) Verify mention pill; else cleanup and (optionally) Enter fallback if enabled.
    YAML overrides (optional):
      mention_type_char_delay_ms_fast: 100
      mention_popup_wait_ms: 1200
      mention_bind_allow_enter: false
    """
    char_delay = int(cfg.get("mention_type_char_delay_ms_fast", TYPE_DELAY_MS_DEFAULT))
    popup_wait = int(cfg.get("mention_popup_wait_ms", POPUP_WAIT_MS_DEFAULT))
    allow_enter = bool(cfg.get("mention_bind_allow_enter", False))
    if popup_wait < 1000:
        popup_wait = 1000  # enforce lower bound

    focused = await _focus_any_composer(page)
    audit.log("FOCUS", target="composer", ok=focused, via="bind")
    if not focused:
        return False

    # Type "@name" at requested cadence
    try:
        await page.keyboard.type("@", delay=char_delay)
        await page.keyboard.type(bot_name, delay=char_delay)
    except Exception:
        return False

    # Wait for popup to be present
    popup = page.locator(MENTION_POPUP).first
    try:
        await popup.wait_for(timeout=popup_wait)
    except Exception:
        await _backspace_cleanup(page, bot_name)
        audit.log("BIND", result="no_popup", delay_ms=char_delay, wait_ms=popup_wait)
        return False

    # Select target option and click robustly
    target = await _pick_target_option(page, bot_name)
    if not target:
        await _backspace_cleanup(page, bot_name)
        audit.log("BIND", result="no_option", delay_ms=char_delay, wait_ms=popup_wait)
        return False

    ok = await _click_robust(page, target)
    if not ok:
        await _backspace_cleanup(page, bot_name)
        audit.log("BIND", result="click_fail", delay_ms=char_delay, wait_ms=popup_wait)
        return False

    # Verify pill presence (best effort)
    try:
        pill = page.locator(MENTION_PILL).first
        await pill.wait_for(timeout=900)
        audit.log("BIND", result="success_click", delay_ms=char_delay, wait_ms=popup_wait)
        return True
    except Exception:
        # Optional Enter fallback, only if explicitly allowed
        if allow_enter:
            try:
                await page.keyboard.press("Enter")
                # Re-check pill
                pill = page.locator(MENTION_PILL).first
                await pill.wait_for(timeout=900)
                audit.log("BIND", result="success_enter_fallback", delay_ms=char_delay, wait_ms=popup_wait)
                return True
            except Exception:
                pass
        # Cleanup and fail
        await _backspace_cleanup(page, bot_name)
        audit.log("BIND", result="uncertain_no_pill", delay_ms=char_delay, wait_ms=popup_wait)
        return False