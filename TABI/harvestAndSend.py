# file: send_using_harvested_mention.py
# deps: pip install pyyaml msal requests
import sys, html, requests, msal, yaml
from pathlib import Path

Gv1 = "https://graph.microsoft.com/v1.0"

def load_cfg():
    with open("config.yaml","r",encoding="utf-8") as f:
        return yaml.safe_load(f)

def token(cfg, extra_scopes=None):
    tenant=cfg["tenant_id"]; client_id=cfg["client_id"]
    scopes = cfg.get("auth", {}).get("scopes", [])
    needed = ["ChannelMessage.Read.All", "ChannelMessage.Send", "Group.Read.All"]
    for s in needed:
        if s not in scopes: scopes.append(s)
    if extra_scopes:
        for s in extra_scopes:
            if s not in scopes: scopes.append(s)

    cache_path = Path(cfg.get("auth",{}).get("cache_path",".msal_cache.bin"))
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try: cache.deserialize(cache_path.read_text("utf-8"))
        except Exception: pass
    app = msal.PublicClientApplication(client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache)
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

def _assert_channel_id_decoded(cid:str):
    low = cid.lower()
    if "%3a" in low or "%40" in low:
        raise ValueError("channel.id looks URL-encoded. Decode it: 19:...@thread.tacv2")

def page(url, headers, limit=200):
    out=[]; top=0
    while url and top<limit:
        r=requests.get(url,headers=headers,timeout=30); r.raise_for_status()
        data=r.json(); vals=data.get("value",[])
        out.extend(vals); top+=len(vals)
        url=data.get("@odata.nextLink")
    return out[:limit]

def find_latest_ui_mention(tok, team_id, channel_id, bot_display_name=None):
    hdr={"Authorization":f"Bearer {tok}"}
    msgs = page(f"{Gv1}/teams/{team_id}/channels/{channel_id}/messages?$top=50", hdr, limit=200)
    # scan top-level then replies
    def harvest(m):
        for ment in m.get("mentions") or []:
            app=(ment.get("mentioned") or {}).get("application")
            if not app or not app.get("id"): 
                continue
            # if a bot name is provided, prefer exact match
            if bot_display_name:
                name=(app.get("displayName") or "").strip().lower()
                if name == bot_display_name.strip().lower():
                    return app
            # otherwise return the first application mention
            return app
        return None
    for m in msgs:
        hit = harvest(m)
        if hit: return hit
    # replies
    for m in msgs:
        pid = m.get("id"); 
        if not pid: continue
        reps = page(f"{Gv1}/teams/{team_id}/channels/{channel_id}/messages/{pid}/replies?$top=50", hdr, limit=200)
        for r in reps:
            hit = harvest(r); 
            if hit: return hit
    return None

def send_with_mention(tok, team_id, channel_id, app_obj, text, mention_text=None):
    # app_obj is exactly what we harvested: {"id": "...", "displayName": "...", maybe "applicationIdentityType": "bot"}
    name = mention_text or app_obj.get("displayName") or "Bot"
    payload = {
        "body": {
            "contentType": "html",
            "content": f'<at id="0">{html.escape(name)}</at> {html.escape(text)}'
        },
        "mentions": [{
            "id": 0,
            "mentionText": name,
            "mentioned": { "application": {
                "id": app_obj["id"],
                "displayName": app_obj.get("displayName", name)
                # NOTE: do NOT include applicationIdentityType on v1.0
            }}
        }]
    }
    url=f"{Gv1}/teams/{team_id}/channels/{channel_id}/messages"
    r=requests.post(url, headers={"Authorization":f"Bearer {tok}","Content-Type":"application/json"},
                    json=payload, timeout=30)
    if r.status_code>=400:
        print("DEBUG payload:", payload)
        print("DEBUG response:", r.status_code, r.text)
        raise RuntimeError(f"Send failed {r.status_code}")
    return r.json()

def main():
    cfg=load_cfg()
    TEAM=cfg["team"]["id"]; CH=cfg["channel"]["id"]; TXT=cfg["message"]["text"]
    BOTNAME=(cfg.get("bot") or {}).get("name")
    _assert_channel_id_decoded(CH)
    tok = token(cfg)

    print("Looking for the latest UI-generated @mention…")
    app_obj = find_latest_ui_mention(tok, TEAM, CH, bot_display_name=BOTNAME)
    if not app_obj:
        print("Couldn’t find any application mentions in recent history. Trigger the bot once in the UI and retry.")
        sys.exit(2)

    print(f"Using harvested identity: name='{app_obj.get('displayName','')}', id={app_obj['id']}")
    res = send_with_mention(tok, TEAM, CH, app_obj, TXT, mention_text=BOTNAME)
    print("Sent.", "messageId=", res.get("id"))

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e); sys.exit(1)