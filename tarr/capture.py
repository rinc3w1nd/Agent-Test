from typing import Optional
from pathlib import Path

def _strip_zwsp(s: str) -> str:
    if not s:
        return s
    return s.replace("\u200b", "").replace("\u2060", "").replace("\ufeff", "")

async def poll_latest_reply(page, bot_name: str, timeout_ms: int) -> Optional[dict]:
    """
    Operator-triggered, bounded scan for the latest reply by `bot_name`.
    Loads observer.js once per page. Returns {'text','html'} or None.
    NFRs: U3 (operator-driven), FMR3 (no timer-based auto capture).
    """
    # Ensure our observer is available
    has_func = await page.evaluate("typeof window.__TARR_OBSERVER === 'function'")
    if not has_func:
        js = Path(__file__).with_name("observer.js").read_text(encoding="utf-8")
        await page.add_script_tag(content=f"window.__TARR_OBSERVER = {js}")

    # One-shot evaluation
    data = await page.evaluate("window.__TARR_OBSERVER", (bot_name or "").lower())
    if not data:
        return None

    data["text"] = _strip_zwsp(data.get("text", "")).strip()
    return data