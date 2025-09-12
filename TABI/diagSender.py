# file: diagnose_sender_types.py
# deps: pip install pyyaml msal requests
import requests, msal, yaml, sys
from pathlib import Path

GRAPH = "https://graph.microsoft.com/v1.0"

def load_cfg():
    with open("config.yaml","r",encoding="utf-8") as f:
        return yaml.safe_load(f)

def token(cfg, scopes=None):
    tenant=cfg["tenant_id"]; client_id=cfg["client_id"]
    scopes = scopes or ["ChannelMessage.Read.All","Group.Read.All"]
    cache_path = Path(cfg.get("auth",{}).get("cache_path",".msal_cache.bin"))
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try: cache.deserialize(cache_path.read_text("utf-8"))
        except Exception: pass
    app = msal.PublicClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant}", token_cache=cache)
    acc = app.get_accounts()
    res = app.acquire_token_silent(scopes, account=acc[0]) if acc else None
    if not (res and "access_token" in res):
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow: raise RuntimeError(flow)
        print(flow["message"])
        res = app.acquire_token_by_device_flow(flow)
        if "access_token" not in res: raise RuntimeError(res)
        cache_path.write_text(cache.serialize(), "utf-8")
    return res["access_token"]

def fetch_messages(tok, team_id, channel_id, max_items=200):
    if "%3a" in channel_id.lower() or "%40" in channel_id.lower():
        raise ValueError("channel_id is URL-encoded; decode it (19:...@thread.tacv2).")
    headers={"Authorization": f"Bearer {tok}"}
    url=f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages?$top=50"
    out=[]
    while url and len(out)<max_items:
        r=requests.get(url,headers=headers,timeout=30); r.raise_for_status()
        data=r.json(); out.extend(data.get("value",[])); url=data.get("@odata.nextLink")
    return out[:max_items]

def fetch_replies(tok, team_id, channel_id, parent_id, max_items=200):
    headers={"Authorization": f"Bearer {tok}"}
    url=f"{GRAPH}/teams/{team_id}/channels/{channel_id}/messages/{parent_id}/replies?$top=50"
    out=[]
    while url and len(out)<max_items:
        r=requests.get(url,headers=headers,timeout=30); r.raise_for_status()
        data=r.json(); out.extend(data.get("value",[])); url=data.get("@odata.nextLink")
    return out[:max_items]

def collect_app_senders(msgs):
    """Return map appId -> {'name':displayName,'type':applicationIdentityType or '?','count':n}"""
    m={}
    for msg in msgs:
        app=(msg.get("from") or {}).get("application") or {}
        if app.get("id"):
            entry=m.setdefault(app["id"],{"name":app.get("displayName","<no name>"),
                                          "type":app.get("applicationIdentityType","?"),
                                          "count":0})
            entry["count"]+=1
    return m

def main():
    cfg=load_cfg()
    tok=token(cfg)
    team_id=cfg["team"]["id"]; channel_id=cfg["channel"]["id"]
    parents=fetch_messages(tok,team_id,channel_id,200)

    senders=collect_app_senders(parents)
    for p in parents:
        rid=p.get("id")
        if not rid: continue
        try:
            senders.update({**senders, **collect_app_senders(fetch_replies(tok,team_id,channel_id,rid,100))})
        except Exception:
            pass

    if not senders:
        print("No app-based senders found. The app may not have posted here yet.")
        sys.exit(2)

    print("Application senders observed in this channel (from.application.*):\n")
    for app_id,info in senders.items():
        mark = " (BOT)" if info["type"].lower()=="bot" else ""
        print(f"- name: {info['name']} | appId: {app_id} | type: {info['type']} | msgs: {info['count']}{mark}")

    # Guidance
    any_bot = any(info["type"].lower()=="bot" for info in senders.values())
    if any_bot:
        print("\n✅ At least one sender is a real BOT identity. Use THAT appId in mentions (no 'applicationIdentityType' field on v1.0).")
    else:
        print("\n❌ None of the application senders are typed as 'bot'. This app is likely a connector or message extension only.")
        print("You cannot trigger it via Graph mentions. Use its webhook/external API or a UI action instead.")

if __name__=="__main__":
    main()