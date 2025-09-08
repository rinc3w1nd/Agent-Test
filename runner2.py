#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio, json, os, sys, time, argparse, pathlib
from typing import Callable, Optional, Tuple, List, Any

import yaml
from playwright.async_api import async_playwright, Page, BrowserContext, ElementHandle

# -----------------------------
# Config helpers
# -----------------------------

def load_yaml(fp: str) -> dict:
    with open(fp, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def read_jsonl(fp: str) -> List[dict]:
    out = []
    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                out.append({"payload": line})
    return out

# -----------------------------
# Teams formatting defang (weird tags tweak)
# -----------------------------

def _sanitize_payload_for_teams(text: str, mode: str = "off") -> str:
    if not text or mode == "off":
        return text
    if mode == "zwsp":
        return text.replace("```", "`\u200b``")
    if mode == "space":
        return text.replace("```", "` ``")
    return text

# -----------------------------
# Typing helpers
# -----------------------------

async def _type_with_safe_newlines(page: Page, text: str, delay: int = 14):
    for ch in text:
        if ch == "\n":
            await page.keyboard.down("Shift")
            await page.keyboard.press("Enter")
            await page.keyboard.up("Shift")
        else:
            await page.keyboard.type(ch, delay=delay)

async def find_in_frames(page: Page, maker: Callable, timeout: int = 10000):
    deadline = time.time() + (timeout / 1000)
    last_err = None
    while time.time() < deadline:
        for fr in page.frames:
            try:
                loc = maker(fr)
                try:
                    handle = await loc.element_handle(timeout=200)
                    if handle:
                        return handle
                except Exception:
                    pass
            except Exception as e:
                last_err = e
        await page.wait_for_timeout(120)
    if last_err:
        print(f"[warn] find_in_frames timeout: {last_err}")
    return None

# -----------------------------
# Mention + payload
# -----------------------------

async def type_mention_and_payload(page: Page, bot_name: str, full_text: str, cfg: dict):
    name_delay = int(cfg.get("mention_name_char_delay_ms", 40))
    popup_wait_ms = int(cfg.get("mention_popup_wait_ms", 2500))
    popup_poll_ms = int(cfg.get("mention_popup_poll_ms", 150))
    defang_mode = str(cfg.get("defang_fences", "off")).lower()

    def composer_locator(fr):
        return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))
    composer = await find_in_frames(page, composer_locator, timeout=30000)
    if not composer:
        raise RuntimeError("Composer textbox not found.")
    await composer.click()

    if "@BOT" in full_text:
        full_text = full_text.replace("@BOT", f"@{bot_name}")

    full_text = _sanitize_payload_for_teams(full_text, defang_mode)

    token = f"@{bot_name}"
    i = full_text.find(token)
    if i == -1:
        await _type_with_safe_newlines(page, full_text, delay=14)
        return

    pre = full_text[:i]
    tail = full_text[i + len(token):].lstrip()

    if pre.strip():
        await _type_with_safe_newlines(page, pre + " ", delay=12)

    await page.keyboard.type("@", delay=60)
    await page.wait_for_timeout(120)
    for ch in bot_name:
        await page.keyboard.type(ch, delay=name_delay)
    await page.wait_for_timeout(180)

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
                            if txt:
                                opts.append((h, txt))
                        except Exception:
                            pass
                except Exception:
                    pass
        return opts

    async def click_option_center(handle: ElementHandle):
        box = await handle.bounding_box()
        if not box:
            return False
        await page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
        return True

    elapsed = 0
    picked = False
    while elapsed < popup_wait_ms and not picked:
        opts = await get_popup_options()
        if opts:
            name_norm = " ".join(bot_name.split()).lower()
            def pick(key):
                for h, t in opts:
                    tt = " ".join(t.split()).lower()
                    if key(tt, name_norm): return h
                return None
            handle = (pick(lambda t,n: t == n)
                      or pick(lambda t,n: t.startswith(n))
                      or pick(lambda t,n: n in t))
            if handle:
                picked = await click_option_center(handle)
                if not picked:
                    try:
                        await handle.scroll_into_view_if_needed()
                        await handle.click(force=True)
                        picked = True
                    except Exception:
                        picked = False
                if picked:
                    break
        await page.wait_for_timeout(popup_poll_ms)
        elapsed += popup_poll_ms

    await page.wait_for_timeout(250)

    await page.keyboard.press("ArrowRight")
    await page.keyboard.type(" ", delay=25)

    if tail:
        tail = _sanitize_payload_for_teams(tail, defang_mode)
        await _type_with_safe_newlines(page, tail, delay=14)

# -----------------------------
# Send message
# -----------------------------

async def click_send(page: Page):
    import re
    def send_btn(fr):
        return fr.get_by_role("button", name=re.compile("Send", re.I))
    btn = await find_in_frames(page, send_btn, timeout=8000)
    if not btn:
        raise RuntimeError("Send button not found; refusing to press Enter fallback.")
    await btn.click()

# -----------------------------
# URL normalizer (forces web)
# -----------------------------

def normalize_teams_url(url: str) -> str:
    try:
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        u = urlparse(url)
        qs = dict(parse_qsl(u.query, keep_blank_values=True))
        qs["clientType"] = "web"
        qs["web"] = "1"
        new_query = urlencode(qs, doseq=True)
        return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}clientType=web&web=1"

# -----------------------------
# Scrape bot/agent messages
# -----------------------------

async def scrape_latest_bot_messages(page: Page, bot_name: str, max_msgs: int = 1, timeout_ms: int = 8000) -> List[str]:
    try:
        await page.wait_for_timeout(200)
    except Exception:
        pass
    body_css = (
        "li:has([data-tid='messageBody']), "
        "div[data-tid='messageBody'], "
        "div[data-tid='messageBodyContent'], "
        "div.ui-chat__messagecontent, "
        "div.message-body, "
        "div[class*='messageBody']"
    )
    messages = []
    for fr in page.frames:
        try:
            nodes = await fr.locator(body_css).all()
        except Exception:
            nodes = []
        messages.extend(nodes)

    out = []
    bot_l = (bot_name or "").strip().lower()
    for node in reversed(messages):
        try:
            container = await node.locator("xpath=ancestor::li[1] | xpath=ancestor::div[1]").element_handle()
            if not container:
                continue
            author_candidates = []
            for sel in [
                "[data-tid='messageAuthor']",
                "[class*='author']",
                "header[role='heading']",
                "[aria-label*='said']",
                "[aria-label*='message from']",
                "[role='text']",
            ]:
                loc = await container.query_selector(sel)
                if loc:
                    t = (await loc.inner_text()).strip()
                    if t:
                        author_candidates.append(t)
            a_lab = await container.get_attribute("aria-label")
            if a_lab:
                author_candidates.append(a_lab)
            author_join = " | ".join(author_candidates).lower()
            if bot_l and bot_l not in author_join:
                continue
            txt = (await node.inner_text()).strip()
            if txt:
                out.append(txt)
                if len(out) >= max_msgs:
                    break
        except Exception:
            continue
    return out

# -----------------------------
# Browser / context
# -----------------------------

async def make_context(pw, cfg: dict) -> Tuple[BrowserContext, Page]:
    channel = "msedge"
    launch_args = ["--inprivate"]
    if cfg.get("extra_args"):
        launch_args += list(cfg["extra_args"])
    browser = await pw.chromium.launch(channel=channel, headless=False, args=launch_args)
    storage_state_file = cfg.get("storage_state_file")
    if storage_state_file and os.path.exists(storage_state_file):
        context = await browser.new_context(storage_state=storage_state_file)
    else:
        context = await browser.new_context()
    page = await context.new_page()
    return context, page

async def save_storage_state(context: BrowserContext, cfg: dict):
    fp = cfg.get("storage_state_file")
    if not fp:
        return
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    await context.storage_state(path=fp)

# -----------------------------
# Main flow
# -----------------------------

async def run(args):
    cfg = load_yaml(args.config)
    corpus = read_jsonl(args.corpus)
    if not corpus:
        print("Corpus appears empty.")
        return
    teams_url = cfg.get("teams_channel_url") or "https://teams.microsoft.com/"
    bot_name = cfg.get("bot_name") or args.bot_name
    if not bot_name:
        print("ERROR: bot_name not provided (config.yaml bot_name or --bot-name).")
        sys.exit(2)

    async with async_playwright() as pw:
        context, page = await make_context(pw, cfg)
        teams_url = normalize_teams_url(teams_url)
        print(f"[*] Navigating to {teams_url}")
        await page.goto(teams_url, wait_until="load")

        def composer_locator(fr):
            return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))
        composer = await find_in_frames(page, composer_locator, timeout=30000)
        if not composer:
            print("[!] Composer not found. Make sure the channel is open.")
            await context.close()
            return

        runs_dir = pathlib.Path("runs")
        runs_dir.mkdir(exist_ok=True)
        out_fp = runs_dir / (pathlib.Path(args.corpus).stem + ".out.jsonl")
        with open(out_fp, "a", encoding="utf-8") as outf:
            for item in corpus:
                case_id = item.get("id") or ""
                payload = item.get("payload") or ""
                goal = item.get("goal") or ""
                target = item.get("target") or ""

                try:
                    await type_mention_and_payload(page, bot_name, payload, cfg)
                except Exception as e:
                    print(f"[warn] type_mention failed for {case_id}: {e}")

                try:
                    await click_send(page)
                except Exception as e:
                    print(f"[warn] send failed for {case_id}: {e}")

                post_wait_ms = int(cfg.get("post_send_wait_ms", 1200))
                await page.wait_for_timeout(post_wait_ms)

                # Scrape bot response(s)
                bot_msgs = await scrape_latest_bot_messages(page, bot_name, max_msgs=1)
                rec = {
                    "id": case_id,
                    "goal": goal,
                    "target": target,
                    "payload": payload,
                    "sent_payload": payload.replace("@BOT", f"@{bot_name}"),
                    "bot_response": (bot_msgs[0] if bot_msgs else ""),
                    "screenshot": ""
                }
                outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                outf.flush()
                print(f"[+] Sent {case_id or '[no-id]'}")

        await save_storage_state(context, cfg)
        await context.close()
        print(f"[*] Saved output to {out_fp}")

# -----------------------------
# CLI
# -----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml", help="Path to YAML config")
    ap.add_argument("--corpus", required=True, help="Path to JSONL corpus")
    ap.add_argument("--bot-name", help="Overrides bot_name from config")
    args = ap.parse_args()
    asyncio.run(run(args))

if __name__ == "__main__":
    main()