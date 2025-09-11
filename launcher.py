from typing import Tuple, Optional
from playwright.async_api import async_playwright, Browser, BrowserContext

async def open_context(channel: str, headless: bool, storage_state: Optional[str] = None) -> Tuple[Browser, BrowserContext]:
    """
    Non-persistent launch (per NFR R1) with optional storage_state.
    Returns (browser, context). Caller owns closing both.
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel=channel, headless=headless)
    context = await browser.new_context(storage_state=(storage_state or None))
    # Stash playwright handle on browser for teardown convenience
    setattr(browser, "_pw", pw)
    return browser, context