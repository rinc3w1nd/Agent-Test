# pip install playwright pyyaml
# playwright install

import sys, time, re, yaml
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CONFIG = "config.yaml"
STATE_FILE = "auth_state.json"

def load_cfg():
    with open(CONFIG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def channel_deeplink(team_id, channel_id, tenant_id=None):
    base = f"https://teams.microsoft.com/l/channel/{channel_id}/_?groupId={team_id}"
    if tenant_id:
        base += f"&tenantId={tenant_id}"
    return base

def wait_for_compose(page, max_wait_ms=300_000):
    """Wait up to ~5 minutes for Teams to finish loading the channel compose box."""
    box = page.get_by_role("textbox", name=re.compile("Type a message", re.I))
    box.wait_for(timeout=max_wait_ms)
    return box

def bind_mention(page, bot_name: str):
    """Type @Name and bind it from the suggestion list (fallback to keyboard)."""
    compose = page.get_by_role("textbox", name=re.compile("Type a message", re.I))
    compose.click()
    compose.type(f"@{bot_name}", delay=40)

    try:
        # Prefer accessible roles when the suggestion list appears
        listbox = page.get_by_role("listbox")
        listbox.wait_for(timeout=15_000)
        option = page.get_by_role("option", name=re.compile(rf"^{re.escape(bot_name)}$", re.I))
        option.click()
    except PWTimeout:
        # Fallback: keyboard select first suggestion
        page.keyboard.press("ArrowDown"); page.keyboard.press("Enter")
    except Exception:
        page.keyboard.press("Enter")

    time.sleep(0.3)  # let the mention chip resolve
    return compose

def main():
    # Usage:
    #   First run (to save auth): python ui_send_mention_stateful_wait.py save
    #   Later runs (reuse auth):  python ui_send_mention_stateful_wait.py
    save_state = len(sys.argv) > 1 and sys.argv[1].lower().startswith("save")

    cfg = load_cfg()
    team_id    = cfg["team"]["id"]
    channel_id = cfg["channel"]["id"]          # decoded: 19:...@thread.tacv2
    tenant_id  = cfg.get("tenant_id")          # optional
    bot_name   = cfg["bot"]["name"]
    text       = cfg["message"]["text"]

    url = channel_deeplink(team_id, channel_id, tenant_id)

    with sync_playwright() as p:
        # InPrivate window each run, optionally primed with saved auth state
        browser = p.chromium.launch(channel="msedge", headless=False, args=["--inprivate"])
        ctx = browser.new_context(storage_state=None if save_state else (STATE_FILE if Path(STATE_FILE).exists() else None))
        page = ctx.new_page()

        print(f"Navigating to: {url}")
        page.goto(url, wait_until="domcontentloaded")

        try:
            compose = wait_for_compose(page, max_wait_ms=300_000)  # up to 5 minutes for slow tenants
        except PWTimeout:
            print("Timed out waiting for the compose box. Complete SSO/MFA then run again.")
            # Keep the browser open for manual debugging
            print("Browser left open. Close it yourself when done.")
            return 2

        # Bind the mention
        compose = bind_mention(page, bot_name)

        # Type your message text but DO NOT send yet
        if text:
            compose.type(" " + text, delay=20)

        # Optionally save state (only on the special 'save' run right after you complete SSO)
        if save_state:
            ctx.storage_state(path=STATE_FILE)
            print(f"Saved session state to {STATE_FILE}")

        # Wait for your go-ahead to actually send
        input("\nReady when you are. Press Enter here in the CLI to SEND the message… ")

        # Send (single Enter)
        page.keyboard.press("Enter")
        print("Message sent. Browser will remain open for observation.")

        # Do NOT close the browser/context; leave running for manual inspection
        # (Comment the next two lines back in if you ever want auto-close)
        # ctx.close()
        # browser.close()
        return 0

if __name__ == "__main__":
    sys.exit(main())