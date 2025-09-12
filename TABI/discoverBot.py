# deps: pip install pyyaml msal requests
import os, sys, json
from pathlib import Path
import requests, msal, yaml

GRAPH_V1 = "https://graph.microsoft.com/v1.0"
GRAPH_BETA = "https://graph.microsoft.com/beta"

def load_cfg(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def token(cfg, cache_path=None, scopes=None):
    tenant = cfg["tenant_id"]; client_id = cfg["client_id"]
    scopes = scopes or cfg.get("auth", {}).get("scopes", ["ChannelMessage.Read.All", "Group.Read.All"])
    if "ChannelMessage.Read.All" not in scopes: scopes = scopes + ["ChannelMessage.Read.All"]
    cache_path = Path(cache_path or cfg.get("auth", {}).get("cache_path", ".msal_cache.bin"))
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try: cache.deserialize(cache_path.read_text("utf-8"))
        except Exception: pass
    app = msal.PublicClientApplication(client_id,
            authority=f"https://login.microsoftonline.com/{tenant}",
            token_cache=cache)
    accs = app.get_accounts()
    if accs:
        res = app.acquire_token_silent(list(dict.fromkeys(scopes)), account=accs[0])
        if res and "access_token" in res:
            if cache.has_state_changed: cache_path.write_text(cache.serialize(), "utf-8")
            return res["access_token"]
    flow = app.initiate_device_flow(scopes=list(dict.fromkeys(scopes)))
    if "user_code" not in flow: raise RuntimeError(f"Device flow failed: {flow}")
    print(flow["message"])
    res = app.acquire_token_by_device_flow(flow)
    if "access_token" not in res: raise RuntimeError(f"Auth failed: {res}")
    if cache.has_state_changed: cache_path.write_text(cache.serialize(), "utf-8")
    return res["access_token"]

def gget(tok, url):
    r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status(); return r.json()

def list_msgs(tok, team_id, channel_id, top=100):
    return gget(tok, f"{GRAPH_V1}/teams/{team_id}/channels/{channel_id}/messages?$top={top}").get("value", [])

def list_replies(tok, team_id, channel_id, parent_id, top=50):
    return gget(tok, f"{GRAPH_V1}/teams/{team_id}/channels/{channel_id}/messages/{parent_id}/replies?$top={top}").get("value", [])

def scan_mentions(messages):
    hits = []
    for m in messages:
        for ment in m.get("mentions", []) or []:
            app = (ment.get("mentioned") or {}).get("application")
            if app and app.get("id"):
                hits.append({"mention": ment, "inMessageId": m.get("id")})
    return hits

def try_beta_installed_apps(tok, team_id, want_name=None, want_teams_app_id=None):
    # Requires no extra scopes; beta endpoint uses same token.
    url = f"{GRAPH_BETA}/teams/{team_id}/installedApps?$expand=teamsAppDefinition($expand=bot)"
    try:
        data = gget(tok, url)
    except requests.HTTPError as e:
        return None
    for item in data.get("value", []):
        tdef = item.get("teamsAppDefinition") or {}
        disp = tdef.get("displayName")
        tid  = tdef.get("teamsAppId")
        bot  = tdef.get("bot") or {}
        bot_id = bot.get("botId")
        if not bot_id: 
            continue
        if want_teams_app_id and tid == want_teams_app_id:
            return {"id": bot_id, "displayName": disp, "applicationIdentityType": "bot"}
        if want_name and disp and disp.strip().lower() == want_name.strip().lower():
            return {"id": bot_id, "displayName": disp, "applicationIdentityType": "bot"}
    return None

def main():
    cfg = load_cfg()
    team_id = cfg["team"]["id"]; channel_id = cfg["channel"]["id"]
    want_name = (cfg.get("bot") or {}).get("name")
    want_pkg  = (cfg.get("bot") or {}).get("teams_app_id")  # optional, if you have it
    tok = token(cfg)

    # Pass 1: scrape mentions from recent messages + replies
    parents = list_msgs(tok, team_id, channel_id, top=100)
    cand = scan_mentions(parents)
    for p in parents:
        pid = p.get("id")
        if not pid: continue
        try:
            cand += scan_mentions(list_replies(tok, team_id, channel_id, pid, top=50))
        except Exception:
            pass

    # Deduplicate by app id
    seen = {}
    for hit in cand:
        app = hit["mention"]["mentioned"]["application"]
        seen[app["id"]] = app

    if seen:
        print("Found application mentions in this channel:\n")
        for aid, app in seen.items():
            mark = ""
            if want_name and app.get("displayName") and app["displayName"].strip().lower() == want_name.strip().lower():
                mark = "  <= matches bot.name"
            print(f"- {app.get('displayName','<no name>')} | appId: {aid} | type: {app.get('applicationIdentityType','?')}{mark}")
        # Prefer exact displayName match if provided
        if want_name:
            match = next((app for app in seen.values()
                          if app.get("displayName","").strip().lower() == want_name.strip().lower()), None)
            if match:
                print("\nPaste into config.yaml under bot:")
                print(f"  id: \"{match['id']}\"")
                print(f"  name: \"{match.get('displayName', want_name)}\"")
                print(f"  identity_type: \"{match.get('applicationIdentityType','bot')}\"")
                return

    # Pass 2: beta fallback via installedApps expansion
    print("\nNo suitable mention found in history; trying beta installedApps expansion…")
    app_ident = try_beta_installed_apps(tok, team_id, want_name=want_name, want_teams_app_id=want_pkg)
    if app_ident:
        print("Discovered bot identity from installedApps (beta):\n")
        print(f"- {app_ident['displayName']} | appId: {app_ident['id']} | type: {app_ident['applicationIdentityType']}")
        print("\nPaste into config.yaml under bot:")
        print(f"  id: \"{app_ident['id']}\"")
        print(f"  name: \"{app_ident['displayName']}\"")
        print(f"  identity_type: \"{app_ident['applicationIdentityType']}\"")
        return

    print("Couldn’t derive a bot identity. Either the app hasn’t posted/been mentioned here, or it isn’t a bot (connector/extension only).")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)