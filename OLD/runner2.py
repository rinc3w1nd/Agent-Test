#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio, json, os, sys, time, argparse, pathlib
from typing import Callable, Tuple, List
import yaml
from playwright.async_api import async_playwright, Page, BrowserContext, ElementHandle, TimeoutError as PWTimeout

# =========================
# Config + IO helpers
# =========================

def load_yaml(fp: str) -> dict:
    with open(fp, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def cfg_i(cfg: dict, key: str, default: int) -> int:
    try: return int(cfg.get(key, default))
    except Exception: return default

def cfg_b(cfg: dict, key: str, default: bool) -> bool:
    v = cfg.get(key, default)
    if isinstance(v, bool): return v
    return str(v).strip().lower() in ("1","true","yes","y","on")

def read_jsonl(fp: str) -> List[dict]:
    out = []
    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s: continue
            try: out.append(json.loads(s))
            except Exception: out.append({"payload": s})
    return out

# =========================
# Teams text defang
# =========================

def _sanitize_payload_for_teams(text: str, mode: str = "off") -> str:
    if not text or mode == "off": return text
    if mode == "zwsp":  return text.replace("```", "`\u200b``")
    if mode == "space": return text.replace("```", "` ``")
    return text

# =========================
# Typing & finding helpers
# =========================

async def _type_with_safe_newlines(page: Page, text: str, delay: int = 14):
    for ch in text:
        if ch == "\n":
            await page.keyboard.down("Shift")
            await page.keyboard.press("Enter")
            await page.keyboard.up("Shift")
        else:
            await page.keyboard.type(ch, delay=delay)

async def find_in_frames(page: Page, maker: Callable, timeout: int = 10000):
    print(f"[*] find_in_frames: timeout={timeout}ms")
    deadline = time.time() + timeout/1000
    last_err = None
    while time.time() < deadline:
        for fr in page.frames:
            try:
                loc = maker(fr)
                h = await loc.element_handle(timeout=200)
                if h:
                    print("[*] find_in_frames: element found")
                    return h
            except Exception as e:
                last_err = e
        await page.wait_for_timeout(120)
    if last_err:
        print(f"[warn] find_in_frames timeout; last_err={last_err}")
    return None

# =========================
# Mention + payload
# =========================

async def type_mention_and_payload(page: Page, bot_name: str, full_text: str, cfg: dict):
    name_delay    = cfg_i(cfg, "mention_name_char_delay_ms", 40)
    popup_wait_ms = cfg_i(cfg, "mention_popup_wait_ms", 4000)
    popup_poll_ms = cfg_i(cfg, "mention_popup_poll_ms", 120)
    defang_mode   = str(cfg.get("defang_fences", "off")).lower()

    def composer_locator(fr):
        return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))

    composer_timeout = cfg_i(cfg, "composer_timeout_ms", 60000)
    print("[*] Locating composer…")
    composer = await find_in_frames(page, composer_locator, timeout=composer_timeout)
    if not composer:
        raise RuntimeError("Composer textbox not found.")
    await composer.click()

    if "@BOT" in full_text:
        full_text = full_text.replace("@BOT", f"@{bot_name}")
    full_text = _sanitize_payload_for_teams(full_text, defang_mode)

    token = f"@{bot_name}"
    i = full_text.find(token)
    if i == -1:
        print("[*] No @mention token found, typing payload only")
        await _type_with_safe_newlines(page, full_text, delay=14)
        return

    pre  = full_text[:i]
    tail = full_text[i + len(token):].lstrip()

    if pre.strip():
        print("[*] Typing preface before mention…")
        await _type_with_safe_newlines(page, pre + " ", delay=12)

    print(f"[*] Typing @ mention for {bot_name}…")
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
                            if txt: opts.append((h, txt))
                        except Exception:
                            pass
                except Exception:
                    pass
        return opts

    async def click_option_center(handle: ElementHandle):
        box = await handle.bounding_box()
        if not box: return False
        await page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
        return True

    print("[*] Waiting for mention popup…")
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
                try:
                    picked = await click_option_center(handle)
                    if not picked:
                        await handle.scroll_into_view_if_needed()
                        await handle.click(force=True)
                        picked = True
                    print("[*] Mention chosen from suggestions")
                except Exception as e:
                    print(f"[warn] Mention click failed: {e}")
                if picked: break
        await page.wait_for_timeout(popup_poll_ms)
        elapsed += popup_poll_ms

    await page.wait_for_timeout(250)
    await page.keyboard.press("ArrowRight")
    await page.keyboard.type(" ", delay=25)

    if tail:
        print("[*] Typing tail payload…")
        tail = _sanitize_payload_for_teams(tail, defang_mode)
        await _type_with_safe_newlines(page, tail, delay=14)

# =========================
# Send message
# =========================

async def click_send(page: Page, cfg: dict):
    import re
    def send_btn(fr):
        return fr.get_by_role("button", name=re.compile("Send", re.I))
    tmo = cfg_i(cfg, "send_button_timeout_ms", 8000)
    print("[*] Clicking Send…")
    btn = await find_in_frames(page, send_btn, timeout=tmo)
    if not btn:
        raise RuntimeError("Send button not found; refusing to press Enter fallback.")
    await btn.click()

# =========================
# URL normalizer (optional)
# =========================

def normalize_teams_url(url: str, force_web: bool = True) -> str:
    if not force_web: return url
    try:
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        u = urlparse(url)
        qs = dict(parse_qsl(u.query, keep_blank_values=True))
        qs["clientType"] = "web"
        qs["web"] = "1"
        qs["preferDesktopApp"] = "false"
        new_query = urlencode(qs, doseq=True)
        return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}clientType=web&web=1&preferDesktopApp=false"

# =========================
# AI flair–aware DOM helpers (install AFTER composer exists)
# =========================

AI_DOM_HELPERS = r"""
(() => {
  if (window.__teamsAiHelpers) return;
  window.__teamsAiHelpers = true;

  const BODY_SEL = "li:has([data-tid='messageBody']), div[data-tid='messageBody'], div[data-tid='messageBodyContent'], div.ui-chat__messagecontent, div.message-body, div[class*='messageBody']";
  const QUOTE_SEL = "blockquote, [role='blockquote'], [data-tid*='quote'], .quote, .quotedMessage, [data-tid='reply-quote']";

  const contains = (el, sel) => !!(el && el.querySelector(sel));

  const isSelf = (container) => {
    const cls  = (container.getAttribute("class") || "").toLowerCase();
    const aria = (container.getAttribute("aria-label") || "").toLowerCase();
    if (cls.includes("outgoing") || cls.includes("from-me") || cls.includes(" me ")) return true;
    if (aria.includes("you said") || aria.includes("your message")) return true;
    return false;
  };

  const extractText = (container) => {
    const body = container.querySelector(BODY_SEL) || container;
    if (!body) return "";
    let txt = body.innerText || "";
    txt = txt.replace(/\r/g, "").trim();
    try {
      const blocks = body.querySelectorAll(QUOTE_SEL);
      blocks.forEach(b => {
        const t = (b.innerText || "").replace(/\r/g,"");
        if (!t.trim()) return;
        const replaced = t.split("\n").map(l => (l.trim() ? ("> " + l.trim()) : "")).join("\n");
        txt = txt.replace(t, replaced);
      });
    } catch(e) {}
    return txt.trim();
  };

  window.__getLastAiDisclaimerMessage = (markerSubstr) => {
    const sub = (markerSubstr || "fai-AiGeneratedDisclaimer").toLowerCase();
    const aiSpans = Array.from(document.querySelectorAll("span[class]")).filter(s => (s.getAttribute("class")||"").toLowerCase().includes(sub));
    for (let i = aiSpans.length - 1; i >= 0; i--) {
      let c = aiSpans[i].closest("li,div");
      let hops = 0;
      while (c && hops < 10) {
        if (contains(c, BODY_SEL)) break;
        c = c.parentElement; hops++;
      }
      if (!c) continue;
      if (isSelf(c)) continue;
      const t = extractText(c);
      if (t) return t;
    }
    return "";
  };

  window.__getLatestIncomingText = () => {
    const nodes = Array.from(document.querySelectorAll(BODY_SEL));
    for (let i = nodes.length - 1; i >= 0; i--) {
      const c = nodes[i].closest("li,div");
      if (!c) continue;
      if (isSelf(c)) continue;
      const t = extractText(c);
      if (t) return t;
    }
    return "";
  };
})();
"""

async def install_ai_dom_helpers(page: Page):
    print("[*] Installing AI-flair DOM helpers…")
    # Install into the current document. (We install AFTER composer is found.)
    try:
        await page.evaluate(AI_DOM_HELPERS)
    except Exception as e:
        print(f"[warn] AI helpers injection failed: {e}")

async def force_scroll_chat_bottom(page: Page, attempts: int = 4):
    js = """
      (() => {
        const cand = document.querySelector("[role='log'], div[aria-live='polite'], div[aria-live='assertive']") || document.scrollingElement;
        if (!cand) return false;
        cand.scrollTop = cand.scrollHeight;
        return true;
      })();
    """
    for _ in range(attempts):
        try:
            ok = await page.evaluate(js)
            if ok: break
        except Exception:
            pass
        await page.wait_for_timeout(200)

async def wait_for_ai_or_incoming(page: Page, flair_substr: str, cfg: dict) -> str:
    timeout_ms = cfg_i(cfg, "bot_response_timeout_ms", 130000)
    poll_ms    = cfg_i(cfg, "bot_response_poll_ms", 700)
    print(f"[*] Waiting up to {timeout_ms}ms for incoming (AI flair '{flair_substr}' preferred)…")

    deadline = time.time() + timeout_ms/1000
    while time.time() < deadline:
        await force_scroll_chat_bottom(page, attempts=1)
        txt = ""
        try:
            txt = await page.evaluate("window.__getLastAiDisclaimerMessage && window.__getLastAiDisclaimerMessage(arguments[0])", flair_substr)
        except Exception:
            pass
        if not txt:
            try:
                txt = await page.evaluate("window.__getLatestIncomingText && window.__getLatestIncomingText()")
            except Exception:
                pass
        if txt:
            print("[*] Incoming message detected.")
            return txt
        await page.wait_for_timeout(poll_ms)

    print("[warn] Bot response wait timed out.")
    return ""

# =========================
# Browser / context
# =========================

async def make_context(pw, cfg: dict) -> Tuple[BrowserContext, Page]:
    channel = "msedge"
    launch_args = ["--inprivate"]
    if cfg.get("extra_args"): launch_args += list(cfg["extra_args"])
    print(f"[*] Launching Edge with args: {launch_args}")
    browser = await pw.chromium.launch(channel=channel, headless=False, args=launch_args)
    storage_state_file = cfg.get("storage_state_file")
    if storage_state_file and os.path.exists(storage_state_file):
        print(f"[*] Using storage_state: {storage_state_file}")
        context = await browser.new_context(storage_state=storage_state_file)
    else:
        context = await browser.new_context()
    page = await context.new_page()
    return context, page

async def save_storage_state(context: BrowserContext, cfg: dict):
    fp = cfg.get("storage_state_file")
    if not fp: return
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    await context.storage_state(path=fp)
    print(f"[*] Session storage saved to {fp}")

# =========================
# Main flow
# =========================

async def run(args):
    cfg = load_yaml(args.config)

    force_web_client      = cfg_b(cfg, "force_web_client", True)
    navigate_timeout_ms   = cfg_i(cfg, "navigate_timeout_ms", 120000)
    post_send_wait_ms     = cfg_i(cfg, "post_send_wait_ms", 600)
    ai_flair_class_substr = str(cfg.get("ai_flair_class_substr", "fai-AiGeneratedDisclaimer"))

    corpus = read_jsonl(args.corpus)
    if not corpus:
        print("Corpus appears empty.")
        return

    teams_url = cfg.get("teams_channel_url") or "https://teams.microsoft.com/"
    bot_name  = cfg.get("bot_name") or args.bot_name
    if not bot_name:
        print("ERROR: bot_name not provided (config.yaml bot_name or --bot-name).")
        sys.exit(2)

    async with async_playwright() as pw:
        context, page = await make_context(pw, cfg)

        teams_url = normalize_teams_url(teams_url, force_web=force_web_client)
        print(f"[*] Navigating to {teams_url}")
        try:
            await page.goto(teams_url, wait_until="load", timeout=navigate_timeout_ms)
        except PWTimeout:
            print(f"[warn] Navigation hit timeout ({navigate_timeout_ms}ms). Continuing anyway…")

        # 1) Ensure composer exists BEFORE tying in helpers (avoids attaching to the wrong tree)
        def composer_locator(fr):
            return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))
        composer_timeout = cfg_i(cfg, "composer_timeout_ms", 60000)
        print("[*] Verifying composer is present…")
        composer = await find_in_frames(page, composer_locator, timeout=composer_timeout)
        if not composer:
            print("[!] Composer not found. Make sure the channel is open.")
            await context.close()
            return

        # 2) Now that chat UI is present, install AI helpers
        await install_ai_dom_helpers(page)

        # Optional: wait for any message body so DOM queries have something to chew on
        BODY_CSS = ("li:has([data-tid='messageBody']), "
                    "div[data-tid='messageBody'], "
                    "div[data-tid='messageBodyContent'], "
                    "div.ui-chat__messagecontent, "
                    "div.message-body, "
                    "div[class*='messageBody']")
        try:
            first_body_tmo = cfg_i(cfg, "first_body_timeout_ms", 30000)
            await page.wait_for_selector(BODY_CSS, timeout=first_body_tmo)
            print("[*] First message body present.")
        except Exception:
            print("[warn] No message body found before timeout. Continuing.")

        runs_dir = pathlib.Path("runs")
        runs_dir.mkdir(exist_ok=True)
        out_fp = runs_dir / (pathlib.Path(args.corpus).stem + ".out.jsonl")
        print(f"[*] Writing results to {out_fp}")

        with open(out_fp, "a", encoding="utf-8") as outf:
            for item in corpus:
                case_id = item.get("id") or ""
                payload = item.get("payload") or ""
                goal    = item.get("goal") or ""
                target  = item.get("target") or ""

                print(f"\n[case {case_id or 'no-id'}] Sending payload …")

                try:
                    await type_mention_and_payload(page, bot_name, payload, cfg)
                except Exception as e:
                    print(f"[warn] type_mention failed for {case_id}: {e}")

                try:
                    await click_send(page, cfg)
                except Exception as e:
                    print(f"[warn] send failed for {case_id}: {e}")

                await force_scroll_chat_bottom(page, attempts=2)
                await page.wait_for_timeout(post_send_wait_ms)

                bot_text = await wait_for_ai_or_incoming(page, ai_flair_class_substr, cfg)

                rec = {
                    "id": case_id,
                    "goal": goal,
                    "target": target,
                    "payload": payload,
                    "sent_payload": payload.replace("@BOT", f"@{bot_name}"),
                    "bot_response": bot_text,
                    "screenshot": ""
                }
                outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                outf.flush()
                print(f"[+] Sent {case_id or '[no-id]'} | bot_response_len={len(bot_text)}")

        await save_storage_state(context, cfg)
        await context.close()
        print(f"[*] Done. Saved output to {out_fp}")

# =========================
# CLI
# =========================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml", help="Path to YAML config")
    ap.add_argument("--corpus", required=True, help="Path to JSONL corpus")
    ap.add_argument("--bot-name", help="Overrides bot_name from config")
    args = ap.parse_args()
    asyncio.run(run(args))

if __name__ == "__main__":
    main()