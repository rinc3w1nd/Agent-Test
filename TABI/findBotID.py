# file: find_bot_id.py
# deps: pip install pyyaml msal requests
import os, sys, time, html
import yaml, requests, msal
from pathlib import Path

GRAPH = "https://graph.microsoft.com/v1.0"

def load_cfg(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def ensure_cache(cache_path: Path):
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try:
            cache.deserialize(cache_path.read_text("utf-8"))
        except Exception:
            pass
    return cache

def save_cache(cache, cache_path: Path):
    if cache.has_state_changed:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            try: cache_path.touch(mode=0o600, exist_ok=True)
            except Exception: cache_path.touch(exist_ok=True)
        cache_path.write_text(cache.serialize(), "utf-8")

def acquire_token(cfg, cache):
    tenant = cfg["tenant_id"]
    client_id = cfg["client_id"]
    scopes = cfg.get("auth", {}).get("scopes", [
        "ChannelMessage.Read.All",  # must include read
        "Group.Read.All"
    ])
    # Ensure read scope is present
    if "ChannelMessage.Read.All" not in scopes:
        scopes = scopes + ["ChannelMessage.Read.All"]

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache
    )
    # Try silent first
    accts = app.get_accounts()
    if accts:
        res = app.acquire_token_silent(scopes, account=accts[0])
        if res and "access_token" in res:
            return res["access_token"]

    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow failed: {flow}")
    print(flow["message"])
    res = app.acquire_token_by_device_flow(flow)
    if "access_token" not in res:
        raise RuntimeError(f"Auth failed: {res}")
    return res["access_token"]

def gget(tok, url):
    r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status()
    return r.json()

def list_channel_messages(tok, team_id, channel_id, top=50):
    return gget(tok, f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages?$top={top}").get("value", [])

def list_replies(tok, team_id, channel_id, parent_id, top=50):
    return gget(tok, f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{parent_id}/replies?$top={top}").get("value", [])

def collect_bot_ids_from_messages(messages):
    """Return dict: botAppId -> displayName (for any application senders with bot identity)."""
    out = {}
    for m in messages:
        app = (m.get("from") or {}).get("application") or {}
        # Some records include applicationIdentityType == "bot"; if missing, still accept.
        if app.get("id"):
            out[app["id"]] = app.get("displayName") or out.get(app["id"]) or "Unknown App"
    return out

def main():
    cfg = load_cfg()
    team_id    = cfg["team"]["id"]
    channel_id = cfg["channel"]["id"]
    bot_name   = (cfg.get("bot") or {}).get("name")

    cache_path = Path(cfg.get("auth", {}).get("cache_path", ".msal_cache.bin"))
    cache = ensure_cache(cache_path)
    try:
        tok = acquire_token(cfg, cache)
    finally:
        save_cache(cache, cache_path)

    # Look at recent parents first
    parents = list_channel_messages(tok, team_id, channel_id, top=50)
    bot_map = collect_bot_ids_from_messages(parents)

    # Also scan replies of each recent parent (common for bots)
    for p in parents:
        pid = p.get("id")
        if not pid:
            continue
        try:
            replies = list_replies(tok, team_id, channel_id, pid, top=50)
            bot_map.update(collect_bot_ids_from_messages(replies))
        except requests.HTTPError as e:
            # Some threads may 404 if permissions are odd; ignore and continue
            continue

    if not bot_map:
        print("No bot/application senders found in recent messages for this channel.")
        print("If the bot has never posted here, trigger it once in Teams and rerun.")
        sys.exit(2)

    # Print results
    print("\nDiscovered application senders (possible bots) in this channel:")
    for app_id, name in bot_map.items():
        mark = ""
        if bot_name and name and name.strip().lower() == bot_name.strip().lower():
            mark = "  <= matches bot.name from config.yaml"
        print(f"- {name}  |  appId: {app_id}{mark}")

    # If config has a bot.name and we found a matching one, print a clean line to paste back
    if bot_name:
        match = next((app_id for app_id, name in bot_map.items()
                      if name and name.strip().lower() == bot_name.strip().lower()), None)
        if match:
            print("\nSuggested config.yaml update:")
            print(f"bot:\n  id: \"{match}\"\n  name: \"{bot_name}\"")
        else:
            print("\nNo exact displayName match for bot.name; pick the correct appId from the list above and set config `bot.id`.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)