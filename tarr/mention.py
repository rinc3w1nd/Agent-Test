from typing import Dict
from .selectors import COMPOSER, MENTION_POPUP

async def bind(page, bot_name: str, cfg: Dict, audit) -> bool:
    """
    Deterministic @-binder with 3 windows of 5s/5s/5s by default (NFR P2).
    - Clicks composer
    - Types '@' + bot_name with per-char delay
    - Waits for popup and tries ArrowDown+Enter selection
    - Between attempts, backspaces the typed name and retries
    - Emits audit lines for each attempt + final outcome
    """
    delay_before = int(cfg.get("mention_delay_before_at_ms", 15000))
    char_delay   = int(cfg.get("mention_type_char_delay_ms", 35))
    popup_wait   = int(cfg.get("mention_popup_wait_ms", 5000))
    retype_wait  = int(cfg.get("mention_retype_wait_ms", 500))
    backoff      = bool(cfg.get("mention_retype_backoff", True))
    windows      = cfg.get("mention_attempt_windows_ms", [5000, 5000, 5000])

    comp = page.locator(COMPOSER).first
    await comp.click(timeout=int(cfg.get("dom_ready_timeout_ms", 120000)))

    # Initial guard wait before first '@'
    if delay_before > 0:
        await page.wait_for_timeout(delay_before)

    for attempt, pre_wait in enumerate(windows, 1):
        await page.wait_for_timeout(pre_wait)

        # Type '@' + name
        try:
            await comp.type("@", delay=char_delay)
            await comp.type(bot_name, delay=char_delay)
        except Exception:
            pass

        # Try to wait for popup and select first option
        ok = False
        try:
            await page.locator(MENTION_POPUP).first.wait_for(timeout=popup_wait)
        except Exception:
            # popup may appear briefly; continue to attempt selection anyway
            pass

        try:
            await comp.press("ArrowDown")
            await comp.press("Enter")
            ok = True
        except Exception:
            ok = False

        if ok:
            audit.log("BIND", attempt=attempt, window_ms=pre_wait, result="success")
            return True

        # Not bound: clean the typed name and retry
        audit.log("BIND", attempt=attempt, window_ms=pre_wait, result="retry")
        try:
            for _ in range(len(bot_name)):
                await comp.press("Backspace")
        except Exception:
            pass

        wait_next = retype_wait * (attempt if backoff else 1)
        if wait_next > 0:
            await page.wait_for_timeout(wait_next)

    audit.log("BIND", result="fail")
    return False