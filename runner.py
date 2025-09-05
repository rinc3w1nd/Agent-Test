#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teams @Mention Corpora Runner (macOS Edge, InPrivate optional)
- Sends corpus lines into a Teams channel, @mentions a bot, captures replies.
- Robust mention commit: listbox options + bounding-box click + fallbacks.
- Debug flag to pause before mention commit so you can inspect the popup.

Requirements:
  pip install playwright pyyaml
  playwright install
"""

import asyncio, json, argparse, time, os, re, pathlib, sys
from typing import Optional
import yaml
from playwright.async_api import async_playwright

def load_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)

async def find_in_frames(page, locator_fn, timeout=15000):
    # Search main frame first, then all frames.
    try:
        el = await locator_fn(page.main_frame).element_handle(timeout=timeout)
        if el:
            return el
    except Exception:
        pass
    for fr in page.frames:
        try:
            el = await locator_fn(fr).element_handle(timeout=3000)
            if el:
                return el
        except Exception:
            continue
    return None

async def type_mention_and_payload(page, bot_name: str, full_text: str, debug_mention: bool = False):
    """
    Types a message into the composer, committing a real @mention chip for the given bot_name.
    `full_text` may contain plain text before the mention and after it.
    """
    # Locate composer (classic + new Teams)
    def composer_locator(fr):
        return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))
    composer = await find_in_frames(page, composer_locator, timeout=30000)
    if not composer:
        raise RuntimeError("Composer textbox not found. Adjust selectors.")
    await composer.click()

    # Split payload around the first @<bot_name> occurrence
    mention_token = f"@{bot_name}"
    idx = full_text.find(mention_token)
    if idx == -1:
        # No mention found; type raw and send
        await page.keyboard.type(full_text, delay=14)
        await _click_send(page)
        return

    pre = full_text[:idx]
    tail = full_text[idx + len(mention_token):].lstrip()

    # Type any leading text before the mention
    if pre.strip():
        await page.keyboard.type(pre, delay=12)
        await page.keyboard.type(" ", delay=12)

    # Helpers for mention handling
    async def mention_chip_present():
        for fr in page.frames:
            try:
                chip = await fr.locator(
                    "//span[contains(@class,'mention') or @data-mention or @data-mentions='true' or @data-tid='mention-chip']"
                ).element_handle(timeout=400)
                if chip:
                    return True
            except Exception:
                continue
        return False

    async def get_popup_options():
        """Return list of (handle, text) for mention options across frames."""
        opts = []
        for fr in page.frames:
            locs = [
                fr.get_by_role("option"),
                fr.locator("//div[@data-tid='mention-suggestion__item']"),
                fr.locator("//*[@role='listbox']//*[@role='option']"),
            ]
            for loc in locs:
                try:
                    handles = await loc.element_handles()
                    for h in handles:
                        try:
                            txt = (await h.inner_text()).strip()
                            if txt:
                                opts.append((h, txt))
                        except Exception:
                            continue
                except Exception:
                    continue
        return opts

    async def click_option_center(handle):
        box = await handle.bounding_box()
        if not box:
            return False
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        await page.mouse.move(x, y)
        await page.mouse.click(x, y)
        return True

    async def commit_mention_from_popup(name: str):
        opts = await get_popup_options()
        if not opts:
            return False
        name_norm = " ".join(name.split()).lower()

        def pick(key):
            for h, t in opts:
                tt = " ".join(t.split()).lower()
                if key(tt, name_norm):
                    return h, t
            return None

        candidate = (pick(lambda t, n: t == n) or
                     pick(lambda t, n: t.startswith(n)) or
                     pick(lambda t, n: n in t))
        if not candidate:
            return False
        handle, _txt = candidate
        ok = await click_option_center(handle)
        if not ok:
            try:
                await handle.scroll_into_view_if_needed()
                await handle.click(force=True)
                ok = True
            except Exception:
                ok = False
        return ok

    # Type @ + bot name slowly to trigger the popup
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(200)
    for ch in bot_name:
        await page.keyboard.type(ch, delay=110)
    await page.wait_for_timeout(350)

    if debug_mention:
        print("[DEBUG] Paused before committing mention. Inspect the popup, then press Enter here.")
        try:
            input()
        except Exception:
            pass

    # Try to commit via popup
    committed = await commit_mention_from_popup(bot_name)
    await page.wait_for_timeout(250)

    # If no chip, nudge to refresh suggestions and retry
    if not await mention_chip_present():
        await page.keyboard.type(" ", delay=60)
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(220)
        committed = await commit_mention_from_popup(bot_name)
        await page.wait_for_timeout(250)

    # Keyboard fallbacks (accept top suggestion)
    if not await mention_chip_present():
        try:
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
        except Exception:
            pass
        await page.wait_for_timeout(250)

    # Final verification
    if not await mention_chip_present():
        raise RuntimeError("Mention did not resolve to a chip. Check bot_display_name or adjust selectors.")

    # Move caret out of the chip + add a space
    await page.keyboard.press("ArrowRight")
    await page.keyboard.type(" ", delay=40)

    # Type the remainder of the message
    if tail:
        await page.keyboard.type(tail, delay=14)

    # Send
    await _click_send(page)

async def _click_send(page):
    # Send via button if found (safer than Enter because of Teams settings)
    def send_btn(fr):
        return fr.get_by_role("button", name=re.compile("Send", re.I))
    btn = await find_in_frames(page, send_btn, timeout=8000)
    if btn:
        await btn.click()
    else:
        await page.keyboard.press("Enter")

async def wait_for_bot_reply(page, bot_name: str, timeout_sec: int = 35) -> Optional[str]:
    deadline = time.time() + timeout_sec

    async def get_latest(fr):
        items = await fr.get_by_role("listitem").all()
        for it in reversed(items):
            try:
                txt = (await it.inner_text()).strip()
                if not txt:
                    continue
                # Heuristic: message block that includes bot name
                if bot_name.lower() in txt.lower():
                    return txt
            except Exception:
                continue
        return None

    while time.time() < deadline:
        for fr in page.frames:
            try:
                txt = await get_latest(fr)
                if txt:
                    return txt
            except Exception:
                continue
        await asyncio.sleep(1.2)
    return None

async def run(corpus_path: str, config_path: str, debug_mention: bool):
    cfg = yaml.safe_load(open(config_path, "r", encoding="utf-8"))

    url = cfg["channel_url"]
    bot = cfg["bot_display_name"]
    headful = bool(cfg.get("headful", True))
    profile = cfg.get("persist_profile_dir", ".pw-profile")
    wait_after = int(cfg.get("wait_after_send_sec", 35))
    retries = int(cfg.get("max_retries", 2))
    screenshot = bool(cfg.get("screenshot", True))
    edge_exe = cfg.get("edge_executable")  # e.g., /Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge
    use_inpr = bool(cfg.get("use_inprivate", False))

    out_dir = pathlib.Path("runs"); out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (pathlib.Path(corpus_path).stem + ".out.jsonl")

    async with async_playwright() as pw:
        # Launch Edge (preferred) or fallback to stock Chromium
        if use_inpr:
            # InPrivate session (fresh each run)
            launch_args = ["--inprivate"]
            if edge_exe and os.path.exists(edge_exe):
                browser = await pw.chromium.launch(executable_path=edge_exe, headless=not headful, args=launch_args)
            else:
                browser = await pw.chromium.launch(headless=not headful, args=launch_args)
            ctx = await browser.new_context()
        else:
            # Persistent profile
            if edge_exe and os.path.exists(edge_exe):
                ctx = await pw.chromium.launch_persistent_context(profile, executable_path=edge_exe, headless=not headful)
            else:
                ctx = await pw.chromium.launch_persistent_context(profile, headless=not headful)

        page = await ctx.new_page()
        await page.goto(url, wait_until="load")
        await asyncio.sleep(5)  # give Teams/SSO a beat
        print("[*] If login/SSO is required, complete it in the window, then press Enter here.")
        try:
            input()
        except Exception:
            pass

        with open(out_path, "w", encoding="utf-8") as outf:
            count = 0
            for row in load_jsonl(corpus_path):
                rid = row.get("id") or f"case-{count+1:04d}"
                payload = row["payload"]

                # Replace @BOT with @<bot> if present; else leave payload as-is
                payload = payload.replace("@BOT", f"@{bot}")

                attempt = 0
                reply = None
                while attempt <= retries:
                    attempt += 1
                    try:
                        print(f"[>] {rid} attempt {attempt}")
                        await type_mention_and_payload(page, bot, payload, debug_mention=debug_mention)
                        reply = await wait_for_bot_reply(page, bot, timeout_sec=wait_after)
                        break
                    except Exception as e:
                        print(f"[!] Error sending {rid}: {e}")
                        await asyncio.sleep(3)

                snap = ""
                if screenshot:
                    snap = str(out_dir / f"{rid}.png")
                    try:
                        await page.screenshot(path=snap, full_page=True)
                    except Exception:
                        snap = ""

                outf.write(json.dumps({
                    **row,
                    "sent_payload": payload,
                    "bot_response": reply or "",
                    "screenshot": snap
                }, ensure_ascii=False) + "\n")
                outf.flush()
                count += 1
                await asyncio.sleep(3)

        await page.close()
        if use_inpr:
            await ctx.close()
            await browser.close()
        else:
            await ctx.close()
    print(f"[+] Done. Results: {out_path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="Path to JSONL corpus file")
    ap.add_argument("--config", default="config.yaml", help="Path to YAML config")
    ap.add_argument("--debug-mention", action="store_true", help="Pause before committing the @mention to inspect the popup")
    args = ap.parse_args()
    asyncio.run(run(args.corpus, args.config, args.debug_mention))