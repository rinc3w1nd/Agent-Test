#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teams @Mention Corpora Runner (Edge on macOS)
- InPrivate + storage_state for SSO persistence (Option B)
- Robust @mention commit (no Enter until final send)
- Clicks Send button only to avoid premature sends
- Saves bot replies + screenshots

Config (config.yaml) keys used:
  channel_url: "https://teams.microsoft.com/v2/..."
  bot_display_name: "Your Bot Name"
  headful: true
  use_inprivate: true
  edge_executable: "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
  storage_state_file: "auth_state.json"
  wait_after_send_sec: 35
  max_retries: 2
  screenshot: true
  persist_profile_dir: ".pw-profile"      # ignored when use_inprivate: true

Usage:
  pip install playwright pyyaml
  playwright install
  python3 runner.py --corpus corpora/example.jsonl [--config config.yaml] [--debug-mention] [--pause-after-login]
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
    # Search main frame, then all child frames.
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
    Fast path: commit @mention via popup click, do NOT verify chip.
    Avoids Enter entirely until final send. Types remainder and returns.
    """
    import re

    # Composer (classic + new Teams)
    def composer_locator(fr):
        return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))
    composer = await find_in_frames(page, composer_locator, timeout=30000)
    if not composer:
        raise RuntimeError("Composer textbox not found.")
    await composer.click()

    # Normalize @BOT → @<name>
    if "@BOT" in full_text:
        full_text = full_text.replace("@BOT", f"@{bot_name}")

    token = f"@{bot_name}"
    i = full_text.find(token)
    if i == -1:
        await page.keyboard.type(full_text, delay=14)
        return

    pre = full_text[:i]
    tail = full_text[i + len(token):].lstrip()

    # Type any leading text before the mention
    if pre.strip():
        await page.keyboard.type(pre + " ", delay=12)

    # Type @ + name slowly to trigger the popup (NO Enter)
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(180)
    for ch in bot_name:
        await page.keyboard.type(ch, delay=100)
    await page.wait_for_timeout(250)

    if debug_mention:
        print("[DEBUG] Paused before commit. Inspect popup, then press Enter here.")
        try: input()
        except Exception: pass

    # Popup option harvesting (supports both classic & New Teams)
    async def get_popup_options():
        opts = []
        for fr in page.frames:
            for loc in (
                fr.get_by_role("option"),
                fr.locator("//div[@data-tid='mention-suggestion__item']"),
                fr.locator("//*[@role='listbox']//*[@role='option']"),
            ):
                try:
                    for h in await loc.element_handles():
                        try:
                            txt = (await h.inner_text()).strip()
                            if txt: opts.append((h, txt))
                        except Exception:
                            pass
                except Exception:
                    pass
        return opts

    async def click_option_center(handle):
        box = await handle.bounding_box()
        if not box:
            return False
        await page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
        return True

    async def commit_mention(name: str):
        opts = await get_popup_options()
        if not opts:
            return False
        name_norm = " ".join(name.split()).lower()
        def pick(key):
            for h, t in opts:
                tt = " ".join(t.split()).lower()
                if key(tt, name_norm): return h
            return None
        handle = (pick(lambda t, n: t == n)
                  or pick(lambda t, n: t.startswith(n))
                  or pick(lambda t, n: n in t))
        if not handle:
            return False
        if await click_option_center(handle):
            return True
        try:
            await handle.scroll_into_view_if_needed()
            await handle.click(force=True)
            return True
        except Exception:
            return False

    # Commit by click (no Enter here), then short settle wait
    _ = await commit_mention(bot_name)
    await page.wait_for_timeout(250)

    # Move caret out of whatever got inserted, space, then type the rest
    await page.keyboard.press("ArrowRight")
    await page.keyboard.type(" ", delay=30)
    if tail:
        await page.keyboard.type(tail, delay=14)

async def _click_send(page):
    # Send via button (safer than Enter due to Teams settings)
    def send_btn(fr):
        return fr.get_by_role("button", name=re.compile("Send", re.I))
    btn = await find_in_frames(page, send_btn, timeout=8000)
    if not btn:
        raise RuntimeError("Send button not found; refusing to press Enter to avoid premature send.")
    await btn.click()

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

async def run(corpus_path: str, config_path: str, debug_mention: bool, pause_after_login: bool):
    cfg = yaml.safe_load(open(config_path, "r", encoding="utf-8"))

    url = cfg["channel_url"]
    bot = cfg["bot_display_name"]
    headful = bool(cfg.get("headful", True))
    wait_after = int(cfg.get("wait_after_send_sec", 35))
    retries = int(cfg.get("max_retries", 2))
    screenshot = bool(cfg.get("screenshot", True))
    use_inpr = bool(cfg.get("use_inprivate", False))
    edge_exe = cfg.get("edge_executable")
    state_fp = cfg.get("storage_state_file")
    profile = cfg.get("persist_profile_dir", ".pw-profile")

    out_dir = pathlib.Path("runs"); out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (pathlib.Path(corpus_path).stem + ".out.jsonl")

    async with async_playwright() as pw:
        # Launch Edge (preferred) with InPrivate + storage state, or fallback to persistent profile
        if use_inpr:
            args = ["--inprivate"]
            if edge_exe and os.path.exists(edge_exe):
                browser = await pw.chromium.launch(executable_path=edge_exe, headless=not headful, args=args)
            else:
                browser = await pw.chromium.launch(headless=not headful, args=args)
            context_kwargs = {}
            if state_fp and os.path.exists(state_fp):
                context_kwargs["storage_state"] = state_fp
            ctx = await browser.new_context(**context_kwargs)
        else:
            # Persistent profile mode
            if edge_exe and os.path.exists(edge_exe):
                ctx = await pw.chromium.launch_persistent_context(profile, executable_path=edge_exe, headless=not headful)
            else:
                ctx = await pw.chromium.launch_persistent_context(profile, headless=not headful)

        page = await ctx.new_page()
        await page.goto(url, wait_until="load")
        await asyncio.sleep(5)  # give Teams/SSO time
        if pause_after_login:
            print("[*] Pause for login. Complete SSO in Edge, then press Enter here.")
            try:
                input()
            except Exception:
                pass

        with open(out_path, "w", encoding="utf-8") as outf:
            count = 0
            for row in load_jsonl(corpus_path):
                rid = row.get("id") or f"case-{count+1:04d}"
                payload = row["payload"]
                # Ensure @BOT is replaced (type_mention also handles it, but do it here too)
                payload = payload.replace("@BOT", f"@{bot}")

                attempt = 0
                reply = None
                while attempt <= retries:
                    attempt += 1
                    try:
                        print(f"[>] {rid} attempt {attempt}")
                        await type_mention_and_payload(page, bot, payload, debug_mention=debug_mention)
                        await _click_send(page)
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

        # Save cookies/localStorage for future runs (works with InPrivate too)
        try:
            if state_fp:
                await ctx.storage_state(path=state_fp)
                try:
                    os.chmod(state_fp, 0o600)
                except Exception:
                    pass
        except Exception:
            pass

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
    ap.add_argument("--pause-after-login", action="store_true", help="Pause after page load to complete SSO manually")
    args = ap.parse_args()
    asyncio.run(run(args.corpus, args.config, args.debug_mention, args.pause_after_login))