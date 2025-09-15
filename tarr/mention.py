# tarr/mention.py
from typing import Dict, Optional
from .tarr_selectors import COMPOSER_CANDIDATES, MENTION_POPUP, MENTION_OPTION, MENTION_PILL

# Tunables (override via YAML if you like)
POPUP_WAIT_MS_DEFAULT  = 1200   # >= 1000ms so Teams can render suggestions
TYPE_DELAY_MS_DEFAULT  = 100    # 100ms/char for reliability
POST_CLICK_WAIT_MS_DEF = 1500   # wait after clicking suggestion for pill to appear
PILL_VERIFY_MS_DEF     = 1200   # time window to detect mention pill

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
    # Try JS side to pick exact/contains, then tag it so we can build a locator.
    handle = None
    try:
        handle = await page.evaluate_handle(
            """(sel, name) => {
                const items = Array.from(document.querySelectorAll(sel));
                const n = (name || '').trim().toLowerCase();
                const exact = items.find(el => (el.textContent || '').trim().toLowerCase() === n);
                if (exact) return exact;
                return items.find(el => (el.textContent || '').toLowerCase().includes(n)) || null;
            }""",
            MENTION_OPTION, bot_name
        )
        if handle:
            await page.evaluate("(el)=>{ if(el) el.setAttribute('data-tarr-pick','1'); }", handle)
            return page.locator(f"{MENTION_OPTION}[data-tarr-pick='1']").first
    except Exception:
        pass
    # Fallback to first option
    return page.locator(MENTION_OPTION).first

async def _click_robust(page, target) -> bool:
    # 1) Direct click
    try:
        await target.scroll_into_view_if_needed(timeout=1000)
        await target.click(timeout=1000)
        return True
    except Exception:
        pass
    # 2) Hover + mouse center click
    try:
        await target.scroll_into_view_if_needed(timeout=1000)
        await target.hover(timeout=1000)
        box = await target.bounding_box()
        if box and box.get("width") and box.get("height"):
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            await page.mouse.click(x, y)
            return True
    except Exception:
        pass
    # 3) JS-dispatched click
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
        return False

async def _composer_text_and_html(page):
    # Read both plain text and innerHTML to detect change even if pill is not yet stylized.
    try:
        return await page.evaluate(
            """() => {
                const ed = document.activeElement;
                const text = ed ? (ed.innerText || ed.textContent || '') : '';
                const html = ed ? (ed.innerHTML || '') : '';
                return {text, html};
            }"""
        )
    except Exception:
        return {"text":"", "html":""}

async def bind(page, bot_name: str, cfg: Dict, audit, fast: bool = True) -> bool:
    """
    Bind an @mention WITHOUT pressing Enter by default.
    Strategy: type @name slowly, wait for popup, robust-click the best option,
    then verify via pill OR HTML change; only cleanup if still clearly unbound.
    YAML overrides (optional):
      mention_type_char_delay_ms_fast: 100
      mention_popup_wait_ms: 1200
      mention_post_click_wait_ms: 1500
      mention_pill_verify_ms: 1200
      mention_bind_allow_enter: false
      mention_no_cleanup_on_uncertain: false
    """
    char_delay = int(cfg.get("mention_type_char_delay_ms_fast", TYPE_DELAY_MS_DEFAULT))
    popup_wait = int(cfg.get("mention_popup_wait_ms", POPUP_WAIT_MS_DEFAULT))
    post_wait  = int(cfg.get("mention_post_click_wait_ms", POST_CLICK_WAIT_MS_DEF))
    pill_verify_ms = int(cfg.get("mention_pill_verify_ms", PILL_VERIFY_MS_DEF))
    allow_enter = bool(cfg.get("mention_bind_allow_enter", False))
    no_uncertain_cleanup = bool(cfg.get("mention_no_cleanup_on_uncertain", False))

    if popup_wait < 1000:
        popup_wait = 1000  # enforce minimum

    focused = await _focus_any_composer(page)
    audit.log("FOCUS", target="composer", ok=focused, via="bind")
    if not focused:
        return False

    # Snapshot before typing
    before = await _composer_text_and_html(page)

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

    # Select option and click robustly
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

    # Give Teams time to transform text to a pill
    try:
        await page.wait_for_timeout(post_wait)
    except Exception:
        pass

    # Verify 1: pill present
    try:
        pill = page.locator(MENTION_PILL).first
        await pill.wait_for(timeout=pill_verify_ms)
        audit.log("BIND", result="success_pill", delay_ms=char_delay, wait_ms=popup_wait, post_wait=post_wait)
        return True
    except Exception:
        pass

    # Verify 2: editor HTML changed meaningfully from pre-click snapshot
    after = await _composer_text_and_html(page)
    html_changed = bool(after.get("html") and (after["html"] != before.get("html")))
    text_now = (after.get("text") or "").strip()
    # If HTML changed or text no longer equals the raw "@Name", treat as success-ish
    if html_changed or (text_now and text_now.lower() != f"@{bot_name.lower()}"):
        audit.log("BIND", result="success_heuristic", html_changed=html_changed, text_now=text_now)
        return True

    # Optional last-resort: Enter
    if allow_enter:
        try:
            await page.keyboard.press("Enter")
            pill = page.locator(MENTION_PILL).first
            await pill.wait_for(timeout=pill_verify_ms)
            audit.log("BIND", result="success_enter_fallback", delay_ms=char_delay, wait_ms=popup_wait)
            return True
        except Exception:
            pass

    # Still uncertain: either cleanup or leave as-is based on config
    if no_uncertain_cleanup:
        audit.log("BIND", result="uncertain_left_as_is", text=text_now)
        return False
    else:
        await _backspace_cleanup(page, bot_name)
        audit.log("BIND", result="uncertain_cleanup", text=text_now)
        return False