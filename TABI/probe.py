# deps: pip install msal requests pyyaml
import sys, argparse, requests, msal, yaml
from pathlib import Path

G_V1   = "https://graph.microsoft.com/v1.0"
G_BETA = "https://graph.microsoft.com/beta"

def load_cfg(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def acquire_token(cfg, extra_scopes=None):
    tenant = cfg["tenant_id"]; client_id = cfg["client_id"]
    scopes = set(cfg.get("auth", {}).get("scopes", []))
    # We need read scopes; add if missing.
    scopes.update(["Group.Read.All"])  # for installedApps in a team
    if extra_scopes:
        scopes.update(extra_scopes)
    cache_path = Path(cfg.get("auth", {}).get("cache_path", ".msal_cache.bin"))
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try: cache.deserialize(cache_path.read_text("utf-8"))
        except Exception: pass
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache
    )
    accounts = app.get_accounts()
    res = app.acquire_token_silent(list(scopes), account=accounts[0]) if accounts else None
    if not (res and "access_token" in res):
        flow = app.initiate_device_flow(scopes=list(scopes))
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow failed: {flow}")
        print(flow["message"])
        res = app.acquire_token_by_device_flow(flow)
        if "access_token" not in res:
            raise RuntimeError(f"Auth failed: {res}")
        cache_path.write_text(cache.serialize(), "utf-8")
    return res["access_token"]

def gget(tok, url):
    r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status()
    return r.json()

def probe_catalog(tok, name_eq=None):
    """
    Returns list of dicts: {displayName, teamsAppId, botId, scopes} from appCatalogs (beta).
    Requires AppCatalog.Read.All (delegated, admin-consented).
    """
    items = []
    if name_eq:
        url = f"{G_BETA}/appCatalogs/teamsApps?$filter=displayName eq '{name_eq}'&$expand=appDefinitions($expand=bot)"
    else:
        url = f"{G_BETA}/appCatalogs/teamsApps?$top=50&$expand=appDefinitions($expand=bot)"
    try:
        data = gget(tok, url)
    except requests.HTTPError as e:
        return {"error": f"Catalog probe failed: {e.response.status_code} {e.response.text}"}
    for app in data.get("value", []):
        for d in app.get("appDefinitions", []):
            tdef = d or {}
            disp = tdef.get("displayName") or app.get("displayName")
            teams_app_id = tdef.get("teamsAppId") or app.get("id")
            bot = tdef.get("bot") or {}
            bot_id = bot.get("botId")
            scopes = bot.get("scopes") or []
            items.append({
                "displayName": disp,
                "teamsAppId": teams_app_id,
                "botId": bot_id,
                "scopes": scopes
            })
    return {"items": items}

def probe_installed_in_team(tok, team_id, name_like=None):
    """
    Returns list of dicts from a specific team: {displayName, teamsAppId, botId, scopes}.
    """
    url = f"{G_BETA}/teams/{team_id}/installedApps?$expand=teamsAppDefinition($expand=bot)"
    try:
        data = gget(tok, url)
    except requests.HTTPError as e:
        return {"error": f"Team installedApps probe failed: {e.response.status_code} {e.response.text}"}
    items = []
    for it in data.get("value", []):
        tdef = (it.get("teamsAppDefinition") or {})
        disp = tdef.get("displayName")
        if name_like and disp and name_like.lower() not in disp.lower():
            continue
        bot = tdef.get("bot") or {}
        items.append({
            "displayName": disp,
            "teamsAppId": tdef.get("teamsAppId"),
            "botId": bot.get("botId"),
            "scopes": bot.get("scopes") or []
        })
    return {"items": items}

def verdict_line(rec):
    # Determine what we can/can't do in channels
    if not rec.get("botId"):
        return "NO BOT: cannot @mention via Graph; use webhook/vendor API."
    scopes = [s.lower() for s in rec.get("scopes") or []]
    if "team" in scopes:
        return "BOT with team scope: @mention via Graph should be supported."
    elif "groupchat" in scopes or "personal" in scopes:
        return "BOT without team scope: may work in chats (not channels)."
    return "BOT present, unknown scopes."

def main():
    ap = argparse.ArgumentParser(description="Probe Teams app for bot capability & scope.")
    ap.add_argument("--name", default="", help="App display name to search (catalog and team).")
    ap.add_argument("--team", default=None, help="Team (groupId) to inspect; defaults to config.team.id")
    ap.add_argument("--no-catalog", action="store_true", help="Skip catalog probe (if AppCatalog.Read.All not consented).")
    args = ap.parse_args()

    cfg = load_cfg()
    team_id = args.team or cfg.get("team", {}).get("id")
    if not team_id:
        print("Missing team id. Provide --team or add team.id in config.yaml.")
        sys.exit(1)

    # Acquire token; include AppCatalog.Read.All for catalog probe unless skipped
    extra_scopes = None if not (not args.no_catalog) else []
    if not args.no_catalog:
        extra_scopes = ["AppCatalog.Read.All"]
    tok = acquire_token(cfg, extra_scopes=extra_scopes)

    print(f"\n== Installed in Team probe (teamId: {team_id}) ==")
    t = probe_installed_in_team(tok, team_id, name_like=args.name)
    if "error" in t:
        print(t["error"])
    else:
        if not t["items"]:
            print("No matching apps found in this team.")
        else:
            for rec in t["items"]:
                print(f"- {rec['displayName']} | teamsAppId: {rec['teamsAppId']}")
                print(f"  botId: {rec.get('botId')}")
                print(f"  scopes: {rec.get('scopes')}")
                print(f"  verdict: {verdict_line(rec)}")

    if not args.no_catalog:
        print(f"\n== Catalog probe (name == '{args.name}') ==")
        c = probe_catalog(tok, name_eq=args.name)
        if "error" in c:
            print(c["error"])
        else:
            if not c["items"]:
                print("No catalog entries matched that name.")
            else:
                for rec in c["items"]:
                    print(f"- {rec['displayName']} | teamsAppId: {rec['teamsAppId']}")
                    print(f"  botId: {rec.get('botId')}")
                    print(f"  scopes: {rec.get('scopes')}")
                    print(f"  verdict: {verdict_line(rec)}")

    print("\nTip: If you see a botId AND 'team' in scopes, use that botId in your Graph mention payload "
          "(`mentioned.application.id`) with v1.0. If no botId or no 'team' scope, you won't be able to drive it via channel mentions.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)