from typing import Dict
from .tarr_selectors import COMPOSER_CANDIDATES, MENTION_POPUP, MENTION_OPTION, MENTION_PILL

# Defaults (overridable via YAML)
POPUP_WAIT_MS_DEFAULT   = 1200   # >= 1000ms
TYPE_DELAY_MS_DEFAULT   = 100    # 100ms/char
POST_CLICK_WAIT_MS_DEF  = 1500   # give Teams time to "pillify"
PILL_VERIFY_MS_DEF      = 1200

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

async def cleanup_if_allowed(page, bot_name: str, allow_cleanup: bool, audit, reason: str):
    if allow_cleanup:
        await _backspace_cleanup(page, bot_name)
        audit.log("BIND_CLEANUP", reason=reason, cleaned=True)
    else:
        audit.log("BIND_CLEANUP", reason=reason, cleaned=False)

async def _pick_target_option(page, bot_name: str):
    """
    Return a locator for the best matching option in the mention popup, or None.
    Prefers exact (case-insensitive), else contains.
    """
    # Try JS to choose exact/contains, then tag it so we can reselect with a locator.
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
    return page.locator(MENTION_OPTION).first  # fallback

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
            await target.element_handle()
        )
        return True
    except Exception:
        return False

async def _composer_text_and_html(page):
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
    All failure paths respect `mention_no_cleanup_on_uncertain`:
      - true  => never backspace cleanup on failure
      - false => cleanup typed '@<name>' on failure
    Optional YAML:
      mention_type_char_delay_ms_fast: 100
      mention_popup_wait_ms: 1200
      mention_post_click_wait_ms: 1500
      mention_pill_verify_ms: 1200
      mention_bind_allow_enter: false
      mention_no_cleanup_on_uncertain: true
    """
    char_delay   = int(cfg.get("mention_type_char_delay_ms_fast", TYPE_DELAY_MS_DEFAULT))
    popup_wait   = int(cfg.get("mention_popup_wait_ms", POPUP_WAIT_MS_DEFAULT))
    post_wait    = int(cfg.get("mention_post_click_wait_ms", POST_CLICK_WAIT_MS_DEF))
    pill_verify  = int(cfg.get("mention_pill_verify_ms", PILL_VERIFY_MS_DEF))
    allow_enter  = bool(cfg.get("mention_bind_allow_enter", False))
    # Treat this as "no cleanup on ANY failure," not just "uncertain"
    no_cleanup   = bool(cfg.get("mention_no_cleanup_on_uncertain", False))

    if popup_wait < 1000:
        popup_wait = 1000

    focused = await _focus_any_composer(page)
    audit.log("FOCUS", target="composer", ok=focused, via="bind")
    if not focused:
        return False

    before = await _composer_text_and_html(page)

    # Type "@name" at requested cadence
    try:
        await page.keyboard.type("@", delay=char_delay)
        await page.keyboard.type(bot_name, delay=char_delay)
    except Exception:
        await cleanup_if_allowed(page, bot_name, not no_cleanup, audit, "type_exception")
        return False

    # Wait for popup
    popup = page.locator(MENTION_POPUP).first
    try:
        await popup.wait_for(timeout=popup_wait)
    except Exception:
        await cleanup_if_allowed(page, bot_name, not no_cleanup, audit, "no_popup")
        audit.log("BIND", result="no_popup", delay_ms=char_delay, wait_ms=popup_wait)
        return False

    # Pick option & click robustly
    target = await _pick_target_option(page, bot_name)
    if not target:
        await cleanup_if_allowed(page, bot_name, not no_cleanup, audit, "no_option")
        audit.log("BIND", result="no_option", delay_ms=char_delay, wait_ms=popup_wait)
        return False

    ok = await _click_robust(page, target)
    if not ok:
        await cleanup_if_allowed(page, bot_name, not no_cleanup, audit, "click_fail")
        audit.log("BIND", result="click_fail", delay_ms=char_delay, wait_ms=popup_wait)
        return False

    # Allow time for pillification
    try:
        await page.wait_for_timeout(post_wait)
    except Exception:
        pass

    # Verify pill
    try:
        pill = page.locator(MENTION_PILL).first
        await pill.wait_for(timeout=pill_verify)
        audit.log("BIND", result="success_pill", delay_ms=char_delay, wait_ms=popup_wait, post_wait=post_wait)
        return True
    except Exception:
        pass

    # Heuristic verify (HTML/text changed)
    after = await _composer_text_and_html(page)
    html_changed = bool(after.get("html") and (after["html"] != before.get("html")))
    text_now = (after.get("text") or "").strip()

    if html_changed or (text_now and text_now.lower() != f"@{bot_name.lower()}"):
        audit.log("BIND", result="success_heuristic", html_changed=html_changed, text_now=text_now)
        return True

    # Optional Enter fallback
    if allow_enter:
        try:
            await page.keyboard.press("Enter")
            pill = page.locator(MENTION_PILL).first
            await pill.wait_for(timeout=pill_verify)
            audit.log("BIND", result="success_enter_fallback", delay_ms=char_delay, wait_ms=popup_wait)
            return True
        except Exception:
            pass

    # Final failure: respect no_cleanup flag
    await cleanup_if_allowed(page, bot_name, not no_cleanup, audit, "final_uncertain")
    audit.log("BIND", result="uncertain", html_changed=html_changed, text_now=text_now)
    return False