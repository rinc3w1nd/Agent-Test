# edge_probe_keep_open.py
import asyncio, os, json
from pathlib import Path
from playwright.async_api import async_playwright

CFG = {
    "browser_channel": "msedge",
    "headless": False,
    # EITHER: root + profile name
    "edge_user_data_dir": "/Users/<you>/Library/Application Support/Microsoft Edge",
    "edge_profile_directory": "Profile 6",
    # OR: exact profile folder (then omit edge_profile_directory)
    # "edge_user_data_dir": "/Users/<you>/Library/Application Support/Microsoft Edge/Profile 6",
    # "edge_profile_directory": "",
}

def expand(p): return os.path.expandvars(os.path.expanduser(p or ""))

def resolve_profile(cfg):
    ud = Path(expand(cfg.get("edge_user_data_dir","")))
    prof = (cfg.get("edge_profile_directory","") or "").strip()
    tail = ud.name.lower()
    is_profile = (tail == "default") or tail.startswith("profile ")
    if prof:
        if is_profile and tail != prof.lower():
            ud = ud.parent / prof
        elif not is_profile:
            ud = ud / prof
    return str(ud)

async def main():
    eff = resolve_profile(CFG)
    print("[DEBUG] Effective user_data_dir:", eff, "exists:", os.path.isdir(eff))
    try:
        with open(os.path.join(eff,"Preferences"), "r", encoding="utf-8") as pf:
            prefs = json.load(pf)
        print("[DEBUG] Preferences.profile.name:", (prefs.get("profile") or {}).get("name"))
    except Exception as e:
        print("[DEBUG] Could not read Preferences:", e)

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=eff,
            channel=CFG["browser_channel"],
            headless=CFG["headless"]
        )
        page = await ctx.new_page()
        await page.goto("edge://version")
        print("\n[READY] Edge launched. Press Enter to close…")
        try:
            input()
        except EOFError:
            # Fallback: hang for a while if stdin not attached
            await asyncio.sleep(300)
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(main())