
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
teams_agent_recon.py
A robust Playwright-based runner for probing Teams-based agents with a JSONL corpus.
Features:
- Real @mention binding for channel posts (translate leading @BOT to a real @<bot_name> mention)
- Channel vs thread-aware reply capture
- Author-based detection (no reliance on "AI flair" badges)
- Adaptive selectors for author/body
- Adaptive card text scraping
- Structured JSONL results with timing
- **Per-item artifacts**: text, HTML (optional), and screenshots saved locally for each probe
"""

import asyncio, json, time, argparse, pathlib, sys, os, re
from typing import Dict, Any, List, Optional
import yaml
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, TimeoutError as PWTimeout

DEBUG = False

def dbg(*a):
    try:
        if DEBUG:
            import datetime
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[DEBUG {ts}]", *a, flush=True)
    except Exception:
        pass

DEBUG = False

def dbg(*a):
    try:
        if DEBUG:
            import datetime
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[DEBUG {ts}]", *a, flush=True)
    except Exception:
        pass

SEL = {
    "use_web_app": "text=Use the web app instead",
    "continue_web": "text=Continue on web",
    "channel_list": '[data-tid="channelMessageList"], [data-tid="threadList"]',
    "message_group": '[role="group"], [data-tid="messageCard"], [data-tid="message"]',
    "author": '[data-tid="messageAuthorName"], [data-tid="authorName"]',
    "body": '[data-tid="messageBody"], [data-tid="messageText"], [data-tid="adaptiveCardRoot"], [data-tid="messageContent"]',
    "card": '[data-tid="adaptiveCardRoot"]',
    "new_post_composer": '[data-tid="newMessageInputComposer"], [data-tid="ck-editor"] textarea, [contenteditable="true"]',
    "send_button": '[data-tid="sendMessageButton"]',
    "reply_button": '[data-tid="replyInThread"], [aria-label="Reply"]',
    "mention_popup": '[data-tid="mentionSuggestList"], [data-tid="mentionSuggestions"]',
}

ZWSP_MAP = {ord(c): None for c in '\u200b\u200c\u200d\ufeff'}

def zwsp_strip(s: str) -> str:
    return (s or "").translate(ZWSP_MAP)

def read_yaml(fp: str) -> dict:
    with open(fp, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def read_jsonl(fp: str) -> List[Dict[str, Any]]:
    rows = []
    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows

async def ensure_web(page: Page):
    for key in ("use_web_app", "continue_web"):
        try:
            await page.locator(SEL[key]).first.click(timeout=2500)
        except Exception:
            pass

async def scroll_bottom(page: Page, container_sel: str):
    # Nudge virtualization to render bottom nodes
    for _ in range(5):
        last = page.locator(container_sel).last
        try:
            await last.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            break
        await page.wait_for_timeout(200)

async def bind_mention(page: Page, composer, bot_name: str, char_delay: int, wait_ms: int = 15000) -> bool:
    # Wait for Teams to fully wire the mention subsystem
    await page.wait_for_timeout(wait_ms)
    # Type @ and the bot name
    await composer.type("@", delay=char_delay)
    await composer.type(bot_name, delay=char_delay)
    # Try to bind by selecting from the popup
    try:
        await page.locator(SEL["mention_popup"]).wait_for(timeout=4000)
        await composer.press("Enter")
        return True
    except Exception:
        # Binding failed; delete the bot name we just typed (leave the @)
        try:
            for _ in range(len(bot_name)):
                await composer.press("Backspace")
        except Exception:
            pass
        return False

async def send_payload(page: Page, cfg: dict, payload: str, bot_name: str):
    char_delay = int(cfg.get("mention_name_char_delay_ms", 35))
    comp = page.locator(SEL["new_post_composer"]).first
    await comp.click(timeout=int(cfg.get("composer_timeout_ms", 60000)))

    if payload.startswith("@BOT"):
        # Retry strategy: 3 attempts with wait windows 15s, 30s, 45s
        waits = [15000, 30000, 45000]
        bound = False
        for attempt, w in enumerate(waits, 1):
            dbg(f"@mention attempt {attempt} with wait {w}ms")
            bound = await bind_mention(page, comp, bot_name, char_delay, wait_ms=w)
            dbg("Bound @mention:", bound)
            if bound:
                break
        remainder = payload[len("@BOT"):].lstrip()
        if remainder:
            await comp.type(" " + remainder, delay=char_delay)
            dbg("Typed remainder chars:", len(remainder))
    else:
        await comp.type(payload, delay=char_delay)
        dbg("Typed payload chars:", len(payload))

    # Send with Ctrl+Enter (works even if button selector changes)
    try:
        await comp.press("Control+Enter")
    except Exception:
        try:
            await page.locator(SEL["send_button"]).click(timeout=3000)
        except Exception:
            await comp.press("Enter")


async def count_bot_msgs(page: Page, bot_name: str) -> int:
    js_path = pathlib.Path(__file__).parent / "js" / "count_bot_msgs.js"
    js = js_path.read_text(encoding="utf-8").replace("BOT_NAME_PLACEHOLDER", bot_name)
    return await page.evaluate(js)


async def extract_last_bot(page: Page, bot_name: str) -> Dict[str, Any]:
    js_path = pathlib.Path(__file__).parent / "js" / "extract_last_bot.js"
    js = js_path.read_text(encoding="utf-8").replace("BOT_NAME_PLACEHOLDER", bot_name)
    data = await page.evaluate(js)
    data["text"] = zwsp_strip(data.get("text","")).strip()
    for c in data.get("cards", []):
        c["text"] = zwsp_strip(c.get("text","")).strip()
    return data

async def open_last_thread_if_any(page: Page):
    # Try to open reply pane; ignore if no button visible
    try:
        await page.locator(SEL["reply_button"]).last.click(timeout=1500)
    except Exception:
        pass

async def run(cfg_path: str, corpus_path: str, bot_name_override: Optional[str] = None):
    run_ts = time.strftime('%y%m%d-%H%M%S')  # timestamp at run start
    cfg = read_yaml(cfg_path)
    corpus = read_jsonl(corpus_path)
    bot_name = bot_name_override or cfg.get("bot_name", "YourBotName")
    dbg("Config loaded:", {k: cfg.get(k) for k in ["teams_channel_url","browser_channel","headless","force_web_client"]})
    dbg("Bot:", bot_name, "Corpus items:", len(corpus))

    url = cfg.get("teams_channel_url")
    if not url:
        raise RuntimeError("teams_channel_url missing in config")

    # Force web client
    if cfg.get("force_web_client", True) and "client=webapp" not in url:
        url += ("&" if "?" in url else "?") + "client=webapp"

    headless = bool(cfg.get("headless", False))
    nav_timeout = int(cfg.get("navigate_timeout_ms", 120000))
    poll_ms = int(cfg.get("bot_response_poll_ms", 700))
    timeout_ms = int(cfg.get("bot_response_timeout_ms", 130000))
    storage_state = cfg.get("storage_state_file")

    # Artifacts
    artifacts_dir = pathlib.Path(cfg.get("artifacts_dir", "artifacts"))
    save_html = bool(cfg.get("save_html", True))
    dir_text = artifacts_dir / "text"
    dir_html = artifacts_dir / "html"
    dir_screens = artifacts_dir / "screens"
    for d in (artifacts_dir, dir_text, dir_screens, dir_html):
        pathlib.Path(d).mkdir(parents=True, exist_ok=True)


    async with async_playwright() as pw:
        channel = cfg.get("browser_channel", "msedge")
        launch_args = cfg.get("extra_args", [])
        try:
            browser = await pw.chromium.launch(channel=channel, headless=headless, args=launch_args)
        except Exception:
            # Fallback to vanilla Chromium if Edge channel not available
            browser = await pw.chromium.launch(headless=headless, args=launch_args)
        context: BrowserContext = await browser.new_context(storage_state=storage_state)
        page: Page = await context.new_page()
        dbg("Navigating to", url)
        await page.goto(url, timeout=nav_timeout)
        await ensure_web(page)
        dbg("Page loaded; ensuring web app mode done")

        # Results
        results_fp = pathlib.Path(corpus_path).with_suffix(".results.jsonl")

        with open(results_fp, "a", encoding="utf-8") as out:
            for i, row in enumerate(corpus, 1):
                payload = row.get("payload", "")
                t0 = time.time()

                # Focus composer and send payload
                try:
                    await page.locator(SEL["new_post_composer"]).first.click(timeout=int(cfg.get("composer_timeout_ms", 60000)))
                except Exception:
                    pass
                await send_payload(page, cfg, payload, bot_name)

                # Try thread pane (many bots reply there)
                await open_last_thread_if_any(page)
                dbg("Attempted to open thread pane (Reply)")

                # Baseline bot messages
                baseline = await count_bot_msgs(page, bot_name)
                dbg(f"Baseline bot message count: {baseline}")

                # Poll until new bot message appears
                ok = False
                reply: Dict[str, Any] = {"text": "", "html": "", "cards": []}
                deadline = time.time() + (timeout_ms / 1000.0)
                while time.time() < deadline:
                    await scroll_bottom(page, SEL["channel_list"])
                    cur = await count_bot_msgs(page, bot_name)
                    dbg(f"Poll: bot messages now {cur} (baseline {baseline})")
                    if cur > baseline:
                        # settle
                        await page.wait_for_timeout(800)
                        reply = await extract_last_bot(page, bot_name)
                        dbg("Detected new bot message; extracted lengths:", len(reply.get("text","")), len(reply.get("html","")))
                        ok = True
                        break
                    await page.wait_for_timeout(poll_ms)

                elapsed_ms = int((time.time() - t0) * 1000)
                record = {**row, "run_ts": run_ts, "ok": ok, "elapsed_ms": elapsed_ms, "bot_reply_text": reply.get("text",""), "bot_reply_html": reply.get("html",""), "bot_reply_cards": reply.get("cards", [])}
                out.write(json.dumps(record, ensure_ascii=False) + "\n")

                if not ok:
                    dbg("Timeout: no new bot message detected before deadline.")
                    try:
                        js = """
(() => {
  const listSel='[data-tid="channelMessageList"], [data-tid="threadList"]';
  const groupSel='[role="group"], [data-tid="messageCard"], [data-tid="message"]';
  const authorSel='[data-tid="messageAuthorName"], [data-tid="authorName"]';
  const arr=[];
  document.querySelectorAll(listSel).forEach(root=>{
    root.querySelectorAll(groupSel).forEach(g=>{
      const a=g.querySelector(authorSel);
      const t=(a?.textContent||'').trim();
      if(t) arr.push(t);
    });
  });
  return arr.slice(-10);
})();
"""
                        last_authors = await page.evaluate(js)
                        dbg("Last 10 authors:", last_authors)
                    except Exception:
                        pass
                # Save per-item artifacts
                item_id = row.get("id") or f"item_{i:03d}"
                # sanitize filename
                safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", item_id)

                # Append text with timestamp header
                txt_path = dir_text / f"{safe_id}.txt"
                with open(txt_path, "a", encoding="utf-8") as tf:
                    tf.write("\n\n=== RUN " + run_ts + " ===\n")
                    tf.write(reply.get("text",""))

                # Append HTML (optional) with timestamp header
                if save_html:
                    html_path = dir_html / f"{safe_id}.html"
                    with open(html_path, "a", encoding="utf-8") as hf:
                        hf.write("\n\n<!-- RUN " + run_ts + " -->\n")
                        hf.write(reply.get("html",""))

                # Always take a screenshot with timestamp in filename (yyMMdd-HHmmss)
                ss_path = dir_screens / f"{safe_id}.{run_ts}.png"
                try:
                    await page.screenshot(path=str(ss_path), full_page=True)
                except Exception:
                    pass

                # small pacing


                await page.wait_for_timeout(500)

        # Persist session if path provided
        if storage_state:
            try:
                await context.storage_state(path=storage_state)
            except Exception:
                pass
        await context.close()
        await browser.close()
        print(f"Wrote results to: {results_fp}")
        print("Done.")

def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="teams_recon.yaml", help="YAML config path")
    ap.add_argument("--corpus", required=True, help="JSONL corpus path")
    ap.add_argument("--bot-name", help="Override bot name")
    ap.add_argument("--debug", action="store_true", help="Enable verbose debug output to CLI")
    args = ap.parse_args()

    global DEBUG
    DEBUG = args.debug

    asyncio.run(run(args.config, args.corpus, args.bot_name))

if __name__ == "__main__":
    cli()