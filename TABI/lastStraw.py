# file: verify_is_bot.py
# deps: pip install pyyaml msal requests
import msal, requests, yaml
from pathlib import Path

GRAPH_BETA = "https://graph.microsoft.com/beta"

cfg = yaml.safe_load(open("config.yaml","r",encoding="utf-8"))
tenant = cfg["tenant_id"]; client_id = cfg["client_id"]; team_id = cfg["team"]["id"]
cache_path = Path(cfg.get("auth",{}).get("cache_path",".msal_cache.bin"))
cache = msal.SerializableTokenCache()
if cache_path.exists():
    try: cache.deserialize(cache_path.read_text("utf-8"))
    except: pass
app = msal.PublicClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant}", token_cache=cache)
scopes = ["ChannelMessage.Read.All","Group.Read.All"]
acc = app.get_accounts()
res = app.acquire_token_silent(scopes, account=acc[0]) if acc else None
if not (res and "access_token" in res):
    flow = app.initiate_device_flow(scopes=scopes); print(flow["message"])
    res = app.acquire_token_by_device_flow(flow)
tok = res["access_token"]

r = requests.get(
    f"{GRAPH_BETA}/teams/{team_id}/installedApps?$expand=teamsAppDefinition($expand=bot)",
    headers={"Authorization": f"Bearer {tok}"}, timeout=30)
r.raise_for_status()
found = []
for item in r.json().get("value", []):
    tdef = item.get("teamsAppDefinition") or {}
    disp = tdef.get("displayName")
    bot  = tdef.get("bot") or {}
    if bot.get("botId"):
        found.append((disp, bot["botId"]))
print("Bots installed in this team:")
for name, bid in found:
    print(f"- {name} | botId: {bid}")
if not found:
    print("No bots detected via installedApps expansion.")