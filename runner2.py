#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio, json, os, sys, time, argparse, pathlib
from typing import Callable, Optional, Tuple, List, Any

import yaml
from playwright.async_api import async_playwright, Page, BrowserContext, ElementHandle, TimeoutError as PWTimeout

# -----------------------------
# Config helpers
# -----------------------------

def load_yaml(fp: str) -> dict:
    with open(fp, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def cfg_i(cfg: dict, key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except Exception:
        return default

def cfg_b(cfg: dict, key: str, default: bool) -> bool:
    v = cfg.get(key, default)
    if isinstance(v, bool): return v
    return str(v).strip().lower() in ("1","true","yes","y","on")

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
# Teams formatting defang
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
# Typing & find helpers
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
    print(f"[*] find_in_frames: timeout={timeout}ms")
    deadline = time.time() + (timeout / 1000)
    last_err = None
    while time.time() < deadline:
        for fr in page.frames:
            try:
                loc = maker(fr)
                try:
                    handle = await loc.element_handle(timeout=200)
                    if handle:
                        print("[*] find_in_frames: element found")
                        return handle
                except Exception:
                    pass
            except Exception as e:
                last_err = e
        await page.wait_for_timeout(120)
    if last_err:
        print(f"[warn] find_in_frames timeout; last_err={last_err}")
    return None

# -----------------------------
# Mention + payload
# -----------------------------

async def type_mention_and_payload(page: Page, bot_name: str, full_text: str, cfg: dict):
    name_delay = cfg_i(cfg, "mention_name_char_delay_ms", 40)
    popup_wait_ms = cfg_i(cfg, "mention_popup_wait_ms", 2500)
    popup_poll_ms = cfg_i(cfg, "mention_popup_poll_ms", 150)
    defang_mode = str(cfg.get("defang_fences", "off")).lower()

    def composer_locator(fr):
        return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))
    composer_timeout = cfg_i(cfg, "composer_timeout_ms", 30000)
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

    pre = full_text[:i]
    tail = full_text[i + len(token):].lstrip()

    if pre.strip():
        print("[*] Typing preface before mention…")
        await _type_with_safe_newlines(page, pre + " ", delay=12)

    print(f"[*] Typing @ mention for {bot_name} …")
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
                if picked:
                    break
        await page.wait_for_timeout(popup_poll_ms)
        elapsed += popup_poll_ms

    await page.wait_for_timeout(250)
    await page.keyboard.press("ArrowRight")
    await page.keyboard.type(" ", delay=25)

    if tail:
        print("[*] Typing tail payload…")
        tail = _sanitize_payload_for_teams(tail, defang_mode)
        await _type_with_safe_newlines(page, tail, delay=14)

# -----------------------------
# Send message
# -----------------------------

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

# -----------------------------
# URL normalizer (forces web)
# -----------------------------

def normalize_teams_url(url: str, force_web: bool = True) -> str:
    if not force_web:
        return url
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
# Block desktop-app launches at source
# -----------------------------

WEB_GUARD_JS = r"""
(() => {
  const isBad = (u) => {
    if (!u || typeof u !== 'string') return false;
    const s = u.trim().toLowerCase();
    return s.startsWith('msteams:') || s.includes('openapp=true') || s.includes('launchapp=true');
  };
  try {
    try {
      localStorage.setItem('clientType', 'web');
      localStorage.setItem('desktopAppPreference', 'web');
      sessionStorage.setItem('clientType', 'web');
    } catch(e) {}
    document.addEventListener('click', (e) => {
      const a = e.target && e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      const href = a.getAttribute('href') || '';
      if (isBad(href)) {
        e.preventDefault(); e.stopImmediatePropagation();
      }
    }, true);
    const _open = window.open;
    window.open = function(u, ...rest) {
      if (isBad(u)) return null;
      return _open ? _open.call(this, u, ...rest) : null;
    };
    const _assign = window.location.assign.bind(window.location);
    const _replace = window.location.replace.bind(window.location);
    window.location.assign = (u) => { if (!isBad(u)) _assign(u); };
    window.location.replace = (u) => { if (!isBad(u)) _replace(u); };
    const ensureWebFlags = (u) => {
      if (!u) return u;
      try {
        const url = new URL(u, window.location.href);
        url.searchParams.set('clientType', 'web');
        url.searchParams.set('web', '1');
        return url.toString();
      } catch(e) { return u; }
    };
    const _push = history.pushState.bind(history);
    const _rep = history.replaceState.bind(history);
    history.pushState = (s, t, u) => _push(s, t, ensureWebFlags(u));
    history.replaceState = (s, t, u) => _rep(s, t, ensureWebFlags(u));
  } catch (_e) {}
})();
"""

async def install_web_only_guards(context: BrowserContext, enabled: bool):
    if enabled:
        print("[*] Installing web-only JS guards…")
        await context.add_init_script(WEB_GUARD_JS)

# -----------------------------
# Scrape bot/agent messages
# -----------------------------

BODY_CSS = (
    "li:has([data-tid='messageBody']), "
    "div[data-tid='messageBody'], "
    "div[data-tid='messageBodyContent'], "
    "div.ui-chat__messagecontent, "
    "div.message-body, "
    "div[class*='messageBody']"
)

async def scrape_latest_bot_messages(page: Page, bot_name: str, max_msgs: int = 1) -> List[str]:
    messages = []
    for fr in page.frames:
        try:
            nodes = await fr.locator(BODY_CSS).all()
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

async def get_latest_bot_text(page: Page, bot_name: str) -> str:
    msgs = await scrape_latest_bot_messages(page, bot_name, max_msgs=1)
    return msgs[0] if msgs else ""

async def wait_for_new_bot_response(page: Page, bot_name: str, baseline_text: str, cfg: dict) -> str:
    timeout_ms = cfg_i(cfg, "bot_response_timeout_ms", 120000)  # 120s default
    poll_ms = cfg_i(cfg, "bot_response_poll_ms", 800)
    print(f"[*] Waiting for bot response up to {timeout_ms}ms …")
    deadline = time.time() + (timeout_ms / 1000)
    last_seen = baseline_text or ""
    while time.time() < deadline:
        try:
            txt = await get_latest_bot_text(page, bot_name)
        except Exception as e:
            txt = ""
        if txt and txt != last_seen:
            print("[*] Bot response detected.")
            return txt
        await page.wait_for_timeout(poll_ms)
    print("[warn] Bot response wait timed out.")
    return ""

# -----------------------------
# Dismiss "open in app" banners (fallback)
# -----------------------------

async def dismiss_open_in_app_banners(page: Page, timeout_ms: int = 4000):
    import re
    names = [
        "Use the web app instead",
        "Use the web app",
        "Continue in browser",
        "Continue on this browser",
        "Continue on web",
        "Continue on the web",
    ]
    print("[*] Checking for 'use web app' banners…")
    deadline = time.time() + timeout_ms/1000
    while time.time() < deadline:
        for fr in page.frames:
            for nm in names:
                try:
                    btn = fr.get_by_role("button", name=re.compile(nm, re.I))
                    h = await btn.element_handle(timeout=200)
                    if h:
                        print("[*] Clicking web-app banner…")
                        await h.click()
                        return True
                except Exception:
                    pass
        await page.wait_for_timeout(150)
    return False

# -----------------------------
# Browser / context
# -----------------------------

async def make_context(pw, cfg: dict) -> Tuple[BrowserContext, Page]:
    channel = "msedge"
    launch_args = ["--inprivate"]
    if cfg.get("extra_args"):
        launch_args += list(cfg["extra_args"])
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
    if not fp:
        return
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    await context.storage_state(path=fp)
    print(f"[*] Session storage saved to {fp}")

# -----------------------------
# Main flow
# -----------------------------

async def run(args):
    cfg = load_yaml(args.config)

    force_web_client = cfg_b(cfg, "force_web_client", True)
    install_guards = cfg_b(cfg, "install_web_guards", True)
    navigate_timeout_ms = cfg_i(cfg, "navigate_timeout_ms", 90000)  # you said ~60s, default 90s
    post_send_wait_ms = cfg_i(cfg, "post_send_wait_ms", 1200)       # tiny settle time before polling

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

        await install_web_only_guards(context, enabled=install_guards)

        teams_url = normalize_teams_url(teams_url, force_web=force_web_client)
        print(f"[*] Navigating to {teams_url}")
        try:
            await page.goto(teams_url, wait_until="load", timeout=navigate_timeout_ms)
        except PWTimeout:
            print(f"[warn] Navigation hit timeout ({navigate_timeout_ms}ms). Continuing anyway…")

        await dismiss_open_in_app_banners(page, timeout_ms=cfg_i(cfg, "banner_dismiss_timeout_ms", 4000))

        def composer_locator(fr):
            return fr.get_by_role("textbox").or_(fr.locator("//div[@contenteditable='true' and not(@role)]"))
        composer_timeout = cfg_i(cfg, "composer_timeout_ms", 30000)
        print("[*] Verifying composer is present…")
        composer = await find_in_frames(page, composer_locator, timeout=composer_timeout)
        if not composer:
            print("[!] Composer not found. Make sure the channel is open.")
            await context.close()
            return

        runs_dir = pathlib.Path("runs")
        runs_dir.mkdir(exist_ok=True)
        out_fp = runs_dir / (pathlib.Path(args.corpus).stem + ".out.jsonl")
        print(f"[*] Writing results to {out_fp}")

        with open(out_fp, "a", encoding="utf-8") as outf:
            for item in corpus:
                case_id = item.get("id") or ""
                payload = item.get("payload") or ""
                goal = item.get("goal") or ""
                target = item.get("target") or ""

                print(f"\n[case {case_id or 'no-id'}] Sending payload …")
                baseline = ""
                try:
                    baseline = await get_latest_bot_text(page, bot_name)
                except Exception:
                    baseline = ""

                try:
                    await type_mention_and_payload(page, bot_name, payload, cfg)
                except Exception as e:
                    print(f"[warn] type_mention failed for {case_id}: {e}")

                try:
                    await click_send(page, cfg)
                except Exception as e:
                    print(f"[warn] send failed for {case_id}: {e}")

                await page.wait_for_timeout(post_send_wait_ms)

                bot_text = await wait_for_new_bot_response(page, bot_name, baseline_text=baseline, cfg=cfg)

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