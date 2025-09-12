# file: harvest_bot_identity.py
# deps: pip install pyyaml msal requests
import re, sys, html, requests, msal, yaml
from pathlib import Path

GRAPH_V1 = "https://graph.microsoft.com/v1.0"
GRAPH_BETA = "https://graph.microsoft.com/beta"

def load_cfg(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def token(cfg):
    tenant = cfg["tenant_id"]; client_id = cfg["client_id"]
    scopes = cfg.get("auth", {}).get("scopes", [])
    # ensure read scopes are present
    need = ["ChannelMessage.Read.All", "Group.Read.All"]
    for s in need:
        if s not in scopes: scopes.append(s)

    cache_path = Path(cfg.get("auth", {}).get("cache_path", ".msal_cache.bin"))
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try: cache.deserialize(cache_path.read_text("utf-8"))
        except Exception: pass
    app = msal.PublicClientApplication(client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache)
    accs = app.get_accounts()
    res = app.acquire_token_silent(scopes, account=accs[0]) if accs else None
    if not (res and "access_token" in res):
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow failed: {flow}")
        print(flow["message"])
        res = app.acquire_token_by_device_flow(flow)
        if "access_token" not in res: raise RuntimeError(f"Auth failed: {res}")
        cache_path.write_text(cache.serialize(), "utf-8")
    return res["access_token"]

def gget(tok, url):
    r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status(); return r.json()

def list_channel_messages_all(tok, team_id, channel_id, max_items=200):
    if "%3a" in channel_id.lower() or "%40" in channel_id.lower():
        raise ValueError("channel_id looks URL-encoded; decode it (19:...@thread.tacv2).")
    url = f"{GRAPH_V1}/teams/{team_id}/channels/{channel_id}/messages?$top=50"
    out = []
    while url and len(out) < max_items:
        data = gget(tok, url)
        out.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return out[:max_items]

def list_replies_all(tok, team_id, channel_id, parent_id, max_items=200):
    url = f"{GRAPH_V1}/teams/{team_id}/channels/{channel_id}/messages/{parent_id}/replies?$top=50"
    out = []
    while url and len(out) < max_items:
        data = gget(tok, url)
        out.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return out[:max_items]

def harvest_from_mentions(messages, bot_display_name=None):
    """
    Look for mentions[].mentioned.application in messages where body contains <at>Bot Name</at>.
    Returns the first matching application block {id, displayName, ...}.
    """
    def at_matches(body_html, name):
        if not name: return True
        # cheap check: <at ...>Name</at> (HTML may escape entities)
        return re.search(rf"<at[^>]*>\s*{re.escape(name)}\s*</at>", body_html or "", flags=re.I) is not None

    for m in messages:
        body = (m.get("body") or {}).get("content", "")
        if bot_display_name and not at_matches(body, bot_display_name):
            continue
        for ment in m.get("mentions") or []:
            app = (ment.get("mentioned") or {}).get("application")
            if app and app.get("id"):
                # This is the precise identity the UI used when the mention was typed
                return app
    return None

def beta_installed_apps_bot(tok, team_id, want_name=None):
    try:
        data = gget(tok, f"{GRAPH_BETA}/teams/{team_id}/installedApps?$expand=teamsAppDefinition($expand=bot)")
    except requests.HTTPError:
        return None
    for item in data.get("value", []):
        tdef = item.get("teamsAppDefinition") or {}
        disp = tdef.get("displayName")
        bot  = tdef.get("bot") or {}
        bot_id = bot.get("botId")
        if not bot_id: 
            continue
        if want_name and disp and disp.strip().lower() == want_name.strip().lower():
            return {"id": bot_id, "displayName": disp}
    return None

def main():
    cfg = load_cfg()
    team_id = cfg["team"]["id"]; channel_id = cfg["channel"]["id"]
    bot_name = (cfg.get("bot") or {}).get("name")

    tok = token(cfg)

    print("Scanning recent channel messages and replies for a real @mention…")
    parents = list_channel_messages_all(tok, team_id, channel_id, max_items=150)
    app = harvest_from_mentions(parents, bot_name)
    if not app:
        # also scan replies of recent threads
        for p in parents:
            pid = p.get("id")
            if not pid: continue
            replies = list_replies_all(tok, team_id, channel_id, pid, max_items=150)
            app = harvest_from_mentions(replies, bot_name)
            if app: break

    if app:
        print("\n✅ Found a mention identity used by the UI:")
        print(f"- displayName: {app.get('displayName','')}")
        print(f"- id (bot Microsoft App ID): {app.get('id')}")
        idtype = app.get("applicationIdentityType")
        if idtype: print(f"- applicationIdentityType: {idtype}")
        print("\nPaste into config.yaml under bot:")
        print(f"  id: \"{app.get('id')}\"")
        print(f"  name: \"{app.get('displayName', bot_name or '')}\"")
        print("Then send with mentions[].mentioned.application = { id, displayName } (no applicationIdentityType on v1.0).")
        return

    print("\nNo mention identity found in recent history. Trying beta installedApps expansion…")
    app2 = beta_installed_apps_bot(tok, team_id, want_name=bot_name)
    if app2:
        print("\n🔎 Discovered bot from installedApps (beta):")
        print(f"- displayName: {app2['displayName']}")
        print(f"- id (bot Microsoft App ID): {app2['id']}")
        print("\nPaste into config.yaml under bot:")
        print(f"  id: \"{app2['id']}\"")
        print(f"  name: \"{app2['displayName']}\"")
        return

    print("\nCould not derive a bot identity. Either the app isn’t a bot (connector/extension only),")
    print("or it has not been mentioned here yet with a resolvable identity. Trigger it once in the UI and rerun.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)