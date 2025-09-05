
import asyncio, json, argparse, time, os, re, pathlib
from typing import Optional
import yaml
from playwright.async_api import async_playwright

def load_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line: continue
            yield json.loads(line)

async def find_in_frames(page, locator_fn, timeout=15000):
    try:
        el = await locator_fn(page.main_frame).element_handle(timeout=timeout)
        if el: return el
    except Exception:
        pass
    for fr in page.frames:
        try:
            el = await locator_fn(fr).element_handle(timeout=3000)
            if el: return el
        except Exception:
            continue
    return None

async def type_mention_and_payload(page, bot_name: str, payload: str):
    import re
    # 1) Find & focus the composer (cover both classic/new Teams)
    def composer_locator(fr):
        return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))
    composer = await find_in_frames(page, composer_locator, timeout=30000)
    if not composer:
        raise RuntimeError("Composer textbox not found. Adjust selectors.")
    await composer.click()

    # Helpers
    async def mention_chip_present():
        # Look for the inserted chip in the editor
        for fr in page.frames:
            try:
                chip = await fr.locator(
                    "//span[contains(@class,'mention') or @data-mention or @data-mentions='true']"
                ).element_handle(timeout=500)
                if chip:
                    return True
            except Exception:
                continue
        return False

    async def commit_from_popup():
        # Prefer exact name in the listbox
        for fr in page.frames:
            try:
                listbox = fr.get_by_role("listbox")
                opt = listbox.get_by_role("option", name=re.compile(rf"^{re.escape(bot_name)}$", re.I))
                if await opt.count():
                    await opt.first().scroll_into_view_if_needed()
                    await opt.first().click(force=True)
                    return True
            except Exception:
                pass
        # Fallback: any option containing the name
        for fr in page.frames:
            try:
                listbox = fr.get_by_role("listbox")
                opt = listbox.get_by_role("option", name=re.compile(re.escape(bot_name), re.I))
                if await opt.count():
                    await opt.first().scroll_into_view_if_needed()
                    await opt.first().click(force=True)
                    return True
            except Exception:
                pass
        # Keyboard fallbacks
        try:
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            return True
        except Exception:
            pass
        try:
            await page.keyboard.press("Tab")
            return True
        except Exception:
            pass
        return False

    if "@BOT" in payload:
        # 2) Type @ + bot name slowly to trigger suggestions
        await page.keyboard.type("@", delay=90)
        await page.wait_for_timeout(200)
        for ch in bot_name:
            await page.keyboard.type(ch, delay=120)
        await page.wait_for_timeout(300)

        # 3) Commit the mention and verify the chip landed
        committed = await commit_from_popup()
        await page.wait_for_timeout(250)

        if not await mention_chip_present():
            # Nudge: reopen/refresh suggestions then retry commit
            await page.keyboard.type(" ", delay=60)
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(200)
            committed = await commit_from_popup()
            await page.wait_for_timeout(250)

        # Some tenants still require Enter to finalize the chip even after click
        if not await mention_chip_present():
            try:
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(200)
            except Exception:
                pass

        # 4) If still not a chip, fail fast so you can see the page state in the screenshot
        if not await mention_chip_present():
            raise RuntimeError("Teams mention did not resolve to a chip. Check selectors and bot_display_name.")

        # 5) Move caret out of the chip and add a space
        await page.keyboard.press("ArrowRight")
        await page.keyboard.type(" ", delay=40)

        # 6) Type the remainder (after @BOT)
        tail = payload.replace("@BOT", "").lstrip()
        if tail:
            await page.keyboard.type(tail, delay=16)
    else:
        await page.keyboard.type(payload, delay=16)

    # 7) Send (button safer than Enter)
    def send_btn(fr):
        return fr.get_by_role("button", name=re.compile("Send", re.I))
    btn = await find_in_frames(page, send_btn, timeout=8000)
    if btn:
        await btn.click()
    else:
        await page.keyboard.press("Enter")

async def wait_for_bot_reply(page, bot_name: str, timeout_sec: int=35) -> Optional[str]:
    deadline = time.time() + timeout_sec

    async def get_latest(fr):
        items = await fr.get_by_role("listitem").all()
        for it in reversed(items):
            try:
                txt = (await it.inner_text()).strip()
                if not txt: continue
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

async def run(corpus_path: str, config_path: str):
    cfg = yaml.safe_load(open(config_path, "r", encoding="utf-8"))
    url = cfg["channel_url"]
    bot = cfg["bot_display_name"]
    headful = bool(cfg.get("headful", True))
    profile = cfg.get("persist_profile_dir", ".pw-profile")
    wait_after = int(cfg.get("wait_after_send_sec", 35))
    retries = int(cfg.get("max_retries", 2))
    screenshot = bool(cfg.get("screenshot", True))
    edge_exe = cfg.get("edge_executable")
    use_inpr = bool(cfg.get("use_inprivate", False))

    out_dir = pathlib.Path("runs"); out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (pathlib.Path(corpus_path).stem + ".out.jsonl")

    async with async_playwright() as pw:
        if use_inpr:
            # InPrivate / ephemeral session
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
        # Give SSO some breathing room
        await asyncio.sleep(5)
        print("[*] If login/SSO is required, complete it in the Edge window, then press Enter here.")
        try:
            input()
        except Exception:
            pass

        with open(out_path, "w", encoding="utf-8") as outf:
            count = 0
            for row in load_jsonl(corpus_path):
                rid = row.get("id") or f"case-{count+1:04d}"
                payload = row["payload"].replace("@BOT", f"@{bot}")
                attempt = 0; reply = None
                while attempt <= retries:
                    attempt += 1
                    try:
                        print(f"[>] {rid} attempt {attempt}")
                        await type_mention_and_payload(page, bot, payload)
                        reply = await wait_for_bot_reply(page, bot, timeout_sec=wait_after)
                        break
                    except Exception as e:
                        print(f"[!] Error: {e}")
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
        # Close context or browser depending on mode
        if use_inpr:
            await ctx.close()
            await browser.close()
        else:
            await ctx.close()
    print(f"[+] Done. Results: {out_path}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    asyncio.run(run(args.corpus, args.config))
