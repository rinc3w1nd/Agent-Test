
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio, json, os, sys, time, argparse, pathlib, re
from typing import List, Dict, Any
import yaml
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PWTimeout

# =========================
# Config helpers
# =========================

def load_yaml(fp: str) -> dict:
    with open(fp, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_jsonl(fp: str) -> List[Dict[str, Any]]:
    rows = []
    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows

def zwsp_strip(s: str) -> str:
    # remove zero-width characters that might confuse length checks
    zw = ''.join(['\u200b', '\u200c', '\u200d', '\ufeff'])
    return s.translate({ord(c): None for c in zw})

# =========================
# Teams automation
# =========================

SELECTORS = {
    "use_web_app": "text=Use the web app instead",
    "continue_web": "text=Continue on web",
    "channel_list": '[data-tid="channelMessageList"], [data-tid="threadList"]',
    "message_group": '[role="group"], [data-tid="messageCard"], [data-tid="message"]',
    "author": '[data-tid="messageAuthorName"], [data-tid="authorName"]',
    "body": '[data-tid="messageBody"], [data-tid="messageText"], [data-tid="adaptiveCardRoot"], [data-tid="messageContent"]',
    "new_post_composer": '[data-tid="newMessageInputComposer"], [data-tid="ck-editor"] textarea, [contenteditable="true"]',
    "send_button": '[data-tid="sendMessageButton"]',
    "reply_button": '[data-tid="replyInThread"], [aria-label="Reply"]',
    "mention_popup": '[data-tid="mentionSuggestList"], [data-tid="mentionSuggestions"]',
}

async def ensure_web_app(page: Page):
    # If desktop app prompt appears, click to web
    try:
        await page.locator(SELECTORS["use_web_app"]).first.click(timeout=3000)
    except Exception:
        pass
    try:
        await page.locator(SELECTORS["continue_web"]).first.click(timeout=3000)
    except Exception:
        pass

async def scroll_bottom(page: Page, container_sel: str):
    for _ in range(4):
        loc = page.locator(container_sel).last
        try:
            await loc.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            break
        await page.wait_for_timeout(250)

async def bind_real_mention(page: Page, composer, bot_name: str, type_delay: int):
    # Type @ + bot name, then select from mention popup (Enter)
    await composer.type("@", delay=type_delay)
    await composer.type(bot_name, delay=type_delay)
    # Wait for popup and confirm
    try:
        await page.locator(SELECTORS["mention_popup"]).wait_for(timeout=4000)
        await composer.press("Enter")
    except Exception:
        # Fallback: still send literal text
        pass

async def send_payload_with_mention(page: Page, cfg: dict, payload: str, bot_name: str):
    # Convert placeholder @BOT to a real bound mention by typing it specially
    # Split payload into tokens to find @BOT
    type_delay = int(cfg.get("mention_name_char_delay_ms", 35))
    # Focus composer
    composer = page.locator(SELECTORS["new_post_composer"]).first
    await composer.click(timeout=int(cfg.get("composer_timeout_ms", 60000)))
    # Process payload
    if payload.startswith("@BOT"):
        # Bind a real mention, then type remainder
        await bind_real_mention(page, composer, bot_name, type_delay)
        remainder = payload[len("@BOT"):].lstrip()
        if remainder:
            await composer.type(" " + remainder, delay=type_delay)
    else:
        # Just type the whole payload
        await composer.type(payload, delay=type_delay)

    # Send with Ctrl+Enter (works even if button selector changes)
    try:
        await composer.press("Control+Enter")
    except Exception:
        try:
            await page.locator(SELECTORS["send_button"]).click(timeout=3000)
        except Exception:
            # Fallback: plain Enter
            await composer.press("Enter")

async def wait_for_bot_reply(page: Page, cfg: dict, bot_name: str) -> Dict[str, Any]:
    timeout_ms = int(cfg.get("bot_response_timeout_ms", 130000))
    poll_ms = int(cfg.get("bot_response_poll_ms", 700))
    start = time.time()

    # Watch both channel main list and thread list
    list_sel = SELECTORS["channel_list"]

    # Baseline count of messages authored by bot
    def count_script(name: str):
        return f"""
(() => {{
  const rootList = document.querySelectorAll('{list_sel}');
  let botCount = 0;
  for (const root of rootList) {{
    const groups = root.querySelectorAll('{SELECTORS["message_group"]}');
    for (const g of groups) {{
      const authorNode = g.querySelector('{SELECTORS["author"]}');
      const aria = (g.getAttribute('aria-label')||'');
      const atext = (authorNode?.textContent||'').trim();
      if (atext === {json.dumps(name)} || aria.includes(`${{ {json.dumps(name)} }} app said`) || aria.includes(`${{ {json.dumps(name)} }} posted`)) {{
        botCount++;
      }}
    }}
  }}
  return botCount;
}})()
"""
    baseline = await page.evaluate(count_script(bot_name))
    # Now poll for a new message
    while (time.time() - start) * 1000 < timeout_ms:
        await scroll_bottom(page, list_sel)
        bot_count = await page.evaluate(count_script(bot_name))
        if bot_count > baseline:
            # Extract last bot message
            data = await page.evaluate(f"""
(() => {{
  const result = {{text: '', html: ''}};
  const rootList = document.querySelectorAll('{list_sel}');
  let lastBot = null;
  for (const root of rootList) {{
    const groups = root.querySelectorAll('{SELECTORS["message_group"]}');
    for (const g of groups) {{
      const authorNode = g.querySelector('{SELECTORS["author"]}');
      const aria = (g.getAttribute('aria-label')||'');
      const atext = (authorNode?.textContent||'').trim();
      if (atext === {json.dumps(bot_name)} || aria.includes(`${{ {json.dumps(bot_name)} }} app said`) || aria.includes(`${{ {json.dumps(bot_name)} }} posted`)) {{
        lastBot = g;
      }}
    }}
  }}
  if (!lastBot) return result;
  const body = lastBot.querySelector('{SELECTORS["body"]}');
  if (body) {{
    result.text = body.innerText || body.textContent || '';
    result.html = body.innerHTML || '';
  }} else {{
    // fallback: get all text within the group
    result.text = lastBot.innerText || lastBot.textContent || '';
    result.html = lastBot.innerHTML || '';
  }}
  return result;
}})()
""")
            # Stabilization wait for rich cards to render
            await page.wait_for_timeout(800)
            data["text"] = zwsp_strip(data.get("text","")).strip()
            return data
        await page.wait_for_timeout(poll_ms)
    raise PWTimeout(f"Timed out waiting for bot reply after {timeout_ms}ms")

async def run(args):
    cfg = load_yaml(args.config)
    corpus = load_jsonl(args.corpus)
    bot_name = args.bot_name or cfg.get("bot_name", "YourBotName")

    nav_timeout = int(cfg.get("navigate_timeout_ms", 120000))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=cfg.get("headless", True), args=cfg.get("extra_args", []))
        context = await browser.new_context(storage_state=cfg.get("storage_state_file", None))
        page = await context.new_page()
        url = cfg.get("teams_channel_url")
        if not url:
            raise RuntimeError("teams_channel_url missing in config")
        # Force web client
        if cfg.get("force_web_client", True) and "client=webapp" not in url:
            sep = "&" if "?" in url else "?"
            url = url + f"{sep}client=webapp"
        await page.goto(url, timeout=nav_timeout)
        await ensure_web_app(page)

        results = []
        for row in corpus:
            payload = row.get("payload", "")
            # Clear composer focus to ensure caret is visible
            try:
                await page.locator(SELECTORS["new_post_composer"]).first.click(timeout=int(cfg.get("composer_timeout_ms", 60000)))
            except Exception:
                pass

            # Send payload (binding @BOT -> real mention selection)
            await send_payload_with_mention(page, cfg, payload, bot_name)

            # If in channels, many bots reply in thread; try clicking reply on the last post to open thread pane
            try:
                await page.locator(SELECTORS["reply_button"]).last.click(timeout=2000)
            except Exception:
                pass

            # Wait for bot reply
            try:
                reply = await wait_for_bot_reply(page, cfg, bot_name)
                results.append({
                    **row,
                    "bot_reply_text": reply.get("text",""),
                    "bot_reply_html": reply.get("html",""),
                    "ok": True
                })
            except PWTimeout:
                results.append({**row, "bot_reply_text": "", "bot_reply_html": "", "ok": False})

            # Small delay between items
            await page.wait_for_timeout(600)

        # Write results JSONL next to corpus
        out_fp = pathlib.Path(args.corpus).with_suffix(".results.jsonl")
        with open(out_fp, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"Wrote results to: {out_fp}")

        await context.storage_state(path=cfg.get("storage_state_file", "auth/auth_state.json"))
        await context.close()
        await browser.close()

# =========================
# CLI
# =========================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config2.yaml", help="Path to YAML config")
    ap.add_argument("--corpus", required=True, help="Path to JSONL corpus")
    ap.add_argument("--bot-name", help="Overrides bot_name from config")
    args = ap.parse_args()
    asyncio.run(run(args))

if __name__ == "__main__":
    main()
