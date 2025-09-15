# tarr/launcher.py
from typing import Tuple, Optional
from playwright.async_api import async_playwright  # type: ignore

async def open_context(
    channel: str = "msedge",
    headless: bool = False,
    storage_state: Optional[str] = None,
) -> Tuple[object, object]:
    """
    Launch a non-persistent Edge (Chromium) browser and open a context.

    Args:
        channel: Playwright channel to use (e.g., "msedge", "chrome", "chromium").
                 For Edge, ensure you've run: `python -m playwright install msedge`
        headless: Run without a visible window if True.
        storage_state: Path to a Playwright storage state JSON file (auth cookies, etc.),
                       or None to start with a blank state.

    Returns:
        (browser, context): Playwright Browser and BrowserContext objects.
                            The Browser has a private attribute `_pw` to stop Playwright later.
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel=channel, headless=headless)
    context = await browser.new_context(storage_state=storage_state if storage_state else None)

    # Stash the playwright driver on the browser for clean shutdown by callers:
    # await context.close(); await browser.close(); await browser._pw.stop()
    setattr(browser, "_pw", pw)
    return browser, context