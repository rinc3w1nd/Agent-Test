# pip install playwright pyyaml
# python -m playwright install

import sys, re, yaml
from pathlib import Path
from playwright.sync_api import sync_playwright

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

def find_compose_now(page):
    """
    Zero 'smart waits': try to grab the compose textbox immediately.
    If it's not there yet, this will raise; trigger the step only when UI is ready.
    """
    # Primary: ARIA role name "Type a message"
    box = page.get_by_role("textbox", name=re.compile("Type a message", re.I))
    # Force a quick resolve (uses default timeout). If you want *no* waiting at all:
    # page.set_default_timeout(0)
    return box

def main():
    # Usage:
    #   First run (to capture auth after completing SSO):  python ui_send_mention_two_step.py save
    #   Subsequent runs (reuse saved auth):                python ui_send_mention_two_step.py
    save_state = len(sys.argv) > 1 and sys.argv[1].lower().startswith("save")

    cfg = load_cfg()
    team_id    = cfg["team"]["id"]             # GUID (groupId)
    channel_id = cfg["channel"]["id"]          # decoded: 19:...@thread.tacv2
    tenant_id  = cfg.get("tenant_id")          # optional but helpful
    bot_name   = cfg["bot"]["name"]
    text       = cfg["message"]["text"]

    url = channel_deeplink(team_id, channel_id, tenant_id)

    with sync_playwright() as p:
        # InPrivate window each run. If we already have state, preload it.
        args = ["--inprivate"]
        browser = p.chromium.launch(channel="msedge", headless=False, args=args)
        storage = STATE_FILE if (not save_state and Path(STATE_FILE).exists()) else None
        ctx = browser.new_context(storage_state=storage)
        page = ctx.new_page()

        print(f"\nOpening Teams channel:\n  {url}\n")
        page.goto(url, wait_until="domcontentloaded")

        print("When the channel is fully loaded and the compose box is visible:")
        input("Press Enter to BIND the @mention and type the message… ")

        # STEP 1: bind @mention and type message (no implicit waiting here)
        compose = find_compose_now(page)
        compose.click()
        compose.type(f"@{bot_name}")

        # Bind the top suggestion without waiting for listbox
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")

        # Type the rest of the message
        if text:
            compose.type(" " + text)

        # Optional: save fresh auth state on explicit 'save' run
        if save_state:
            ctx.storage_state(path=STATE_FILE)
            print(f"Saved session state to {STATE_FILE}")

        input("Press Enter to SEND the message… ")

        # STEP 2: send
        page.keyboard.press("Enter")
        print("Message sent. Browser remains open. Close it manually when you’re done.")

        # Do not close the browser/context; leave for manual inspection
        return 0

if __name__ == "__main__":
    sys.exit(main())